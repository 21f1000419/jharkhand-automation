from __future__ import annotations

import asyncio
import os
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path

from playwright.async_api import Page

from automation.portal import CitizenOtpResendBudget, PortalAutomation
from core.controls import RunControls
from core.models import (
    STAGE_CHECKPOINTS,
    AutomationError,
    Credentials,
    PersistenceError,
    RunMode,
    RunOptions,
    Stage,
    UiEvent,
    WorkflowStopped,
)
from core.payment_coordination import PaymentCoordinator, default_payment_coordinator
from services.captcha_ocr import CaptchaSolver
from services.csv_store import CsvBatchStore
from services.sms_otp_client import SmsOtpClient


def _download_path_for_csv(destination: Path | None, csv_path: Path) -> str:
    if destination is None:
        return ""
    try:
        return os.path.relpath(destination, csv_path.parent)
    except ValueError:
        # Windows cannot create a relative path between different drives.
        return str(destination.resolve())


class ParallelBatchRuntime:
    """Shared CSV state and quantity claims for every active ID."""

    def __init__(self, csv_path: Path) -> None:
        self.store = CsvBatchStore(csv_path)
        self.initialize_lock = asyncio.Lock()
        self.persist_lock = asyncio.Lock()
        self._claim_changed = asyncio.Condition()
        self.citizen_login_lock = asyncio.Lock()
        self.citizen_login_locks: dict[str, asyncio.Lock] = {}
        self.error_decision_locks: dict[str, asyncio.Lock] = {}
        self.initialized = False
        self.initial_has_work = False
        self.work: deque[tuple[int, dict[str, str], int]] = deque()
        self.claims: dict[str, tuple[int, dict[str, str], int]] = {}
        self.failed = False
        self.fatal = False

    async def initialize(self) -> None:
        async with self.initialize_lock:
            if self.initialized:
                return
            self.store.load()
            self.store.persist()
            for row_number, row in self.store.pending_rows():
                if self.store.validate_row(row):
                    self.work.append((row_number, row, self.store.next_pending_quantity(row)))
                    continue
                for sequence in self.store.pending_quantity_numbers(row):
                    self.work.append((row_number, row, sequence))
            self.initial_has_work = bool(self.work)
            self.initialized = True

    def citizen_login_lock_for(self, group_id: str) -> asyncio.Lock:
        if not group_id:
            return self.citizen_login_lock
        return self.citizen_login_locks.setdefault(group_id, asyncio.Lock())

    def error_decision_lock(self, group_id: str) -> asyncio.Lock:
        return self.error_decision_locks.setdefault(group_id, asyncio.Lock())

    async def claim_next(self, worker_key: str) -> tuple[int, dict[str, str], int] | None:
        async with self._claim_changed:
            while not self.work and self.claims:
                await self._claim_changed.wait()
            if not self.work:
                return None
            claimed = self.work.popleft()
            self.claims[worker_key] = claimed
            return claimed

    async def complete_claim(self, worker_key: str) -> None:
        async with self._claim_changed:
            self.claims.pop(worker_key, None)
            self._claim_changed.notify_all()

    async def release_claim(self, worker_key: str) -> None:
        async with self._claim_changed:
            claimed = self.claims.pop(worker_key, None)
            if claimed is not None:
                _row_number, row, sequence = claimed
                if sequence in self.store.pending_quantity_numbers(row):
                    self.work.appendleft(claimed)
            self._claim_changed.notify_all()


class WorkflowEngine:
    def __init__(
        self,
        page: Page | None,
        solver: CaptchaSolver | None,
        controls: RunControls,
        emit: Callable[[UiEvent], None],
        open_portal_page: Callable[[], Awaitable[Page]] | None = None,
        close_portal_page: Callable[[], Awaitable[None]] | None = None,
        payment_coordinator: PaymentCoordinator | None = None,
        focus_payment_page: Callable[[], Awaitable[None]] | None = None,
        parallel_runtime: ParallelBatchRuntime | None = None,
    ) -> None:
        self.page = page
        self.solver = solver
        self.controls = controls
        self.emit = emit
        self.open_portal_page = open_portal_page
        self.close_portal_page = close_portal_page
        self.payment_coordinator = payment_coordinator or default_payment_coordinator()
        self.focus_payment_page = focus_payment_page
        self.parallel_runtime = parallel_runtime
        self.store: CsvBatchStore | None = None
        self.current_row: dict[str, str] | None = None
        self.current_stage = Stage.IDLE
        self.browser_interrupted = False
        self.citizen_otp_resend_budget = CitizenOtpResendBudget()

    def _create_portal(self, page: Page, options: RunOptions) -> PortalAutomation:
        return PortalAutomation(
            page,
            self.solver,
            self.controls,
            self._on_stage,
            self.emit,
            SmsOtpClient(options.sms_server_url),
            options.sms_user_id,
            options.captcha_copy_mode,
            options.payment_trigger_url,
            options.payment_trigger_method,
            options.save_captcha_images,
            options.retry_egras_otp_once,
            self.payment_coordinator,
            self.focus_payment_page,
            self.citizen_otp_resend_budget,
            (
                self.parallel_runtime.citizen_login_lock_for(options.run_id)
                if self.parallel_runtime is not None
                else None
            ),
        )

    async def run(self, options: RunOptions) -> bool:
        if self.parallel_runtime is not None:
            return await self._run_parallel(options)
        self.citizen_otp_resend_budget = CitizenOtpResendBudget()
        self.store = CsvBatchStore(options.csv_path)
        self.store.load()
        await self._persist()
        self.emit(UiEvent("batch_update", data={"rows": self.store.summaries()}))

        if not options.article.strip():
            self.emit(UiEvent("fatal_error", "Choose or type an Article before starting the batch."))
            return False

        download_root = options.download_root or Path.home() / "Downloads"
        portal: PortalAutomation | None = None
        if not options.fresh_browser_per_unit:
            if (self.page is None or self.page.is_closed()) and self.open_portal_page is not None:
                self.page = await self.open_portal_page()
            if self.page is None:
                raise RuntimeError("No portal browser page available.")
            portal = self._create_portal(self.page, options)

        try:
            pending = list(self.store.pending_rows())
            if not pending:
                if portal is not None:
                    await portal.reset_to_start(credentials=options.credentials)
                self.emit(
                    UiEvent(
                        "run_completed",
                        "Recheck complete - all CSV rows are already complete. Existing results preserved.",
                    )
                )
                return True
            # Highlight the first work item immediately.  Citizen login may
            # pause for user input before per-row processing starts.
            first_row_number, first_row = pending[0]
            self.current_row = first_row
            self._publish_progress(first_row_number, first_row)
            # Sign in before validating individual CSV rows. This makes the
            # visible Citizen login/CAPTCHA flow available at batch start even
            # when a later row needs CSV corrections.
            if not options.fresh_browser_per_unit and portal is not None:
                await portal.ensure_citizen_session(options.credentials)

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
                    action = await self._handle_error(
                        portal,
                        row_number,
                        row,
                        error,
                        options.mode,
                        options.credentials,
                        options.fresh_browser_per_unit,
                    )
                    if action == "retry":
                        # Validate again in case the in-memory row is updated by a future editor.
                        validation = self.store.validate_row(row)
                        if validation:
                            continue
                    continue

                start_from_stage = Stage.CITIZEN_LOGIN
                while int(row["processed_quantity"]) < int(row["quantity"]):
                    self.current_stage = start_from_stage
                    self.store.set_running(row, self.current_stage)
                    await self._persist()
                    self._publish_progress(row_number, row)
                    sequence = self.store.next_pending_quantity(row)

                    if options.fresh_browser_per_unit:
                        if (
                            self.page is None
                            or self.page.is_closed()
                            or start_from_stage == Stage.CITIZEN_LOGIN
                        ):
                            if self.open_portal_page is not None:
                                self.page = await self.open_portal_page()
                            if self.page is None:
                                raise RuntimeError("No portal browser page available.")
                            portal = self._create_portal(self.page, options)
                    else:
                        if self.page is None or self.page.is_closed():
                            if self.open_portal_page is not None:
                                self.page = await self.open_portal_page()
                            if self.page is None:
                                raise RuntimeError("No portal browser page available.")
                            portal = self._create_portal(self.page, options)

                    if portal is None:
                        raise RuntimeError("No portal automation session available.")
                    try:
                        result = await portal.process_unit(
                            row,
                            options.article,
                            options.credentials,
                            download_root,
                            row_number + 1,
                            sequence,
                            start_from_stage=start_from_stage,
                        )
                        start_from_stage = Stage.CITIZEN_LOGIN
                        relative_path = _download_path_for_csv(
                            result.destination,
                            options.csv_path,
                        )
                        details = dict(result.details)
                        details["PDF status"] = "saved" if result.destination is not None else "failed"
                        details["PDF file"] = relative_path
                        details["PDF error"] = result.download_error
                        self.store.mark_success(row, result.reference, relative_path, details)
                        await self._persist()
                        self._publish_progress(row_number, row)
                        outcome = (
                            result.destination.name
                            if result.destination is not None
                            else "transaction recorded; PDF unavailable"
                        )
                        self.emit(
                            UiEvent(
                                "log",
                                f"Row {row_number + 1}, quantity {sequence} completed: {outcome}",
                            )
                        )
                        if options.fresh_browser_per_unit:
                            if self.close_portal_page is not None:
                                await self.close_portal_page()
                            self.page = None
                            portal = None
                        else:
                            try:
                                await portal.reset_to_start(credentials=options.credentials)
                            except AutomationError as reset_error:
                                self.emit(
                                    UiEvent(
                                        "log",
                                        "Transaction was recorded, but the portal could not reset: "
                                        f"{reset_error}",
                                        {"level": "error"},
                                    )
                                )
                                if reset_error.code == "browser_closed":
                                    raise WorkflowStopped from reset_error
                    except WorkflowStopped:
                        raise
                    except AutomationError as error:
                        action = await self._handle_error(
                            portal,
                            row_number,
                            row,
                            error,
                            options.mode,
                            options.credentials,
                            options.fresh_browser_per_unit,
                        )
                        if error.code == "browser_closed":
                            raise WorkflowStopped from error
                        if action == "retry":
                            start_from_stage = Stage.CITIZEN_LOGIN
                            continue
                        if action == "continue":
                            checkpoint_info = STAGE_CHECKPOINTS.get(error.stage)
                            start_from_stage = checkpoint_info[0] if checkpoint_info else Stage.CITIZEN_LOGIN
                            self.emit(
                                UiEvent(
                                    "log",
                                    f"Resuming automation from next checkpoint: {start_from_stage.value}",
                                )
                            )
                            continue
                        start_from_stage = Stage.CITIZEN_LOGIN
                        self.store.mark_skipped_quantity(
                            row,
                            sequence,
                            error.stage,
                            str(error),
                        )
                        await self._persist()
                        self._publish_progress(row_number, row)
                        self.emit(
                            UiEvent(
                                "log",
                                f"Row {row_number + 1}, quantity {sequence} skipped; "
                                "moving to the next quantity.",
                            )
                        )
                        continue

            self.current_row = None
            self.current_stage = Stage.IDLE
            if options.fresh_browser_per_unit:
                if self.close_portal_page is not None:
                    await self.close_portal_page()
                self.page = None
            elif portal is not None:
                await portal.reset_to_start(credentials=options.credentials)
            self.emit(UiEvent("batch_update", data={"rows": self.store.summaries()}))
            self.emit(
                UiEvent(
                    "run_completed",
                    "Batch pass finished. Skipped quantities and PDF issues are recorded in the CSV.",
                )
            )
            return True
        except (WorkflowStopped, asyncio.CancelledError):
            if self.current_row is not None and not self.browser_interrupted:
                if "closed" in self.controls.stop_reason.casefold():
                    self.browser_interrupted = True
                    self.store.mark_error(
                        self.current_row,
                        self.current_stage,
                        "Browser closed. This row can be retried.",
                    )
                else:
                    self.store.mark_stopped(
                        self.current_row,
                        self.current_stage,
                        self.controls.stop_reason,
                    )
                await self._persist(ignore_stop=True)
                self.emit(UiEvent("batch_update", data={"rows": self.store.summaries()}))
            self.current_row = None
            message = (
                "A browser was closed. The current row remains retryable."
                if self.browser_interrupted
                else f"{self.controls.stop_reason}. Current work remains retryable."
            )
            self.emit(
                UiEvent(
                    "run_stopped",
                    message,
                    {"browser_closed": self.browser_interrupted},
                )
            )
            return False

    async def _run_parallel(self, options: RunOptions) -> bool:
        runtime = self.parallel_runtime
        if runtime is None:
            raise RuntimeError("Parallel batch state is unavailable.")
        self.citizen_otp_resend_budget = CitizenOtpResendBudget()
        await runtime.initialize()
        self.store = runtime.store
        if options.worker_index == 0:
            self.emit(UiEvent("batch_update", data={"rows": self.store.summaries()}))

        if not options.article.strip():
            return False

        download_root = options.download_root or Path.home() / "Downloads"
        worker_key = options.worker_key or f"{options.run_id}:{options.worker_index}"
        portal: PortalAutomation | None = None
        if not options.fresh_browser_per_unit:
            if (self.page is None or self.page.is_closed()) and self.open_portal_page is not None:
                self.page = await self.open_portal_page()
            if self.page is None:
                raise RuntimeError("No portal browser page available.")
            portal = self._create_portal(self.page, options)

        try:
            if not runtime.initial_has_work:
                return True

            while True:
                await self.controls.checkpoint()
                claimed = await runtime.claim_next(worker_key)
                if claimed is None:
                    break
                row_number, row, sequence = claimed
                self.current_row = row
                self._publish_progress(row_number, row, sequence)

                validation = self.store.validate_row(row)
                if validation:
                    error = AutomationError(
                        "; ".join(validation),
                        stage=Stage.VALIDATING,
                        code="invalid_csv_row",
                    )
                    async with runtime.error_decision_lock(options.run_id):
                        await self._handle_error(
                            portal,
                            row_number,
                            row,
                            error,
                            options.mode,
                            options.credentials,
                            options.fresh_browser_per_unit,
                            sequence,
                        )
                    await runtime.complete_claim(worker_key)
                    self.current_row = None
                    continue

                start_from_stage = Stage.CITIZEN_LOGIN
                while True:
                    self.current_stage = start_from_stage
                    self.store.set_running(row, self.current_stage)
                    await self._persist()
                    self._publish_progress(row_number, row, sequence)

                    if options.fresh_browser_per_unit:
                        if (
                            self.page is None
                            or self.page.is_closed()
                            or start_from_stage == Stage.CITIZEN_LOGIN
                        ):
                            if self.open_portal_page is not None:
                                self.page = await self.open_portal_page()
                            if self.page is None:
                                raise RuntimeError("No portal browser page available.")
                            portal = self._create_portal(self.page, options)
                    elif self.page is None or self.page.is_closed():
                        if self.open_portal_page is not None:
                            self.page = await self.open_portal_page()
                        if self.page is None:
                            raise RuntimeError("No portal browser page available.")
                        portal = self._create_portal(self.page, options)

                    if portal is None:
                        raise RuntimeError("No portal automation session available.")
                    try:
                        result = await portal.process_unit(
                            row,
                            options.article,
                            options.credentials,
                            download_root,
                            row_number + 1,
                            sequence,
                            start_from_stage=start_from_stage,
                        )
                        relative_path = _download_path_for_csv(
                            result.destination,
                            options.csv_path,
                        )
                        details = dict(result.details)
                        details["PDF status"] = "saved" if result.destination is not None else "failed"
                        details["PDF file"] = relative_path
                        details["PDF error"] = result.download_error
                        self.store.mark_success(
                            row,
                            result.reference,
                            relative_path,
                            details,
                            quantity_number=sequence,
                        )
                        await self._persist()
                        self._publish_progress(row_number, row, sequence)
                        outcome = (
                            result.destination.name
                            if result.destination is not None
                            else "transaction recorded; PDF unavailable"
                        )
                        self.emit(
                            UiEvent(
                                "log",
                                f"Row {row_number + 1}, quantity {sequence} completed: {outcome}",
                            )
                        )
                        if options.fresh_browser_per_unit:
                            if self.close_portal_page is not None:
                                await self.close_portal_page()
                            self.page = None
                            portal = None
                        else:
                            try:
                                await portal.reset_to_start(credentials=options.credentials)
                            except AutomationError as reset_error:
                                self.emit(
                                    UiEvent(
                                        "log",
                                        "Transaction was recorded, but the portal could not reset: "
                                        f"{reset_error}",
                                        {"level": "error"},
                                    )
                                )
                                if reset_error.code == "browser_closed":
                                    raise WorkflowStopped from reset_error
                        break
                    except WorkflowStopped:
                        raise
                    except AutomationError as error:
                        async with runtime.error_decision_lock(options.run_id):
                            action = await self._handle_error(
                                portal,
                                row_number,
                                row,
                                error,
                                options.mode,
                                options.credentials,
                                options.fresh_browser_per_unit,
                                sequence,
                            )
                        if error.code == "browser_closed":
                            raise WorkflowStopped from error
                        if action == "retry":
                            start_from_stage = Stage.CITIZEN_LOGIN
                            continue
                        if action == "continue":
                            checkpoint_info = STAGE_CHECKPOINTS.get(error.stage)
                            start_from_stage = checkpoint_info[0] if checkpoint_info else Stage.CITIZEN_LOGIN
                            self.emit(
                                UiEvent(
                                    "log",
                                    f"Resuming automation from next checkpoint: {start_from_stage.value}",
                                )
                            )
                            continue
                        self.store.mark_skipped_quantity(
                            row,
                            sequence,
                            error.stage,
                            str(error),
                        )
                        await self._persist()
                        self._publish_progress(row_number, row, sequence)
                        self.emit(
                            UiEvent(
                                "log",
                                f"Row {row_number + 1}, quantity {sequence} skipped; "
                                "moving to the next quantity.",
                            )
                        )
                        break

                await runtime.complete_claim(worker_key)
                self.current_row = None

            self.current_row = None
            self.current_stage = Stage.IDLE
            if options.fresh_browser_per_unit:
                if self.close_portal_page is not None:
                    await self.close_portal_page()
                self.page = None
            elif portal is not None:
                await portal.reset_to_start(credentials=options.credentials)
            self.emit(UiEvent("batch_update", data={"rows": self.store.summaries()}))
            return True
        except (WorkflowStopped, asyncio.CancelledError):
            if self.current_row is not None and not self.browser_interrupted:
                if "closed" in self.controls.stop_reason.casefold():
                    self.browser_interrupted = True
                    self.store.mark_error(
                        self.current_row,
                        self.current_stage,
                        "Browser closed. This row can be retried.",
                    )
                else:
                    self.store.mark_stopped(
                        self.current_row,
                        self.current_stage,
                        self.controls.stop_reason,
                    )
                await self._persist(ignore_stop=True)
                self.emit(UiEvent("batch_update", data={"rows": self.store.summaries()}))
            await runtime.release_claim(worker_key)
            self.current_row = None
            return False
        except BaseException:
            await runtime.release_claim(worker_key)
            self.current_row = None
            raise

    async def _handle_error(
        self,
        portal: PortalAutomation | None,
        row_number: int,
        row: dict[str, str],
        error: AutomationError,
        mode: RunMode,
        credentials: Credentials | None = None,
        fresh_browser_per_unit: bool = False,
        quantity_number: int | None = None,
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
        self.emit(
            UiEvent(
                "notification",
                "An automation error occurred. Please look in the application for details.",
                {"title": "eStamp Automation error", "level": "error"},
            )
        )
        if error.code == "browser_closed":
            self.browser_interrupted = True
            return "next"

        if mode == RunMode.CONTINUOUS:
            if fresh_browser_per_unit:
                if self.close_portal_page is not None:
                    await self.close_portal_page()
                self.page = None
            elif portal is not None:
                try:
                    await portal.reset_to_start(record_stage=False, credentials=credentials)
                except Exception as reset_error:
                    self.emit(
                        UiEvent(
                            "log",
                            f"Could not reset the portal: {reset_error}",
                            {"level": "error"},
                        )
                    )
            return "next"

        checkpoint_info = STAGE_CHECKPOINTS.get(error.stage)
        can_continue = checkpoint_info is not None and error.stage != Stage.VALIDATING

        self.emit(
            UiEvent(
                "error_prompt",
                str(error),
                {
                    "row": row_number + 1,
                    "quantity": quantity_number or self.store_or_raise().next_pending_quantity(row),
                    "stage": error.stage.value,
                    "quantity_action": error.stage != Stage.VALIDATING,
                    "post_payment_warning": error.stage in {Stage.PAYMENT, Stage.RESULT, Stage.DOWNLOAD},
                    "can_continue": can_continue,
                    "next_checkpoint_stage": checkpoint_info[0].value if checkpoint_info else "",
                    "next_checkpoint_title": checkpoint_info[1] if checkpoint_info else "",
                    "next_checkpoint_instruction": checkpoint_info[2] if checkpoint_info else "",
                },
            )
        )
        decision = await self.controls.wait_for_decision()
        if decision in ("retry", "next"):
            if fresh_browser_per_unit:
                if self.close_portal_page is not None:
                    await self.close_portal_page()
                self.page = None
            elif portal is not None:
                try:
                    await portal.reset_to_start(record_stage=False, credentials=credentials)
                except Exception as reset_error:
                    self.emit(
                        UiEvent(
                            "log",
                            f"Could not reset the portal: {reset_error}",
                            {"level": "error"},
                        )
                    )

        return decision

    async def _on_stage(self, stage: Stage) -> None:
        self.current_stage = stage
        if self.current_row is not None:
            self.store_or_raise().set_stage(self.current_row, stage)
            await self._persist()
        self.emit(UiEvent("stage", stage.value))

    async def _persist(self, ignore_stop: bool = False) -> None:
        reported_block = False
        while True:
            try:
                if self.parallel_runtime is None:
                    self.store_or_raise().persist()
                else:
                    async with self.parallel_runtime.persist_lock:
                        self.store_or_raise().persist()
                return
            except PersistenceError as error:
                if ignore_stop:
                    self.emit(UiEvent("log", f"Final CSV save failed: {error}", {"level": "error"}))
                    return
                if not reported_block:
                    self.emit(UiEvent("persistence_blocked", str(error)))
                    reported_block = True
                await self.controls.checkpoint()
                await asyncio.sleep(1)

    def _publish_progress(
        self, row_number: int, row: dict[str, str], quantity_number: int | None = None
    ) -> None:
        store = self.store_or_raise()
        self.emit(
            UiEvent(
                "batch_update",
                data={
                    "rows": store.summaries(),
                    "current_row": row_number + 1,
                    "current_unit": quantity_number or store.next_pending_quantity(row),
                },
            )
        )

    def store_or_raise(self) -> CsvBatchStore:
        if self.store is None:
            raise RuntimeError("The CSV store is not loaded.")
        return self.store
