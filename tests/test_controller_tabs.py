from __future__ import annotations

import asyncio
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from core.config import AppConfig
from core.controller import (
    AutomationController,
    PortalSessionFactory,
    worker_dock_id,
    worker_short_label,
)
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

    def test_one_id_starts_the_configured_number_of_browser_workers(self) -> None:
        options = _options("run-one")
        options = replace(options, browser_count=3)
        with patch("core.controller.WorkflowEngine", _BlockingWorkflow):
            controller = AutomationController(AppConfig())
            try:
                controller.start("tab-one", options)
                expected = {
                    "tab-one",
                    "tab-one::browser-2",
                    "tab-one::browser-3",
                }
                self.wait_for(lambda: set(controller.sessions) == expected)
                self.assertEqual(
                    {session.group_id for session in controller.sessions.values()},
                    {"tab-one"},
                )
                self.assertEqual(
                    {session.worker_number for session in controller.sessions.values()},
                    {1, 2, 3},
                )
            finally:
                controller.shutdown()

    def test_parallel_browsers_use_concise_dock_names(self) -> None:
        self.assertEqual(worker_short_label("3", 1, 3), "B3.1")
        self.assertEqual(worker_short_label("3", 2, 3), "B3.2")
        self.assertEqual(worker_short_label("3", 3, 3), "B3.3")
        self.assertEqual(worker_dock_id("3", 2, 3), "3.2")
        self.assertEqual(worker_dock_id("3", 1, 1), "3")
        self.assertEqual(
            AutomationController._worker_window_label("ID 3 | user", 3, 1, "3"),
            "ID 3 | user | B3.2",
        )
        self.assertEqual(
            AutomationController._worker_window_label("ID 3 | user", 1, 0, "3"),
            "ID 3 | user",
        )

    def test_parallel_browser_events_carry_their_own_dock_id(self) -> None:
        options = _options("3")
        options = replace(options, browser_count=2)
        with patch("core.controller.WorkflowEngine", _BlockingWorkflow):
            controller = AutomationController(AppConfig())
            try:
                controller.start("3", options)
                self.wait_for(lambda: len(controller.sessions) == 2)
                sessions = sorted(
                    controller.sessions.values(), key=lambda s: s.worker_number
                )
                self.assertEqual(
                    [s.options.portal_window_label for s in sessions],
                    ["B3.1", "B3.2"],
                )

                def run_started_dock_ids() -> set[str]:
                    found: set[str] = set()
                    while not controller.events.empty():
                        event = controller.events.get_nowait()
                        if event.kind == "run_started" and event.data.get("dock_id"):
                            found.add(str(event.data["dock_id"]))
                    return found

                self.wait_for(lambda: {"3.1"} <= run_started_dock_ids())
                for session in sessions:
                    self.assertEqual(
                        str(
                            controller.sessions[session.tab_id]
                            .options.portal_window_label
                        ),
                        f"B3.{session.worker_number}",
                    )
            finally:
                controller.shutdown()

    def test_stopping_one_browser_keeps_the_other_working(self) -> None:
        options = _options("3")
        options = replace(options, browser_count=2)
        with patch("core.controller.WorkflowEngine", _BlockingWorkflow):
            controller = AutomationController(AppConfig())
            try:
                controller.start("3", options)
                self.wait_for(lambda: len(controller.sessions) == 2)
                controller.stop("3.2")
                self.wait_for(lambda: len(controller.sessions) == 1)
                remaining = next(iter(controller.sessions.values()))
                self.assertEqual(remaining.worker_number, 1)
                self.assertEqual(remaining.group_id, "3")

                def worker_stopped_seen() -> bool:
                    while not controller.events.empty():
                        event = controller.events.get_nowait()
                        if event.kind == "worker_stopped" and str(
                            event.data.get("dock_id", "")
                        ) == "3.2":
                            return True
                    return False

                self.wait_for(worker_stopped_seen)
                # Group is still reserved: starting the same ID must be rejected.
                with self.assertRaises(ValueError):
                    controller.start("3", options)
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
