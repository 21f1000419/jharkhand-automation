from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from contextlib import suppress
from pathlib import Path
from typing import Any

from automation.browser import BrowserSession, PortalBrowserSession
from core.activity_log import DailyActivityLog
from core.config import AppConfig
from core.controls import RunControls
from core.models import RunOptions, UiEvent
from core.workflow import WorkflowEngine
from services.gemini_ocr import GeminiCaptchaSolver


class AutomationController:
    """Bridges Tkinter to one long-lived asyncio/Playwright worker thread."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.activity_log = DailyActivityLog()
        self.events: queue.Queue[UiEvent] = queue.Queue()
        self.controls = RunControls(self.emit)
        self.loop: asyncio.AbstractEventLoop | None = None
        # Gemini always stays in the dedicated signed-in Chrome profile.  The
        # portal is deliberately launched separately with a fresh session.
        self.gemini_browser: BrowserSession | None = None
        self.portal_browser: PortalBrowserSession | None = None
        self.solver: GeminiCaptchaSolver | None = None
        self.run_task: asyncio.Task[None] | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, name="automation-worker", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def emit(self, event: UiEvent) -> None:
        self.activity_log.write(
            event.kind,
            event.message,
            level=str(event.data.get("level", "INFO")),
            data=event.data,
        )
        self.events.put(event)

    def record_activity(self, action: str, message: str = "") -> None:
        self.activity_log.write(action, message)

    def open_gemini_setup(self) -> None:
        self._submit(self._open_gemini_setup())

    def verify_gemini(self) -> None:
        self._submit(self._verify_gemini())

    def start(self, options: RunOptions) -> None:
        if self.loop is None:
            raise RuntimeError("Automation worker is unavailable.")
        self.controls.reset()
        self.loop.call_soon_threadsafe(self._start_run, options)

    def pause(self) -> None:
        self.controls.pause()
        self.emit(UiEvent("paused", "Automation paused."))

    def resume(self) -> None:
        self.controls.resume()
        self.emit(UiEvent("resumed", "Automation resumed."))

    def stop(self) -> None:
        self.controls.stop("Stopped by user")
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._cancel_run)

    def decide_error(self, action: str) -> None:
        self.controls.decide(action)

    def reconfigure_browser(self) -> None:
        self._submit(self._close_gemini_browser())

    def shutdown(self) -> None:
        self.controls.stop("Application closed")
        if self.loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self.loop)
        with suppress(Exception):
            future.result(timeout=12)
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=2)

    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.close()

    async def _ensure_gemini_services(self) -> tuple[BrowserSession, GeminiCaptchaSolver]:
        if self.gemini_browser is None:
            self.gemini_browser = BrowserSession(
                Path(self.config.chrome_executable),
                self.config.profile_path,
                self.config.debug_port,
                self._on_gemini_browser_disconnected,
            )
            self.solver = GeminiCaptchaSolver(self.gemini_browser)
        await self.gemini_browser.start()
        if self.solver is None:
            raise RuntimeError("Gemini service was not created.")
        return self.gemini_browser, self.solver

    async def _open_gemini_setup(self) -> None:
        try:
            _, solver = await self._ensure_gemini_services()
            await solver.open_setup()
            if await solver.verify_ready():
                self.emit(UiEvent("gemini_verified", "The browser profile is signed in and Gemini is ready."))
            else:
                self.emit(
                    UiEvent(
                        "gemini_setup_opened",
                        "Chrome is open at Gemini. Sign in to the Google account for this browser profile, "
                        "then check the profile.",
                    )
                )
        except Exception as error:
            self.emit(UiEvent("fatal_error", f"Could not open the browser profile: {error}"))

    async def _verify_gemini(self) -> None:
        try:
            _, solver = await self._ensure_gemini_services()
            if await solver.verify_ready():
                self.emit(UiEvent("gemini_verified", "The browser profile is signed in and Gemini is ready."))
            else:
                self.emit(
                    UiEvent(
                        "gemini_not_ready",
                        "A usable Gemini chat was not found in this browser profile. "
                        "Sign in to Google and try again.",
                    )
                )
        except Exception as error:
            self.emit(UiEvent("fatal_error", f"Browser profile verification failed: {error}"))

    def _start_run(self, options: RunOptions) -> None:
        if self.run_task is not None and not self.run_task.done():
            self.emit(UiEvent("fatal_error", "A batch is already running."))
            return
        self.run_task = asyncio.create_task(self._run(options))

    async def _run(self, options: RunOptions) -> None:
        try:
            _, solver = await self._ensure_gemini_services()
            if not await solver.verify_ready():
                self.emit(
                    UiEvent(
                        "gemini_setup_required",
                        "The browser profile is not ready. Sign in to Google and verify Gemini "
                        "before starting a batch.",
                    )
                )
                return
            self.emit(UiEvent("gemini_verified", "The browser profile and Gemini chat are ready."))
            self.emit(UiEvent("run_started", "Automation started."))
            self.portal_browser = PortalBrowserSession(
                options.portal_browser, self._on_portal_browser_disconnected
            )
            page = await self.portal_browser.new_portal_page()
            engine = WorkflowEngine(page, solver, self.controls, self.emit)
            await engine.run(options)
        except asyncio.CancelledError:
            self.emit(UiEvent("run_stopped", "Automation stopped."))
        except Exception as error:
            self.emit(UiEvent("fatal_error", f"Automation stopped: {error}"))
        finally:
            await self._close_portal_browser()
            self.run_task = None

    def _cancel_run(self) -> None:
        if self.run_task is not None and not self.run_task.done():
            self.run_task.cancel()

    def _on_gemini_browser_disconnected(self) -> None:
        self.emit(
            UiEvent(
                "browser_closed",
                "The signed-in Chrome profile was closed. The automation has stopped.",
                {"profile_browser": True},
            )
        )
        self.controls.stop("Signed-in Chrome profile was closed")
        self._cancel_run()
        self.gemini_browser = None
        self.solver = None

    def _on_portal_browser_disconnected(self) -> None:
        name = self.portal_browser.choice.name if self.portal_browser is not None else "Portal browser"
        self.emit(UiEvent("browser_closed", f"{name} was closed. The automation has stopped."))
        self.controls.stop(f"{name} was closed")
        self._cancel_run()

    async def _close_gemini_browser(self) -> None:
        if self.gemini_browser is not None:
            await self.gemini_browser.close()
        self.gemini_browser = None
        self.solver = None

    async def _close_portal_browser(self) -> None:
        browser, self.portal_browser = self.portal_browser, None
        if browser is not None:
            await browser.close()

    async def _shutdown_async(self) -> None:
        self.emit(UiEvent("browsers_closing", "Closing the portal browser and signed-in Chrome profile."))
        self._cancel_run()
        await asyncio.sleep(0)
        await self._close_portal_browser()
        await self._close_gemini_browser()

    def _submit(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        if self.loop is None:
            self.emit(UiEvent("fatal_error", "Automation worker is unavailable."))
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def report_failure(done: Future[Any]) -> None:
            try:
                done.result()
            except Exception as error:
                self.emit(UiEvent("fatal_error", str(error)))

        future.add_done_callback(report_failure)
