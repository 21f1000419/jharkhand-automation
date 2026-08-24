from __future__ import annotations

import asyncio
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import AppConfig
from core.controller import AutomationController
from core.models import BrowserEngine, Credentials, PortalBrowser, RunMode, RunOptions


class _BlockingWorkflow:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def run(self, options: RunOptions) -> bool:
        await asyncio.Event().wait()
        return False


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
