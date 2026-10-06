"""Registered-user finance transaction CSV export dialog."""

from __future__ import annotations

import asyncio
import queue
import re
import sqlite3
import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING
from uuid import uuid4

from tkcalendar import DateEntry  # type: ignore[import-untyped]

from core.config import app_data_directory, default_download_directory
from core.controls import RunControls
from core.models import CaptchaCopyMode, OcrEngine, WorkflowStopped
from services.estamp_transactions import parse_payment_amount
from services.finance_checkpoint import FINANCE_HEADERS, FinanceCheckpoint, FinancePageUpdate
from services.finance_transactions import (
    FinanceExportFilters,
    FinanceExportTarget,
    export_finance_checkpoint,
    fetch_finance_transactions_batch,
)

if TYPE_CHECKING:
    from ui.main_window import MainWindow
    from ui.run_tab import AutomationTab


class FinanceTransactionsDialog:
    def __init__(self, owner: MainWindow) -> None:
        self.owner = owner
        self.dialog = tk.Toplevel(owner.root)
        self.dialog.title("Fetch and download successful finance transactions")
        self.dialog.geometry("1100x880")
        self.dialog.minsize(860, 760)
        self.dialog.transient(owner.root)
        self.controls: RunControls | None = None
        self.is_running = False
        self.owns_export_slot = False
        self.close_after_stop = False
        self.events: queue.Queue[tuple[str, str | FinancePageUpdate]] = queue.Queue()
        self.checkpoint: FinanceCheckpoint | None = None
        self.can_download = False
        self.export_id = ""
        self.log_secrets: tuple[str, ...] = ()
        self.account_tabs: dict[str, AutomationTab] = {}
        self.account_vars: dict[str, tk.BooleanVar] = {}
        self.account_checks: list[ttk.Checkbutton] = []
        self.editable: list[ttk.Widget] = []
        self.name_var = tk.StringVar()
        self.amount_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.results_var = tk.StringVar(value="No transactions fetched yet")
        self.checkpoint_var = tk.StringVar(value="Each fetched page will be saved to a local checkpoint.")
        self.start_enabled = tk.BooleanVar(value=False)
        self.end_enabled = tk.BooleanVar(value=False)
        self._build_ui()
        self._refresh_accounts()
        self.dialog.protocol("WM_DELETE_WINDOW", self._close)
        self.poll_id = self.dialog.after(100, self._poll_events)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Jharkhand finance | Successful transactions", style="Heading.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text="Accounts fetch in parallel. Complete CAPTCHA or OTP in each browser if prompted.",
        ).pack(anchor="w", pady=(6, 10))

        accounts = ttk.LabelFrame(frame, text="Saved eGRAS accounts", padding=8)
        accounts.pack(fill="x")
        tools = ttk.Frame(accounts)
        tools.pack(fill="x")
        for label, command in (
            ("Select all", lambda: self._select_accounts(True)),
            ("Deselect all", lambda: self._select_accounts(False)),
            ("Refresh accounts", self._refresh_accounts),
        ):
            button = ttk.Button(tools, text=label, command=command)
            button.pack(side="left", padx=(0, 6))
            self.editable.append(button)
        self.account_summary = ttk.Label(tools)
        self.account_summary.pack(side="right")
        scroll_frame = ttk.Frame(accounts)
        scroll_frame.pack(fill="x", pady=(8, 0))
        canvas = tk.Canvas(scroll_frame, height=120, highlightthickness=0)
        canvas.pack(side="left", fill="x", expand=True)
        scroll = ttk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scroll.set)
        self.account_frame = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.account_frame, anchor="nw")
        self.account_frame.bind(
            "<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))

        filters = ttk.LabelFrame(frame, text="Optional filters", padding=8)
        filters.pack(fill="x", pady=10)
        filters.columnconfigure(1, weight=1)
        ttk.Label(filters, text="Remitter name contains").grid(row=0, column=0, sticky="w")
        name = ttk.Entry(filters, textvariable=self.name_var)
        name.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(filters, text="Amount equals").grid(row=1, column=0, sticky="w", pady=6)
        amount = ttk.Entry(filters, textvariable=self.amount_var)
        amount.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        self.editable.extend([name, amount])
        dates = ttk.Frame(filters)
        dates.grid(row=2, column=0, columnspan=2, sticky="w")
        start = ttk.Checkbutton(
            dates, text="Start entry date", variable=self.start_enabled, command=self._update_dates
        )
        start.pack(side="left")
        self.start_date = DateEntry(dates, date_pattern="yyyy-mm-dd", state="disabled", width=12)
        self.start_date.pack(side="left", padx=(6, 18))
        end = ttk.Checkbutton(
            dates, text="End entry date", variable=self.end_enabled, command=self._update_dates
        )
        end.pack(side="left")
        self.end_date = DateEntry(dates, date_pattern="yyyy-mm-dd", state="disabled", width=12)
        self.end_date.pack(side="left", padx=6)
        self.editable.extend([start, end])
        ttk.Label(
            filters,
            text="Names ignore case. Dates are inclusive. Stops at the first entry older than start.",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=10)
        self.start_button = ttk.Button(actions, text="Fetch", command=self._start)
        self.start_button.pack(side="left")
        self.download_button = ttk.Button(
            actions, text="Download CSV", command=self._download, state="disabled"
        )
        self.download_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(actions, text="Stop", command=self._stop, state="disabled")
        self.stop_button.pack(side="left")
        restore = ttk.Button(actions, text="Open checkpoint...", command=self._open_checkpoint)
        restore.pack(side="right")
        self.editable.append(restore)
        ttk.Label(frame, textvariable=self.checkpoint_var, wraplength=1040).pack(anchor="w")
        ttk.Label(
            frame,
            text="Download uses the saved results. Change filters and Fetch again to get different results.",
        ).pack(anchor="w", pady=(4, 0))

        results = ttk.LabelFrame(frame, text="Fetched transactions", padding=6)
        results.pack(fill="both", expand=True, pady=8)
        ttk.Label(results, textvariable=self.results_var).pack(anchor="w", pady=(0, 4))
        table_frame = ttk.Frame(results)
        table_frame.pack(fill="both", expand=True)
        columns = [*FINANCE_HEADERS, "eGRAS Username"]
        self.results = ttk.Treeview(table_frame, columns=columns, show="headings", height=7)
        for column in columns:
            self.results.heading(column, text=column)
            self.results.column(column, width=130, minwidth=80, stretch=False)
        self.results.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.results.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.results.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.results.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        ttk.Label(frame, textvariable=self.status_var, wraplength=1040).pack(anchor="w")
        self.log = tk.Text(frame, height=4, state="disabled", wrap="word")
        self.log.pack(fill="x", pady=(8, 0))

    def _refresh_accounts(self) -> None:
        if self.is_running:
            return
        previous = {key: var.get() for key, var in self.account_vars.items()}
        for child in self.account_frame.winfo_children():
            child.destroy()
        self.account_tabs.clear()
        self.account_vars.clear()
        self.account_checks.clear()
        for tab in sorted(self.owner.tabs.values(), key=lambda item: item.tab_id):
            credentials = tab.entered_credentials()
            key = credentials.egras_username.strip().lower()
            if key and (
                key not in self.account_tabs
                or not self.account_tabs[key].entered_credentials().egras_password
            ):
                self.account_tabs[key] = tab
        for index, (key, tab) in enumerate(self.account_tabs.items()):
            var = tk.BooleanVar(value=previous.get(key, True))
            self.account_vars[key] = var
            check = ttk.Checkbutton(
                self.account_frame,
                text=f"{tab.tab_id}. {tab.entered_credentials().egras_username.strip()}",
                variable=var,
                command=self._update_summary,
            )
            check.grid(row=index // 2, column=index % 2, sticky="w", padx=6, pady=2)
            self.account_checks.append(check)
        if not self.account_tabs:
            ttk.Label(self.account_frame, text="Add eGRAS credentials in Configure IDs, then refresh.").pack(
                anchor="w"
            )
        self._update_summary()

    def _select_accounts(self, selected: bool) -> None:
        for var in self.account_vars.values():
            var.set(selected)
        self._update_summary()

    def _update_summary(self) -> None:
        selected = sum(var.get() for var in self.account_vars.values())
        self.account_summary.configure(text=f"{selected} of {len(self.account_vars)} selected")

    def _update_dates(self) -> None:
        self.start_date.configure(
            state="readonly" if self.start_enabled.get() and not self.is_running else "disabled"
        )
        self.end_date.configure(
            state="readonly" if self.end_enabled.get() and not self.is_running else "disabled"
        )

    def _start(self) -> None:
        if self.is_running:
            return
        if self.owner.transaction_export_running:
            messagebox.showwarning(
                "Finance CSV", "Another transaction export is running.", parent=self.dialog
            )
            return
        keys = [key for key, var in self.account_vars.items() if var.get()]
        if not keys:
            messagebox.showwarning("Finance CSV", "Select at least one eGRAS account.", parent=self.dialog)
            return
        try:
            amount_text = self.amount_var.get().strip()
            amount = parse_payment_amount(amount_text) if amount_text else None
            if amount_text and amount is None:
                raise ValueError("Amount must be a valid number, such as 10 or 1,000.00.")
            filters = FinanceExportFilters(
                name=self.name_var.get().strip().lower(),
                date_from=date.fromisoformat(self.start_date.get()) if self.start_enabled.get() else None,
                date_to=date.fromisoformat(self.end_date.get()) if self.end_enabled.get() else None,
                amount=amount,
            )
            filters.validate()
        except ValueError as error:
            messagebox.showwarning("Finance CSV filters", str(error), parent=self.dialog)
            return
        browser = self.owner.selected_portal_browser()
        if browser is None:
            messagebox.showwarning("Finance CSV", "Select an installed portal browser.", parent=self.dialog)
            return
        targets: list[FinanceExportTarget] = []
        try:
            solver = (
                self.owner.controller.ocr_solver_for_external_loop(OcrEngine(self.owner.ocr_engine_var.get()))
                if self.owner.ocr_enabled_var.get()
                else None
            )
        except Exception:
            solver = None
        browser_slug = re.sub(r"[^a-z0-9]+", "-", browser.name.lower()).strip("-") or "browser"
        for key in keys:
            tab = self.account_tabs[key]
            credentials = tab.entered_credentials()
            if credentials.egras_username.strip().lower() != key:
                messagebox.showwarning(
                    "Finance CSV", "Credentials changed. Refresh the account list.", parent=self.dialog
                )
                return
            targets.append(
                FinanceExportTarget(
                    name=credentials.egras_username.strip(),
                    browser=browser,
                    profile_path=app_data_directory()
                    / "finance-export-profiles"
                    / f"id-{tab.tab_id}"
                    / browser.engine.value
                    / browser_slug,
                    credentials=credentials,
                    sms_server_url=self.owner.sms_server_url_var.get().strip(),
                    solver=solver,
                    captcha_copy_mode=CaptchaCopyMode(self.owner.captcha_copy_mode_var.get()),
                    save_captcha_images=self.owner.save_captcha_images_var.get(),
                )
            )
        export_id = uuid4().hex[:12]
        filter_description = (
            f"name_contains={filters.name!r}, start_entry_date={filters.date_from}, "
            f"end_entry_date={filters.date_to}, amount={filters.amount}"
        )
        try:
            checkpoint = FinanceCheckpoint.create(
                app_data_directory()
                / "finance-checkpoints"
                / f"finance_{datetime.now():%Y%m%d_%H%M%S}_{export_id}.sqlite3",
                [target.credentials.egras_username.strip() for target in targets],
                filter_description,
            )
        except (OSError, sqlite3.Error) as error:
            messagebox.showerror("Finance fetch", f"Cannot create a checkpoint: {error}", parent=self.dialog)
            return
        self.checkpoint = checkpoint
        self.can_download = False
        self._clear_results()
        self.results_var.set("Fetching transactions. Each page appears here after it is saved.")
        self.checkpoint_var.set(f"Checkpoint: {checkpoint.path}")
        self.controls = RunControls(lambda _event: None)
        controls = self.controls
        self._set_running(True)
        self.status_var.set("Opening finance portal...")
        self.export_id = export_id
        self.log_secrets = tuple(
            target.credentials.egras_password for target in targets if target.credentials.egras_password
        )
        self._publish_event(
            "started",
            f"Starting parallel finance fetch: accounts={[target.name for target in targets]}, "
            f"browser={browser.name}, {filter_description}, checkpoint={checkpoint.path}",
            display_kind="status",
        )

        def run_worker() -> None:
            try:
                summary = asyncio.run(
                    fetch_finance_transactions_batch(
                        targets,
                        checkpoint,
                        filters,
                        lambda message: self._publish_event("status", message),
                        controls,
                        lambda update: self.events.put(("page", update)),
                    )
                )
                failures = [f"{item.account}: {item.error}" for item in summary.accounts if item.error]
                message = (
                    f"Fetched {summary.row_count} unique successful transaction(s). "
                    "Click Download CSV to save them."
                )
                if failures:
                    self._publish_event(
                        "warning" if summary.can_download else "error",
                        message + "\n\nAccounts that failed:\n" + "\n".join(failures),
                    )
                else:
                    self._publish_event("success", message)
            except WorkflowStopped:
                self._publish_event(
                    "stopped",
                    "Fetch stopped. Saved pages remain available for Download CSV or Open checkpoint.",
                )
            except Exception as error:
                self._publish_event("error", str(error))

        threading.Thread(target=run_worker, name="finance-fetch", daemon=True).start()

    def _clear_results(self) -> None:
        children = self.results.get_children()
        if children:
            self.results.delete(*children)

    def _open_checkpoint(self) -> None:
        if self.is_running:
            return
        selected = filedialog.askopenfilename(
            parent=self.dialog,
            title="Open saved finance transactions",
            initialdir=app_data_directory() / "finance-checkpoints",
            filetypes=[("Finance checkpoints", "*.sqlite3")],
        )
        if not selected:
            return
        try:
            checkpoint = FinanceCheckpoint(Path(selected))
            info = checkpoint.info()
            rows = checkpoint.read_rows()
        except (OSError, sqlite3.Error, ValueError) as error:
            messagebox.showerror(
                "Finance checkpoint", f"Cannot open this checkpoint: {error}", parent=self.dialog
            )
            return
        self.checkpoint = checkpoint
        self.can_download = info.can_download
        self._clear_results()
        for row in rows:
            self.results.insert("", "end", values=row)
        self.checkpoint_var.set(f"Checkpoint: {checkpoint.path}")
        self.results_var.set(f"{info.row_count} saved transaction(s). Original filters: {info.filters}")
        incomplete = [account.account for account in info.accounts if account.status != "completed"]
        message = f"Opened {info.row_count} saved transaction(s)."
        if incomplete:
            message += f" Partial fetch for: {', '.join(incomplete)}."
        self.export_id = checkpoint.path.stem
        self.log_secrets = ()
        self._publish_event("checkpoint_opened", message, display_kind="status")
        self._set_running(False)

    def _download(self) -> None:
        if self.is_running or self.checkpoint is None or not self.can_download:
            return
        if self.owner.transaction_export_running:
            messagebox.showwarning(
                "Finance CSV", "Another transaction export is running.", parent=self.dialog
            )
            return
        selected = filedialog.askdirectory(
            parent=self.dialog,
            title="Choose a folder for the finance CSV",
            initialdir=default_download_directory(),
            mustexist=True,
        )
        if not selected:
            return
        try:
            output_path = Path(selected).resolve() / f"finance_success_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showwarning("Finance CSV", f"Cannot use this path: {error}", parent=self.dialog)
            return
        if output_path.exists() and not messagebox.askyesno(
            "Replace CSV?", f"Replace this CSV?\n{output_path}", parent=self.dialog
        ):
            return
        checkpoint = self.checkpoint
        self.controls = None
        self._set_running(True)
        self.status_var.set("Saving the fetched results to CSV...")

        def save_worker() -> None:
            try:
                count = export_finance_checkpoint(checkpoint, output_path)
                self._publish_event("success", f"Saved {count} transaction(s) to:\n{output_path}")
            except Exception as error:
                self._publish_event("error", f"Cannot save CSV: {error}. The checkpoint is unchanged.")

        threading.Thread(target=save_worker, name="finance-csv-download", daemon=True).start()

    def _publish_event(self, kind: str, message: str, *, display_kind: str | None = None) -> None:
        # Persist in the worker before queuing the UI update, so closing the dialog
        # cannot discard the last progress message or error.
        for secret in sorted(self.log_secrets, key=len, reverse=True):
            message = message.replace(secret, "[redacted]")
        # Playwright failures can include fill("value") in their call log, including OTPs.
        message = re.sub(
            r"\bfill\((?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')\)",
            "fill('[redacted]')",
            message,
        )
        level = "ERROR" if kind == "error" else "WARNING" if kind == "warning" else "INFO"
        try:
            self.owner.controller.activity_log.write(
                f"finance_export_{kind}", message, level=level, data={"export_id": self.export_id}
            )
        except OSError as error:
            self.events.put(("status", f"Could not save the finance export log: {error}"))
        self.events.put((display_kind or kind, message))

    def _set_running(self, running: bool) -> None:
        self.is_running = running
        if running:
            self.owner.transaction_export_running = True
            self.owns_export_slot = True
        elif self.owns_export_slot:
            self.owner.transaction_export_running = False
            self.owns_export_slot = False
        self.start_button.configure(state="disabled" if running else "normal")
        self.download_button.configure(state="normal" if self.can_download and not running else "disabled")
        self.stop_button.configure(state="normal" if running and self.controls is not None else "disabled")
        for widget in [*self.editable, *self.account_checks]:
            widget.configure({"state": "disabled" if running else "normal"})
        self._update_dates()

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if isinstance(payload, FinancePageUpdate):
                for row in payload.rows:
                    self.results.insert("", "end", values=row)
                self.results_var.set(
                    f"{payload.total_rows} unique transaction(s) saved. "
                    f"Latest checkpoint: {payload.account}, page {payload.page}."
                )
                continue
            message = payload
            self.status_var.set(message)
            self.owner.append_session_log(message)
            self.log.configure(state="normal")
            self.log.insert("end", message + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
            if kind != "status":
                if self.checkpoint is not None:
                    try:
                        self.can_download = self.checkpoint.info().can_download
                    except (OSError, sqlite3.Error, ValueError):
                        self.can_download = False
                self.controls = None
                self._set_running(False)
                if self.close_after_stop:
                    self.dialog.destroy()
                    return
                if kind == "success":
                    messagebox.showinfo("Finance CSV", message, parent=self.dialog)
                elif kind == "warning":
                    messagebox.showwarning("Finance CSV", message, parent=self.dialog)
                elif kind == "error":
                    messagebox.showerror("Finance CSV", message, parent=self.dialog)
        self.poll_id = self.dialog.after(100, self._poll_events)

    def _stop(self) -> None:
        if self.controls is not None:
            self.controls.stop("Finance export stopped by user")
        self._publish_event("stop_requested", "Finance fetch stop requested.", display_kind="status")
        self.status_var.set("Stopping fetch and closing its browsers. Saved pages will be kept.")
        self.stop_button.configure(state="disabled")

    def _close(self) -> None:
        if self.is_running:
            self.close_after_stop = True
            if self.controls is not None:
                self._stop()
        else:
            self.dialog.after_cancel(self.poll_id)
            self.dialog.destroy()
