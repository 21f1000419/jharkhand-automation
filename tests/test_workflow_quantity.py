from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.controls import RunControls
from core.models import (
    AutomationError,
    BrowserEngine,
    Credentials,
    PortalBrowser,
    RunMode,
    RunOptions,
    Stage,
    TransactionResult,
    UiEvent,
)
from core.workflow import WorkflowEngine
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


if __name__ == "__main__":
    unittest.main()
