from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

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

DEFAULT_TAB_ID = "default"


def _ocr_engine_label(engine: OcrEngine) -> str:
    return {
        OcrEngine.PADDLEOCR: "PaddleOCR v6 medium (local)",
        OcrEngine.EASYOCR: "EasyOCR (local)",
        OcrEngine.GEMINI: "Gemini (browser)",
    }[engine]


class PortalSessionFactory(Protocol):
    def __call__(
        self, choice: PortalBrowser, on_disconnect: Callable[[PortalBrowserSession], None]
    ) -> PortalBrowserSession: ...


@dataclass
class RunSession:
    """Resources belonging to one UI tab and no other tab."""

    tab_id: str
    run_id: str
    options: RunOptions
    controls: RunControls
    run_task: asyncio.Task[None] | None = None
    portal_browser: PortalBrowserSession | None = None
    portal_page: Page | None = None
    watchdog_task: asyncio.Task[None] | None = None
    portal_browser_closed: bool = False


class _LockedSolver:
    """Serializes calls to Gemini's one shared browser conversation."""

    def __init__(self, solver: CaptchaSolver, lock: asyncio.Lock) -> None:
        self._solver = solver
        self._lock = lock

    async def verify_ready(self) -> bool:
        async with self._lock:
            return await self._solver.verify_ready()

    async def solve(self, expected_length: int | None = None) -> str:
        async with self._lock:
            return await self._solver.solve(expected_length)

    async def solve_image(self, image_bytes: bytes, expected_length: int | None = None) -> str:
        async with self._lock:
            return await self._solver.solve_image(image_bytes, expected_length)

    async def cancel_active_response(self) -> None:
        async with self._lock:
            await self._solver.cancel_active_response()


class AutomationController:
    """Runs independent tab workflows on one dedicated asyncio worker thread."""

    def __init__(
        self, config: AppConfig, *, portal_session_factory: PortalSessionFactory | None = None
    ) -> None:
        self.config = config
        self.activity_log = DailyActivityLog()
        self.events: queue.Queue[UiEvent] = queue.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sessions: dict[str, RunSession] = {}
        self._reserved_tabs: set[str] = set()
        self._session_lock = threading.Lock()
        self._portal_session_factory = portal_session_factory
        self.gemini_browser: BrowserSession | None = None
        self.solver: CaptchaSolver | None = None
        self._ocr_locks: dict[OcrEngine, asyncio.Lock] = {}
        self.ocr_test_task: asyncio.Task[None] | None = None
        self._is_shut_down = False
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, name="automation-worker", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    @property
    def controls(self) -> RunControls | None:
        """Legacy view of the default tab's controls."""
        session = self.sessions.get(DEFAULT_TAB_ID)
        return session.controls if session is not None else None

    @property
    def run_task(self) -> asyncio.Task[None] | None:
        """Legacy view of the default tab's task."""
        session = self.sessions.get(DEFAULT_TAB_ID)
        return session.run_task if session is not None else None

    def emit(self, event: UiEvent, *, run_id: str = "") -> None:
        """Publish an event, adding run_id without breaking the old model."""
        data = dict(event.data)
        data.setdefault("run_id", run_id)
        if "run_id" in UiEvent.__dataclass_fields__:
            event = replace(event, data=data, run_id=run_id)
        else:
            event = replace(event, data=data)
        self.activity_log.write(
            event.kind,
            event.message,
            level=str(event.data.get("level", "INFO")),
            data=event.data,
        )
        self.events.put(event)

    def _emit_session(self, session: RunSession, event: UiEvent) -> None:
        self.emit(event, run_id=session.run_id)

    @staticmethod
    def _event(kind: str, message: str = "", data: dict[str, Any] | None = None) -> UiEvent:
        return UiEvent(kind, message, data or {})

    def record_activity(self, action: str, message: str = "") -> None:
        self.activity_log.write(action, message)

    def verify_gemini(self) -> None:
        self._submit(self._verify_gemini())

    def test_gemini_ocr(self) -> None:
        if self.loop is None:
            self.emit(self._event("ocr_test_failed", "Automation worker is unavailable."))
            return
        self.loop.call_soon_threadsafe(self._start_gemini_ocr_test)

    def start(self, tab_id: str | RunOptions, options: RunOptions | None = None) -> None:
        """Start a tab. ``start(options)`` remains valid for the legacy UI."""
        if isinstance(tab_id, RunOptions):
            options = tab_id
            tab_id = DEFAULT_TAB_ID
        if options is None:
            raise TypeError("start() requires a tab_id and RunOptions")
        if not tab_id:
            raise ValueError("tab_id must not be empty")
        if self.loop is None or self._is_shut_down:
            raise RuntimeError("Automation worker is unavailable.")
        with self._session_lock:
            if tab_id in self._reserved_tabs:
                run_id = self._run_id(tab_id, options)
                self.emit(
                    self._event("fatal_error", "This tab already has an active automation run."),
                    run_id=run_id,
                )
                raise ValueError(f"Tab {tab_id!r} already has an active automation run.")
            self._reserved_tabs.add(tab_id)
        self.loop.call_soon_threadsafe(self._start_run, tab_id, options)

    def pause(self, tab_id: str | None = None) -> None:
        self._call_soon(self._pause_run, tab_id or DEFAULT_TAB_ID)

    def resume(self, tab_id: str | None = None) -> None:
        self._call_soon(self._resume_run, tab_id or DEFAULT_TAB_ID)

    def stop(self, tab_id: str | None = None) -> None:
        self._submit(self._stop_run(tab_id or DEFAULT_TAB_ID, "Stopped by user"))

    def decide_error(self, tab_id: str, action: str | None = None) -> None:
        """Apply an error decision. ``decide_error(action)`` targets the default tab."""
        if action is None:
            action, tab_id = tab_id, DEFAULT_TAB_ID
        self._call_soon(self._decide_error, tab_id, action)

    def stop_all(self) -> None:
        self._submit(self._stop_all_runs("Stopped by user"))

    def reconfigure_browser(self) -> None:
        self._submit(self._close_gemini_browser())

    def close_gemini_ocr(self) -> None:
        self._submit(self._close_gemini_ocr())

    def shutdown(self) -> None:
        if self._is_shut_down:
            return
        self._is_shut_down = True
        if self.loop is not None and self.loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self.loop)
                with suppress(Exception):
                    future.result(timeout=15)
            finally:
                with suppress(Exception):
                    self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        cleanup_all_spawned_processes()

    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ocr_locks = {engine: asyncio.Lock() for engine in OcrEngine}
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

    @staticmethod
    def _run_id(tab_id: str, options: RunOptions) -> str:
        return str(getattr(options, "run_id", "") or tab_id)

    def _call_soon(self, callback: Callable[..., None], *args: object) -> None:
        if self.loop is None or self._is_shut_down:
            return
        self.loop.call_soon_threadsafe(callback, *args)

    def _start_run(self, tab_id: str, options: RunOptions) -> None:
        if tab_id in self.sessions:
            self.emit(
                self._event("fatal_error", "This tab already has an active automation run."),
                run_id=self._run_id(tab_id, options),
            )
            return
        session: RunSession
        session = RunSession(
            tab_id=tab_id,
            run_id=self._run_id(tab_id, options),
            options=options,
            controls=RunControls(lambda event: self._emit_session(session, event)),
        )
        self.sessions[tab_id] = session
        session.run_task = asyncio.create_task(self._run(session), name=f"automation:{tab_id}")

    def _pause_run(self, tab_id: str) -> None:
        session = self.sessions.get(tab_id)
        if session is not None:
            session.controls.pause()
            self._emit_session(session, self._event("paused", "Automation paused."))

    def _resume_run(self, tab_id: str) -> None:
        session = self.sessions.get(tab_id)
        if session is not None:
            session.controls.resume()
            self._emit_session(session, self._event("resumed", "Automation resumed."))

    def _decide_error(self, tab_id: str, action: str) -> None:
        session = self.sessions.get(tab_id)
        if session is not None:
            session.controls.decide(action)

    async def _stop_run(self, tab_id: str, reason: str) -> None:
        session = self.sessions.get(tab_id)
        if session is None:
            return
        session.controls.stop(reason)
        task = session.run_task
        if task is not None and not task.done():
            task.cancel()
        await self._close_portal_browser(session)
        self._emit_session(session, self._event("portal_closed", "Portal browser closed."))

    async def _stop_all_runs(self, reason: str) -> None:
        await asyncio.gather(
            *(self._stop_run(tab_id, reason) for tab_id in list(self.sessions)), return_exceptions=True
        )

    async def _ensure_gemini_services(self) -> tuple[BrowserSession, GeminiWebCaptchaSolver]:
        if self.gemini_browser is None:
            self.gemini_browser = BrowserSession(
                Path(self.config.chrome_executable),
                self.config.profile_path,
                self.config.debug_port,
                self._on_gemini_browser_disconnected,
                headless=False,
            )
        if not isinstance(self.solver, GeminiWebCaptchaSolver):
            self.solver = GeminiWebCaptchaSolver(self.gemini_browser)
        await self.gemini_browser.start()
        return self.gemini_browser, self.solver

    async def _ensure_ocr_solver(self, engine: OcrEngine) -> CaptchaSolver:
        lock = self._ocr_locks.get(engine)
        if lock is None:
            raise RuntimeError("Automation worker is unavailable.")
        async with lock:
            if engine == OcrEngine.PADDLEOCR:
                from services.paddleocr_ocr import PaddleOcrCaptchaSolver

                solver: CaptchaSolver = PaddleOcrCaptchaSolver()
            elif engine == OcrEngine.EASYOCR:
                from services.gemini_ocr import EasyOcrCaptchaSolver

                solver = EasyOcrCaptchaSolver()
            else:
                _, solver = await self._ensure_gemini_services()
            if not await solver.verify_ready():
                raise RuntimeError(f"{_ocr_engine_label(engine)} is not ready.")
        return _LockedSolver(solver, lock)

    async def _verify_gemini(self) -> None:
        try:
            engine = OcrEngine(self.config.ocr_engine)
            await self._ensure_ocr_solver(engine)
            self.emit(self._event("gemini_verified", f"{_ocr_engine_label(engine)} is ready."))
        except Exception as error:
            self.emit(self._event("gemini_not_ready", f"OCR verification failed: {error}"))

    def _start_gemini_ocr_test(self) -> None:
        if self.sessions:
            self.emit(self._event("ocr_test_failed", "Stop active runs before running the OCR test."))
            return
        if self.ocr_test_task is not None and not self.ocr_test_task.done():
            self.emit(self._event("ocr_test_failed", "An OCR test is already running."))
            return
        self.ocr_test_task = asyncio.create_task(self._test_gemini_ocr())

    async def _test_gemini_ocr(self) -> None:
        try:
            engine = OcrEngine(self.config.ocr_engine)
            self.emit(self._event("ocr_test_started", f"Testing {_ocr_engine_label(engine)}..."))
            solver = await self._ensure_ocr_solver(engine)
            test_image = bundled_path("test-images/1.png")
            if not test_image.is_file():
                raise RuntimeError(f"OCR test image was not found: {test_image}")
            self.emit(self._event("ocr_test_progress", "Reading the bundled CAPTCHA test image..."))
            code = await solver.solve_image(test_image.read_bytes())
            self.emit(
                self._event(
                    "ocr_test_succeeded",
                    f"{_ocr_engine_label(engine)} read the test CAPTCHA as: {code}",
                    {"code": code},
                )
            )
        except Exception as error:
            self.emit(self._event("ocr_test_failed", f"CAPTCHA OCR test failed: {error}"))
        finally:
            self.ocr_test_task = None

    async def _run(self, session: RunSession) -> None:
        try:
            solver: CaptchaSolver | None = None
            if session.options.ocr_enabled:
                solver = await self._ensure_ocr_solver(session.options.ocr_engine)
                self._emit_session(
                    session,
                    self._event(
                        "gemini_verified", f"{_ocr_engine_label(session.options.ocr_engine)} is ready."
                    ),
                )
            else:
                self._emit_session(
                    session, self._event("ocr_manual_mode", "OCR is inactive; CAPTCHAs require manual entry.")
                )
            self._emit_session(session, self._event("run_started", "Automation started."))

            async def open_portal_page() -> Page:
                return await self._open_fresh_portal_page(session)

            async def close_portal_page() -> None:
                await self._close_portal_browser(session)

            async def focus_payment_page() -> None:
                browser = session.portal_browser
                page = session.portal_page
                if browser is None or page is None:
                    raise RuntimeError("No portal browser page is available for payment.")
                await browser.focus(page, session.controls)

            page: Page | None = None
            if not session.options.fresh_browser_per_unit:
                if not self._can_reuse_portal(session, session.options.portal_browser):
                    page = await open_portal_page()
                else:
                    page = session.portal_page
                if page is None:
                    raise RuntimeError("The portal browser did not provide a page.")

            session.watchdog_task = asyncio.create_task(self._monitor_portal_browser(session))
            engine = WorkflowEngine(
                page,
                solver,
                session.controls,
                lambda event: self._emit_session(session, event),
                open_portal_page=open_portal_page,
                close_portal_page=close_portal_page,
                focus_payment_page=focus_payment_page,
            )
            await engine.run(session.options)
        except asyncio.CancelledError:
            self._emit_session(session, self._event("run_stopped", "Automation stopped."))
        except Exception as error:
            self._emit_session(session, self._event("fatal_error", f"Automation stopped: {error}"))
        finally:
            watchdog = session.watchdog_task
            session.watchdog_task = None
            if watchdog is not None:
                watchdog.cancel()
                await asyncio.gather(watchdog, return_exceptions=True)
            await self._close_portal_browser(session)
            if self.sessions.get(session.tab_id) is session:
                del self.sessions[session.tab_id]
            with self._session_lock:
                self._reserved_tabs.discard(session.tab_id)

    def _can_reuse_portal(self, session: RunSession, choice: PortalBrowser) -> bool:
        return (
            session.portal_browser is not None
            and session.portal_page is not None
            and not session.portal_page.is_closed()
            and session.portal_browser.is_active
            and session.portal_browser.choice == choice
        )

    async def _open_fresh_portal_page(self, session: RunSession) -> Page:
        await self._close_portal_browser(session)

        def callback(browser: PortalBrowserSession) -> None:
            self._on_portal_browser_disconnected(session, browser)

        browser = (
            self._portal_session_factory(session.options.portal_browser, callback)
            if self._portal_session_factory is not None
            else PortalBrowserSession(
                session.options.portal_browser,
                callback,
                getattr(session.options, "portal_profile_path", None),
            )
        )
        session.portal_browser = browser
        session.portal_browser_closed = False
        session.portal_page = await browser.new_portal_page(CITIZEN_LOGIN_URL, timeout_ms=0)
        return session.portal_page

    async def _monitor_portal_browser(self, session: RunSession) -> None:
        while True:
            browser = session.portal_browser
            page = session.portal_page
            if (
                browser is not None
                and page is not None
                and not session.portal_browser_closed
                and (page.is_closed() or not browser.is_active)
            ):
                self._on_portal_browser_disconnected(session, browser)
                return
            await asyncio.sleep(1)

    def _on_portal_browser_disconnected(self, session: RunSession, browser: PortalBrowserSession) -> None:
        if browser is not session.portal_browser or session.portal_browser_closed:
            return
        session.portal_browser_closed = True
        if session.controls.stop_event.is_set():
            return
        name = browser.choice.name
        self._emit_session(
            session, self._event("browser_closed", f"{name} was closed. The automation has stopped.")
        )
        session.controls.stop(f"{name} was closed")
        if session.run_task is not None and not session.run_task.done():
            session.run_task.cancel()

    async def _close_portal_browser(self, session: RunSession) -> None:
        session.portal_browser_closed = True
        browser, session.portal_browser = session.portal_browser, None
        session.portal_page = None
        if browser is not None:
            with suppress(Exception):
                await browser.close()

    async def _close_gemini_ocr(self) -> None:
        await self._close_gemini_browser()
        self.emit(self._event("gemini_stopped", "Gemini OCR browser stopped."))

    def _on_gemini_browser_disconnected(self) -> None:
        self.gemini_browser = None
        self.solver = None
        self.emit(
            self._event(
                "browser_closed",
                "The Gemini OCR browser session stopped. OCR requests will fail until it is reopened.",
                {"profile_browser": True},
            )
        )

    async def _close_gemini_browser(self) -> None:
        browser, self.gemini_browser = self.gemini_browser, None
        self.solver = None
        if browser is not None:
            with suppress(Exception):
                await browser.close()

    async def _shutdown_async(self) -> None:
        self.emit(
            self._event("browsers_closing", "Closing portal browsers and the signed-in Chrome profile.")
        )
        await self._stop_all_runs("Application closed")
        tasks = [session.run_task for session in self.sessions.values() if session.run_task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.ocr_test_task is not None:
            self.ocr_test_task.cancel()
            await asyncio.gather(self.ocr_test_task, return_exceptions=True)
        await self._close_gemini_browser()

    def _submit(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        if self.loop is None or self._is_shut_down:
            coroutine.close()
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def report_failure(done: Future[Any]) -> None:
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception as error:
                self.emit(self._event("fatal_error", str(error)))

        future.add_done_callback(report_failure)
