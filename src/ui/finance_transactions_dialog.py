"""Registered-user finance transaction CSV export dialog."""

from __future__ import annotations

import asyncio
import queue
import re
import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from tkcalendar import DateEntry  # type: ignore[import-untyped]

from core.config import app_data_directory, default_download_directory
from core.controls import RunControls
from core.models import OcrEngine, WorkflowStopped
from services.estamp_transactions import parse_payment_amount
from services.finance_transactions import (
    FinanceExportFilters,
    FinanceExportTarget,
    export_finance_transactions_batch,
)

if TYPE_CHECKING:
    from ui.main_window import MainWindow
    from ui.run_tab import AutomationTab


class FinanceTransactionsDialog:
    def __init__(self, owner: MainWindow) -> None:
        self.owner = owner
        self.dialog = tk.Toplevel(owner.root)
        self.dialog.title("Download successful finance transactions")
        self.dialog.geometry("840x700")
        self.dialog.minsize(740, 620)
        self.dialog.transient(owner.root)
        self.controls: RunControls | None = None
        self.is_running = False
        self.close_after_stop = False
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.account_tabs: dict[str, AutomationTab] = {}
        self.account_vars: dict[str, tk.BooleanVar] = {}
        self.account_checks: list[ttk.Checkbutton] = []
        self.editable: list[ttk.Widget] = []
        self.name_var = tk.StringVar()
        self.amount_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.confirmed_destination = ""
        self.status_var = tk.StringVar(value="Ready")
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
            text="Uses each eGRAS username once. Complete CAPTCHA or OTP in the browser if prompted.",
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

        destination = ttk.Frame(frame)
        destination.pack(fill="x")
        ttk.Label(destination, text="Save CSV to").pack(side="left")
        path = ttk.Entry(destination, textvariable=self.path_var)
        path.pack(side="left", fill="x", expand=True, padx=8)
        browse = ttk.Button(destination, text="Browse...", command=self._browse)
        browse.pack(side="left")
        self.editable.extend([path, browse])

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=10)
        self.start_button = ttk.Button(actions, text="Download CSV", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(frame, textvariable=self.status_var, wraplength=780).pack(anchor="w")
        self.log = tk.Text(frame, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(8, 0))

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

    def _browse(self) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self.dialog,
            title="Save successful finance transactions",
            initialdir=default_download_directory(),
            initialfile=f"finance_success_{datetime.now():%Y%m%d_%H%M%S}.csv",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if selected:
            self.path_var.set(selected)
            self.confirmed_destination = selected

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
        if not self.path_var.get().strip():
            self._browse()
        if not self.path_var.get().strip():
            return
        try:
            output_path = Path(self.path_var.get().strip()).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showwarning("Finance CSV", f"Cannot use this path: {error}", parent=self.dialog)
            return
        if output_path.suffix.lower() != ".csv":
            messagebox.showwarning("Finance CSV", "Choose a CSV file path.", parent=self.dialog)
            return
        # A manually typed existing path needs the same overwrite confirmation as the save picker.
        if (
            output_path.exists()
            and self.path_var.get().strip() != self.confirmed_destination
            and not messagebox.askyesno(
                "Replace CSV?",
                f"Replace this CSV after the export completes?\n{output_path}",
                parent=self.dialog,
            )
        ):
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
                )
            )
        self.controls = RunControls(lambda _event: None)
        controls = self.controls
        self._set_running(True)
        self.status_var.set("Opening finance portal...")

        def run_worker() -> None:
            try:
                summary = asyncio.run(
                    export_finance_transactions_batch(
                        targets,
                        output_path,
                        filters,
                        lambda message: self.events.put(("status", message)),
                        controls,
                    )
                )
                failures = [f"{item.target_name}: {item.error}" for item in summary.results if item.error]
                message = f"Saved {summary.appended_rows} successful transaction(s) to:\n{output_path}"
                if failures:
                    self.events.put(
                        ("warning", message + "\n\nAccounts that failed:\n" + "\n".join(failures))
                    )
                else:
                    self.events.put(("success", message))
            except WorkflowStopped:
                self.events.put(("stopped", "Export stopped. The destination file was not replaced."))
            except Exception as error:
                self.events.put(("error", str(error)))

        threading.Thread(target=run_worker, name="finance-csv-export", daemon=True).start()

    def _set_running(self, running: bool) -> None:
        self.is_running = running
        self.owner.transaction_export_running = running
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        for widget in [*self.editable, *self.account_checks]:
            widget.configure({"state": "disabled" if running else "normal"})
        self._update_dates()

    def _poll_events(self) -> None:
        while True:
            try:
                kind, message = self.events.get_nowait()
            except queue.Empty:
                break
            self.status_var.set(message)
            self.owner.append_session_log(message)
            self.log.configure(state="normal")
            self.log.insert("end", message + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
            if kind != "status":
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
        self.status_var.set("Stopping export and closing its browser...")
        self.stop_button.configure(state="disabled")

    def _close(self) -> None:
        if self.is_running:
            self.close_after_stop = True
            self._stop()
        else:
            self.dialog.after_cancel(self.poll_id)
            self.dialog.destroy()
