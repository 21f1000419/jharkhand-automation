from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from contextlib import suppress
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from automation.browser import BrowserSession, PortalBrowserSession, cleanup_all_spawned_processes
from automation.portal import CITIZEN_LOGIN_URL
from core.activity_log import DailyActivityLog
from core.config import AppConfig
from core.controls import RunControls
from core.models import OcrEngine, PortalBrowser, RunOptions, UiEvent
from core.resources import bundled_path
from core.workflow import WorkflowEngine
from services.captcha_ocr import CaptchaSolver
from services.gemini_web_ocr import GeminiWebCaptchaSolver


def _ocr_engine_label(engine: OcrEngine) -> str:
    return {
        OcrEngine.PADDLEOCR: "PaddleOCR v6 small (local)",
        OcrEngine.EASYOCR: "EasyOCR (local)",
        OcrEngine.GEMINI: "Gemini (browser)",
    }[engine]


class AutomationController:
    """Bridges Tkinter to one long-lived asyncio/Playwright worker thread."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.activity_log = DailyActivityLog()
        self.events: queue.Queue[UiEvent] = queue.Queue()
        self.controls = RunControls(self.emit)
        self.loop: asyncio.AbstractEventLoop | None = None
        # Gemini runs in the dedicated signed-in Chrome profile in non-headless mode.
        # The portal uses a separate, fresh session throughout.
        # (Headless mode code commented out for future reference).
        self.gemini_browser: BrowserSession | None = None
        self.portal_browser: PortalBrowserSession | None = None
        self.portal_page: Page | None = None
        self.solver: CaptchaSolver | None = None
        self.run_task: asyncio.Task[None] | None = None
        self.ocr_test_task: asyncio.Task[None] | None = None
        self._portal_browser_closed = False
        self._is_shut_down = False
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

    def verify_gemini(self) -> None:
        self._submit(self._verify_gemini())

    def test_gemini_ocr(self) -> None:
        if self.loop is None:
            self.emit(UiEvent("ocr_test_failed", "Automation worker is unavailable."))
            return
        self.loop.call_soon_threadsafe(self._start_gemini_ocr_test)

    # Setup browser sign-in handler commented out for non-headless only mode:
    # def open_gemini_login_browser(self) -> None:
    #     self._submit(self._open_gemini_login_browser())

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
            self._submit(self._stop_and_close_portal())

    def decide_error(self, action: str) -> None:
        self.controls.decide(action)

    def reconfigure_browser(self) -> None:
        self._submit(self._close_gemini_browser())

    def close_gemini_ocr(self) -> None:
        self._submit(self._close_gemini_ocr())

    def shutdown(self) -> None:
        if self._is_shut_down:
            return
        self._is_shut_down = True
        self.controls.stop("Application closed")
        if self.loop is not None and self.loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self.loop)
                with suppress(Exception):
                    future.result(timeout=15)
            except Exception:
                pass
            with suppress(Exception):
                self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        cleanup_all_spawned_processes()

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
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()

    async def _ensure_gemini_services(
        self, # *, headless: bool = False
    ) -> tuple[BrowserSession, GeminiWebCaptchaSolver]:
        # Headless mode browser restart logic commented out:
        # if self.gemini_browser is not None and self.gemini_browser.headless != headless:
        #     await self._close_gemini_browser()
        if self.gemini_browser is None:
            # debug_port = self.config.debug_port + 1 if headless else self.config.debug_port
            # if headless:
            #     self.emit(UiEvent("gemini_headless_starting", "Starting Gemini OCR in headless mode."))
            self.gemini_browser = BrowserSession(
                Path(self.config.chrome_executable),
                self.config.profile_path,
                self.config.debug_port,
                self._on_gemini_browser_disconnected,
                headless=False,
                # headless=headless,
            )
        if not isinstance(self.solver, GeminiWebCaptchaSolver):
            self.solver = GeminiWebCaptchaSolver(self.gemini_browser)
        await self.gemini_browser.start()
        if self.solver is None:
            raise RuntimeError("Gemini service was not created.")
        return self.gemini_browser, self.solver

    async def _ensure_ocr_solver(self, engine: OcrEngine) -> CaptchaSolver:
        solver: CaptchaSolver
        if engine == OcrEngine.PADDLEOCR:
            from services.paddleocr_ocr import PaddleOcrCaptchaSolver

            solver = PaddleOcrCaptchaSolver()
        elif engine == OcrEngine.EASYOCR:
            from services.gemini_ocr import EasyOcrCaptchaSolver

            solver = EasyOcrCaptchaSolver()
        else:
            _, solver = await self._ensure_gemini_services()
        if not await solver.verify_ready():
            raise RuntimeError(f"{_ocr_engine_label(engine)} is not ready.")
        self.solver = solver
        return solver

    async def _verify_gemini(self) -> None:
        try:
            engine = OcrEngine(self.config.ocr_engine)
            await self._ensure_ocr_solver(engine)
            self.emit(UiEvent("gemini_verified", f"{_ocr_engine_label(engine)} is ready."))
        except Exception as error:
            self.emit(UiEvent("gemini_not_ready", f"OCR verification failed: {error}"))

    def _start_gemini_ocr_test(self) -> None:
        if self.run_task is not None and not self.run_task.done():
            self.emit(UiEvent("ocr_test_failed", "Stop the active batch before running the OCR test."))
            return
        if self.ocr_test_task is not None and not self.ocr_test_task.done():
            self.emit(UiEvent("ocr_test_failed", "An OCR test is already running."))
            return
        self.ocr_test_task = asyncio.create_task(self._test_gemini_ocr())

    async def _test_gemini_ocr(self) -> None:
        try:
            engine = OcrEngine(self.config.ocr_engine)
            self.emit(UiEvent("ocr_test_started", f"Testing {_ocr_engine_label(engine)}..."))
            solver = await self._ensure_ocr_solver(engine)
            test_image = bundled_path("test-images/1.png")
            if not test_image.is_file():
                raise RuntimeError(f"OCR test image was not found: {test_image}")
            self.emit(UiEvent("ocr_test_progress", "Reading the bundled CAPTCHA test image..."))
            code = await solver.solve_image(test_image.read_bytes())
            self.emit(
                UiEvent(
                    "ocr_test_succeeded",
                    f"{_ocr_engine_label(engine)} read the test CAPTCHA as: {code}",
                    {"code": code},
                )
            )
        except Exception as error:
            self.emit(UiEvent("ocr_test_failed", f"CAPTCHA OCR test failed: {error}"))
        finally:
            self.ocr_test_task = None

    # Headless helper methods and sign-in page commented out for non-headless only mode:
    # async def _prepare_headless_gemini(self) -> GeminiCaptchaSolver | None:
    #     """Use headless Gemini unless the page explicitly requires a Google sign-in."""
    #     _, solver = await self._ensure_gemini_services(headless=True)
    #     if await solver.sign_in_required():
    #         await self._close_gemini_browser()
    #         self.emit(
    #             UiEvent(
    #                 "gemini_login_required",
    #                 "Gemini needs sign-in. Click Start OCR browser to open the dedicated Chrome profile.",
    #             )
    #         )
    #         return None
    #     if not await solver.verify_ready():
    #         self.emit(
    #             UiEvent(
    #                 "gemini_not_ready",
    #                 "Headless Gemini could not find a usable chat. Check your connection, then try "
    #                 "Start OCR browser again.",
    #             )
    #         )
    #         return None
    #     return solver

    # async def _open_gemini_login_browser(self) -> None:
    #     try:
    #         visible_browser, _ = await self._ensure_gemini_services(headless=False)
    #         login_page = await visible_browser.new_portal_page("about:blank")
    #         await login_page.set_content(
    #             """
    #             <style>
    #               body { font-family: Segoe UI, Arial, sans-serif; }
    #               main {
    #                 max-width: 720px; margin: 80px auto; padding: 28px;
    #                 border: 1px solid #d1d5db; border-radius: 12px; line-height: 1.5;
    #               }
    #               h1 { margin-top: 0; }
    #             </style>
    #             <main>
    #               <h1>Chrome profile sign-in required</h1>
    #               <p>Complete the required sign-in in this dedicated Chrome profile.</p>
    #               <p>After sign-in succeeds, close this Chrome window. Return to the application and click
    #                  <strong>Start OCR browser</strong> to reopen Gemini headlessly.</p>
    #             </main>
    #             """
    #         )
    #         self.emit(
    #             UiEvent(
    #                 "gemini_login_browser_opened",
    #                 "Chrome profile opened for sign-in. Close it after sign-in, then start OCR again.",
    #             )
    #         )
    #     except Exception as error:
    #         self.emit(UiEvent("fatal_error", f"Could not open the Chrome profile: {error}"))

    def _start_run(self, options: RunOptions) -> None:
        if self.run_task is not None and not self.run_task.done():
            self.emit(UiEvent("fatal_error", "A batch is already running."))
            return
        self.run_task = asyncio.create_task(self._run(options))

    async def _run(self, options: RunOptions) -> None:
        portal_watchdog: asyncio.Task[None] | None = None
        completed = False
        try:
            solver: CaptchaSolver | None = None
            if options.ocr_enabled:
                # solver = await self._prepare_headless_gemini()  # Headless mode
                # if solver is None:
                #     return
                solver = await self._ensure_ocr_solver(options.ocr_engine)
                self.emit(UiEvent("gemini_verified", f"{_ocr_engine_label(options.ocr_engine)} is ready."))
            else:
                self.emit(UiEvent("ocr_manual_mode", "OCR is inactive; CAPTCHAs require manual entry."))
            self.emit(UiEvent("run_started", "Automation started."))

            async def open_portal_page() -> Page:
                return await self._open_fresh_portal_page(options.portal_browser)

            async def close_portal_page() -> None:
                await self._close_portal_browser()

            page: Page | None = None
            if not options.fresh_browser_per_unit:
                if not self._can_reuse_portal(options.portal_browser):
                    page = await open_portal_page()
                else:
                    page = self.portal_page
                if page is None:
                    raise RuntimeError("The portal browser did not provide a page.")

            portal_watchdog = asyncio.create_task(self._monitor_portal_browser())
            engine = WorkflowEngine(
                page,
                solver,
                self.controls,
                self.emit,
                open_portal_page=open_portal_page,
                close_portal_page=close_portal_page,
            )
            completed = await engine.run(options)
        except asyncio.CancelledError:
            self.emit(UiEvent("run_stopped", "Automation stopped."))
        except Exception as error:
            self.emit(UiEvent("fatal_error", f"Automation stopped: {error}"))
        finally:
            if portal_watchdog is not None:
                portal_watchdog.cancel()
                await asyncio.gather(portal_watchdog, return_exceptions=True)
            if not completed:
                await self._close_portal_browser()
            self.run_task = None

    def _can_reuse_portal(self, choice: PortalBrowser) -> bool:
        return (
            self.portal_browser is not None
            and self.portal_page is not None
            and not self.portal_page.is_closed()
            and self.portal_browser.is_active
            and self.portal_browser.choice == choice
        )

    async def _open_fresh_portal_page(self, choice: PortalBrowser) -> Page:
        await self._close_portal_browser()
        self.portal_browser = PortalBrowserSession(
            choice, self._on_portal_browser_disconnected
        )
        self._portal_browser_closed = False
        self.portal_page = await self.portal_browser.new_portal_page(
            CITIZEN_LOGIN_URL, timeout_ms=0
        )
        return self.portal_page

    async def _monitor_portal_browser(self) -> None:
        """Keep checking browser health while the workflow is paused for user input."""
        while True:
            browser = self.portal_browser
            page = self.portal_page
            if not self._portal_browser_closed:
                if browser is None or page is None or page.is_closed() or not browser.is_active:
                    self._on_portal_browser_disconnected()
                    return
            await asyncio.sleep(1)

    def _cancel_run(self) -> None:
        if self.run_task is not None and not self.run_task.done():
            self.run_task.cancel()

    async def _stop_and_close_portal(self) -> None:
        self._cancel_run()
        await self._close_portal_browser()
        self.emit(UiEvent("portal_closed", "Portal browser closed."))

    async def _close_gemini_ocr(self) -> None:
        await self._close_gemini_browser()
        self.emit(UiEvent("gemini_stopped", "Gemini OCR browser stopped."))

    def _on_gemini_browser_disconnected(self) -> None:
        self.emit(
            UiEvent(
                "browser_closed",
                "The Gemini OCR browser session stopped. The automation has stopped.",
                {"profile_browser": True},
            )
        )
        self.controls.stop("Gemini OCR browser session stopped")
        self._cancel_run()
        self.gemini_browser = None
        self.solver = None

    def _on_portal_browser_disconnected(self) -> None:
        if self._portal_browser_closed or self.controls.stop_event.is_set():
            return
        self._portal_browser_closed = True
        name = self.portal_browser.choice.name if self.portal_browser is not None else "Portal browser"
        self.emit(UiEvent("browser_closed", f"{name} was closed. The automation has stopped."))
        self.controls.stop(f"{name} was closed")

    async def _close_gemini_browser(self) -> None:
        browser, self.gemini_browser = self.gemini_browser, None
        self.solver = None
        if browser is not None:
            with suppress(Exception):
                await browser.close()

    async def _close_portal_browser(self) -> None:
        self._portal_browser_closed = True
        browser, self.portal_browser = self.portal_browser, None
        self.portal_page = None
        if browser is not None:
            with suppress(Exception):
                await browser.close()

    async def _shutdown_async(self) -> None:
        self.emit(UiEvent("browsers_closing", "Closing the portal browser and signed-in Chrome profile."))
        self._cancel_run()
        task = self.run_task
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
        test_task = self.ocr_test_task
        if test_task is not None and test_task is not asyncio.current_task():
            test_task.cancel()
            await asyncio.gather(test_task, return_exceptions=True)
        await asyncio.gather(
            self._close_portal_browser(),
            self._close_gemini_browser(),
            return_exceptions=True,
        )

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
