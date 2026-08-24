from __future__ import annotations

import asyncio
import time
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from core.config import AppConfig
from core.controller import AutomationController, PortalSessionFactory
from core.models import BrowserEngine, Credentials, PortalBrowser, RunMode, RunOptions


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
                self.wait_for(lambda: len(attempts) == 2)
                self.wait_for(lambda: "tab-one" not in controller.sessions)

                self.assertEqual(len(attempts), 2)
                events = []
                while not controller.events.empty():
                    events.append(controller.events.get_nowait())
                self.assertTrue(
                    any("startup attempt 1 failed" in event.message for event in events)
                )
                self.assertTrue(any(event.kind == "session_finished" for event in events))
            finally:
                controller.shutdown()
