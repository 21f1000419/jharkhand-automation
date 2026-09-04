from __future__ import annotations

import asyncio
import threading
import time
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from core.config import AppConfig
from core.controller import AutomationController, PortalSessionFactory
from core.models import BrowserEngine, Credentials, OcrEngine, PortalBrowser, RunMode, RunOptions


class _BlockingWorkflow:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def run(self, options: RunOptions) -> bool:
        await asyncio.Event().wait()
        return False


class _OpeningWorkflow:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.open_portal_page: Any = kwargs["open_portal_page"]

    async def run(self, options: RunOptions) -> bool:
        await self.open_portal_page()
        return True


class _ConcurrentWorkflow:
    entered: set[str] = set()
    both_entered = threading.Event()

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def run(self, options: RunOptions) -> bool:
        self.entered.add(options.run_id)
        if len(self.entered) == 2:
            self.both_entered.set()
        await asyncio.Event().wait()
        return False


class _PortalSessionAttempt:
    def __init__(self, choice: PortalBrowser, *, fail: bool) -> None:
        self.choice = choice
        self.fail = fail
        self.is_active = True

    async def new_portal_page(self, _url: str, *, timeout_ms: int) -> object:
        if self.fail:
            raise RuntimeError("simulated startup failure")
        page = MagicMock()
        page.is_closed.return_value = False
        return page

    async def close(self) -> None:
        self.is_active = False


class _RecordingSolver:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def verify_ready(self) -> bool:
        self.calls.append(("verify", threading.current_thread().name))
        return True

    async def solve(self, expected_length: int | None = None) -> str:
        self.calls.append(("solve", threading.current_thread().name, expected_length))
        return "ABCDEF"

    async def solve_image(
        self, image_bytes: bytes, expected_length: int | None = None
    ) -> str:
        self.calls.append(
            ("solve_image", threading.current_thread().name, image_bytes, expected_length)
        )
        return "ABCDEF"

    async def cancel_active_response(self) -> None:
        self.calls.append(("cancel", threading.current_thread().name))


def _options(run_id: str) -> RunOptions:
    return RunOptions(
        csv_path=Path("batch.csv"),
        download_root=None,
        article="Article",
        portal_browser=PortalBrowser("Test browser", Path("browser.exe"), BrowserEngine.CHROMIUM),
        mode=RunMode.ASSISTED,
        credentials=Credentials(),
        fresh_browser_per_unit=True,
        run_id=run_id,
    )


class AutomationControllerTabTests(unittest.TestCase):
    def wait_for(self, predicate: object, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if callable(predicate) and predicate():
                return
            time.sleep(0.01)
        self.fail("controller did not reach the expected state")

    def test_stop_only_closes_its_own_tab_and_events_have_run_ids(self) -> None:
        with patch("core.controller.WorkflowEngine", _BlockingWorkflow):
            controller = AutomationController(AppConfig())
            try:
                controller.start("tab-one", _options("run-one"))
                controller.start("tab-two", _options("run-two"))
                self.wait_for(lambda: set(controller.sessions) == {"tab-one", "tab-two"})

                controller.pause("tab-one")
                self.wait_for(lambda: not controller.events.empty())
                events = []
                while not controller.events.empty():
                    events.append(controller.events.get_nowait())
                self.assertIn("run-one", {event.run_id for event in events})

                controller.stop("tab-one")
                self.wait_for(lambda: "tab-one" not in controller.sessions)
                self.assertIn("tab-two", controller.sessions)
            finally:
                controller.shutdown()

    def test_two_id_runs_enter_their_workflows_in_parallel(self) -> None:
        _ConcurrentWorkflow.entered = set()
        _ConcurrentWorkflow.both_entered = threading.Event()
        with patch("core.controller.WorkflowEngine", _ConcurrentWorkflow):
            controller = AutomationController(AppConfig())
            try:
                controller.start("tab-one", _options("run-one"))
                controller.start("tab-two", _options("run-two"))

                self.assertTrue(_ConcurrentWorkflow.both_entered.wait(timeout=2))
                self.assertEqual(_ConcurrentWorkflow.entered, {"run-one", "run-two"})
            finally:
                controller.shutdown()

    def test_duplicate_active_tab_is_rejected(self) -> None:
        with patch("core.controller.WorkflowEngine", _BlockingWorkflow):
            controller = AutomationController(AppConfig())
            try:
                controller.start("tab-one", _options("run-one"))
                self.wait_for(lambda: "tab-one" in controller.sessions)
                with self.assertRaises(ValueError):
                    controller.start("tab-one", _options("second-run"))
            finally:
                controller.shutdown()

    def test_external_ocr_proxy_runs_local_ocr_on_controller_loop(self) -> None:
        controller = AutomationController(AppConfig())
        recording_solver = _RecordingSolver()
        requested_engines: list[OcrEngine] = []

        async def ensure_solver(engine: OcrEngine) -> _RecordingSolver:
            requested_engines.append(engine)
            return recording_solver

        try:
            with patch.object(controller, "_ensure_ocr_solver", new=ensure_solver):
                proxy = controller.ocr_solver_for_external_loop(OcrEngine.PADDLEOCR)
                self.assertTrue(asyncio.run(proxy.verify_ready()))
                result = asyncio.run(proxy.solve_image(b"captcha", 6))

            self.assertEqual(result, "ABCDEF")
            self.assertEqual(requested_engines, [OcrEngine.PADDLEOCR, OcrEngine.PADDLEOCR])
            self.assertEqual(
                recording_solver.calls,
                [("solve_image", "automation-worker", b"captcha", 6)],
            )
        finally:
            controller.shutdown()

    def test_portal_startup_retries_once_for_only_the_affected_tab(self) -> None:
        attempts: list[_PortalSessionAttempt] = []

        def portal_factory(choice: PortalBrowser, _callback: object) -> _PortalSessionAttempt:
            session = _PortalSessionAttempt(choice, fail=not attempts)
            attempts.append(session)
            return session

        with patch("core.controller.WorkflowEngine", _OpeningWorkflow):
            controller = AutomationController(
                AppConfig(),
                portal_session_factory=cast(PortalSessionFactory, portal_factory),
            )
            try:
                controller.start("tab-one", _options("run-one"))
                events: list[Any] = []

                def session_finished_received() -> bool:
                    while not controller.events.empty():
                        events.append(controller.events.get_nowait())
                    return any(event.kind == "session_finished" for event in events)

                self.wait_for(session_finished_received)
                self.assertEqual(len(attempts), 2)
                self.assertTrue(
                    any("startup attempt 1 failed" in event.message for event in events)
                )
                self.assertTrue(any(event.kind == "session_finished" for event in events))
            finally:
                controller.shutdown()
