from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

from playwright.async_api import Page

from automation.portal import PortalAutomation
from core.controls import RunControls
from core.models import (
    AutomationError,
    PersistenceError,
    RunMode,
    RunOptions,
    Stage,
    UiEvent,
    WorkflowStopped,
)
from services.csv_store import CsvBatchStore
from services.gemini_ocr import GeminiCaptchaSolver


class WorkflowEngine:
    def __init__(
        self,
        page: Page,
        solver: GeminiCaptchaSolver,
        controls: RunControls,
        emit: Callable[[UiEvent], None],
    ) -> None:
        self.page = page
        self.solver = solver
        self.controls = controls
        self.emit = emit
        self.store: CsvBatchStore | None = None
        self.current_row: dict[str, str] | None = None
        self.current_stage = Stage.IDLE

    async def run(self, options: RunOptions) -> None:
        self.store = CsvBatchStore(options.csv_path)
        self.store.load()
        await self._persist()
        self.emit(UiEvent("batch_update", data={"rows": self.store.summaries()}))

        if not options.article.strip():
            self.emit(UiEvent("fatal_error", "Choose or type an Article before starting the batch."))
            return

        download_root = options.download_root or Path.home() / "Downloads"
        portal = PortalAutomation(
            self.page,
            self.solver,
            self.controls,
            self._on_stage,
            self.emit,
        )
        try:
            pending = list(self.store.pending_rows())
            if not pending:
                self.emit(UiEvent("run_completed", "All CSV rows are already complete."))
                return

            for row_number, row in pending:
                self.current_row = row
                await self.controls.checkpoint()
                validation = self.store.validate_row(row)
                if validation:
                    error = AutomationError(
                        "; ".join(validation),
                        stage=Stage.VALIDATING,
                        code="invalid_csv_row",
                    )
                    action = await self._handle_error(portal, row_number, row, error, options.mode)
                    if action == "retry":
                        # Validate again in case the in-memory row is updated by a future editor.
                        validation = self.store.validate_row(row)
                        if validation:
                            continue
                    continue

                while int(row["completed_quantity"]) < int(row["quantity"]):
                    self.current_stage = Stage.CITIZEN_LOGIN
                    self.store.set_running(row, self.current_stage)
                    await self._persist()
                    self._publish_progress(row_number, row)
                    sequence = int(row["completed_quantity"]) + 1
                    try:
                        destination, reference = await portal.process_unit(
                            row,
                            options.article,
                            options.credentials,
                            download_root,
                            row_number + 1,
                            sequence,
                        )
                        relative_path = os.path.relpath(destination, options.csv_path.parent)
                        self.store.mark_success(row, reference, relative_path)
                        await self._persist()
                        self._publish_progress(row_number, row)
                        self.emit(
                            UiEvent(
                                "log",
                                f"Row {row_number + 1}, unit {sequence} completed: {destination.name}",
                            )
                        )
                        await portal.reset_to_start()
                    except WorkflowStopped:
                        raise
                    except AutomationError as error:
                        action = await self._handle_error(portal, row_number, row, error, options.mode)
                        if error.code == "browser_closed":
                            raise WorkflowStopped from error
                        if action == "retry":
                            continue
                        break

            self.current_row = None
            self.current_stage = Stage.IDLE
            self.emit(UiEvent("run_completed", "Batch pass finished. Failed rows remain retryable."))
        except (WorkflowStopped, asyncio.CancelledError):
            if self.current_row is not None:
                self.store.mark_stopped(
                    self.current_row,
                    self.current_stage,
                    self.controls.stop_reason,
                )
                await self._persist(ignore_stop=True)
                self.emit(UiEvent("batch_update", data={"rows": self.store.summaries()}))
            self.emit(
                UiEvent(
                    "run_stopped",
                    f"{self.controls.stop_reason}. Current work remains retryable.",
                )
            )

    async def _handle_error(
        self,
        portal: PortalAutomation,
        row_number: int,
        row: dict[str, str],
        error: AutomationError,
        mode: RunMode,
    ) -> str:
        self.current_stage = error.stage
        self.store_or_raise().mark_error(row, error.stage, str(error))
        await self._persist()
        self._publish_progress(row_number, row)
        self.emit(
            UiEvent(
                "log",
                f"Row {row_number + 1} failed at {error.stage.value}: {error}",
                {"level": "error"},
            )
        )
        if error.code == "browser_closed":
            return "next"

        try:
            await portal.reset_to_start(record_stage=False)
        except Exception as reset_error:
            self.emit(UiEvent("log", f"Could not reset the portal: {reset_error}", {"level": "error"}))

        if mode == RunMode.CONTINUOUS:
            return "next"
        self.emit(
            UiEvent(
                "error_prompt",
                str(error),
                {
                    "row": row_number + 1,
                    "stage": error.stage.value,
                    "post_payment_warning": error.stage in {Stage.PAYMENT, Stage.RESULT, Stage.DOWNLOAD},
                },
            )
        )
        return await self.controls.wait_for_decision()

    async def _on_stage(self, stage: Stage) -> None:
        self.current_stage = stage
        if self.current_row is not None:
            self.store_or_raise().set_stage(self.current_row, stage)
            await self._persist()
        self.emit(UiEvent("stage", stage.value))

    async def _persist(self, ignore_stop: bool = False) -> None:
        while True:
            try:
                self.store_or_raise().persist()
                return
            except PersistenceError as error:
                if ignore_stop:
                    self.emit(UiEvent("log", f"Final CSV save failed: {error}", {"level": "error"}))
                    return
                self.controls.pause()
                self.emit(UiEvent("persistence_blocked", str(error)))
                await self.controls.checkpoint()

    def _publish_progress(self, row_number: int, row: dict[str, str]) -> None:
        store = self.store_or_raise()
        self.emit(
            UiEvent(
                "batch_update",
                data={
                    "rows": store.summaries(),
                    "current_row": row_number + 1,
                    "current_unit": int(row["completed_quantity"]) + 1,
                },
            )
        )

    def store_or_raise(self) -> CsvBatchStore:
        if self.store is None:
            raise RuntimeError("The CSV store is not loaded.")
        return self.store
