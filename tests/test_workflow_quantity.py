from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from automation.portal import PortalAutomation
from core.controls import RunControls
from core.models import (
    AutomationError,
    BrowserEngine,
    CaptchaCopyMode,
    Credentials,
    PortalBrowser,
    RunMode,
    RunOptions,
    Stage,
    TransactionResult,
    UiEvent,
)
from core.workflow import ParallelBatchRuntime, WorkflowEngine
from services.csv_store import CsvBatchStore


class WorkflowQuantityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.csv_path = self.directory / "batch.csv"
        CsvBatchStore.write_template(self.csv_path)
        store = CsvBatchStore(self.csv_path)
        store.load()
        store.rows[0].update(
            {
                "district": "Ranchi",
                "first_party_name": "First Party",
                "stamp_duty_paid_by": "First Party",
                "stamp_purpose": "Test purpose",
                "amount": "20",
                "quantity": "2",
            }
        )
        store.persist()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def options(self) -> RunOptions:
        return RunOptions(
            csv_path=self.csv_path,
            download_root=self.directory / "downloads",
            article="AFFIDAVIT",
            portal_browser=PortalBrowser("Test", Path("browser.exe"), BrowserEngine.CHROMIUM),
            mode=RunMode.ASSISTED,
            credentials=Credentials(),
        )

    def run_with_results(
        self, decision: str, results: list[AutomationError | TransactionResult]
    ) -> tuple[dict[str, str], list[int]]:
        controls = RunControls(lambda _event: None)
        controls.decide(decision)
        portal = MagicMock()
        portal.ensure_citizen_session = AsyncMock()
        portal.reset_to_start = AsyncMock()
        portal.process_unit = AsyncMock(side_effect=results)
        engine = WorkflowEngine(MagicMock(), None, controls, lambda _event: None)

        with patch("core.workflow.PortalAutomation", return_value=portal):
            self.assertTrue(asyncio.run(engine.run(self.options())))

        reloaded = CsvBatchStore(self.csv_path)
        reloaded.load()
        sequences = [int(call.args[5]) for call in portal.process_unit.await_args_list]
        return reloaded.rows[0], sequences

    def test_move_next_advances_one_quantity(self) -> None:
        row, sequences = self.run_with_results(
            "next",
            [
                AutomationError("First quantity failed", stage=Stage.EGRAS_LOGIN),
                TransactionResult({"Transaction ID": "second"}, "second"),
            ],
        )

        self.assertEqual(sequences, [1, 2])
        self.assertEqual(row["processed_quantity"], "2")
        self.assertEqual(row["completed_quantity"], "1")

    def test_retry_repeats_current_quantity_before_advancing(self) -> None:
        row, sequences = self.run_with_results(
            "retry",
            [
                AutomationError("Temporary failure", stage=Stage.EGRAS_LOGIN),
                TransactionResult({"Transaction ID": "first"}, "first"),
                TransactionResult({"Transaction ID": "second"}, "second"),
            ],
        )

        self.assertEqual(sequences, [1, 1, 2])
        self.assertEqual(row["processed_quantity"], "2")
        self.assertEqual(row["completed_quantity"], "2")

    def test_browser_failure_marks_run_stopped_as_browser_closed(self) -> None:
        controls = RunControls(lambda _event: None)
        portal = MagicMock()
        portal.ensure_citizen_session = AsyncMock()
        portal.process_unit = AsyncMock(
            side_effect=AutomationError(
                "Chrome or the portal page was closed.",
                stage=Stage.CITIZEN_LOGIN,
                code="browser_closed",
                retryable=False,
            )
        )
        events: list[UiEvent] = []
        engine = WorkflowEngine(MagicMock(), None, controls, events.append)

        with patch("core.workflow.PortalAutomation", return_value=portal):
            self.assertFalse(asyncio.run(engine.run(self.options())))

        stopped = next(event for event in events if event.kind == "run_stopped")
        self.assertTrue(stopped.data["browser_closed"])

    def test_parallel_runtime_assigns_distinct_quantities_to_browsers(self) -> None:
        runtime = ParallelBatchRuntime(self.csv_path)

        async def exercise_queue() -> tuple[
            tuple[int, dict[str, str], int],
            tuple[int, dict[str, str], int],
            None,
        ]:
            await runtime.initialize()
            first = runtime.claim_next()
            second = runtime.claim_next()
            assert first is not None
            assert second is not None
            finished = runtime.claim_next()
            assert finished is None
            return first, second, finished

        first, second, finished = asyncio.run(exercise_queue())

        self.assertEqual(first[2], 1)
        self.assertEqual(second[2], 2)
        self.assertIs(first[1], second[1])
        self.assertIsNone(finished)

    def test_parallel_citizen_logins_are_serialized_for_one_id(self) -> None:
        runtime = ParallelBatchRuntime(self.csv_path)
        controls = RunControls(lambda _event: None)
        active = 0
        maximum_active = 0

        async def login_step(_credentials: Credentials) -> None:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1

        portals = [
            PortalAutomation(
                MagicMock(),
                None,
                controls,
                AsyncMock(),
                lambda _event: None,
                MagicMock(),
                "",
                CaptchaCopyMode.DIRECT,
                citizen_login_lock=runtime.citizen_login_lock,
            )
            for _ in range(3)
        ]
        for portal in portals:
            portal._ensure_citizen_session = AsyncMock(  # type: ignore[method-assign]
                side_effect=login_step
            )

        async def run_logins() -> None:
            await asyncio.gather(
                *(portal.ensure_citizen_session(Credentials()) for portal in portals)
            )

        asyncio.run(run_logins())

        self.assertEqual(maximum_active, 1)

    def test_parallel_worker_does_not_prelogin_before_processing_claim(self) -> None:
        runtime = ParallelBatchRuntime(self.csv_path)
        controls = RunControls(lambda _event: None)
        portal = MagicMock()
        portal.ensure_citizen_session = AsyncMock()
        portal.reset_to_start = AsyncMock()
        portal.process_unit = AsyncMock(
            side_effect=[
                TransactionResult({"Transaction ID": "first"}, "first"),
                TransactionResult({"Transaction ID": "second"}, "second"),
            ]
        )
        options = self.options()
        options = replace(options, browser_count=2)
        engine = WorkflowEngine(
            MagicMock(),
            None,
            controls,
            lambda _event: None,
            parallel_runtime=runtime,
        )

        with patch("core.workflow.PortalAutomation", return_value=portal):
            self.assertTrue(asyncio.run(engine.run(options)))

        portal.ensure_citizen_session.assert_not_awaited()
        self.assertEqual(portal.process_unit.await_count, 2)


if __name__ == "__main__":
    unittest.main()
