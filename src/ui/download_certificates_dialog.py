"""Unified dialog for Downloading Undownloaded Certificates.

Presents a 2-column layout with clear headings:
- Left column (Mode A): Export eStamp Payment Transactions (automatic/manual Citizen login)
- Right column (Mode B): Compare Transaction CSV with Downloaded Stamps
"""

from __future__ import annotations

import asyncio
import datetime
import re
import shutil
import threading
import tkinter as tk
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from tkcalendar import DateEntry

from core.config import DEFAULT_SMS_SERVER_URL, app_data_directory, default_download_directory
from core.controls import RunControls
from core.models import BrowserEngine, CaptchaCopyMode, Credentials, OcrEngine, PortalBrowser
from services.captcha_ocr import CaptchaSolver
from services.estamp_transactions import (
    TransactionExportSummary,
    TransactionExportTarget,
    download_missing_stamps,
    export_payment_transactions_batch,
)
from services.transaction_reconciliation import (
    TransactionReconciliation,
    reconcile_transactions,
    write_reconciliation_csv,
    write_reconciliation_text,
)

if TYPE_CHECKING:
    from ui.main_window import MainWindow
    from ui.run_tab import AutomationTab


@dataclass
class IdSelectionItem:
    tab_id: int
    display_name: str
    citizen_username: str
    citizen_password: str
    sms_user_id: str
    sms_server_url: str
    browser: PortalBrowser | None
    profile_path: Path
    window_accent: str
    ocr_engine: OcrEngine
    ocr_enabled: bool
    captcha_copy_mode: CaptchaCopyMode
    var: tk.BooleanVar


class DownloadUndownloadedCertificatesDialog:
    """Fetch or load transactions, compare them, then recover missing eStamps."""

    def __init__(self, owner: MainWindow, initial_tab: int = 0) -> None:
        self.owner = owner
        self.dialog = tk.Toplevel(owner.root)
        self.dialog.title("Find and Download Missing eStamps")
        self.dialog.geometry("980x760")
        self.dialog.minsize(860, 620)
        self.dialog.transient(owner.root)

        self.is_running = False
        self.current_controls: RunControls | None = None
        self.is_downloading_stamps = False
        self.download_missing_controls: RunControls | None = None
        self.last_reconciliation: TransactionReconciliation | None = None
        self.fetched_csv_path: Path | None = None

        self.export_date_from_var = tk.StringVar()
        self.export_date_to_var = tk.StringVar()
        self.export_date_filter_enabled_var = tk.BooleanVar(value=False)
        self.export_name_filter_var = tk.StringVar()
        self.export_date_entries: list[DateEntry] = []
        self.use_chrome_for_all_var = tk.BooleanVar(value=True)
        self.selection_summary_var = tk.StringVar()
        self.export_status_var = tk.StringVar(value="Ready")
        self.source_mode_var = tk.StringVar(value="fetch")

        self.reconcile_csv_var = tk.StringVar(
            value=self.owner.config.transaction_export_path.strip()
        )
        active_download_path = self._default_download_folder()
        self.reconcile_folder_var = tk.StringVar(value=active_download_path)
        self.reconcile_status_var = tk.StringVar(value="Select files and click Compare.")

        self.id_items: list[IdSelectionItem] = []

        self._build_ui()
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_close)

    def _default_download_folder(self) -> str:
        tab = self.owner._selected_tab()
        if tab is not None and tab.download_var.get().strip():
            return tab.download_var.get().strip()
        for t in self.owner.tabs.values():
            if t.download_var.get().strip():
                return t.download_var.get().strip()
        return str(default_download_directory())

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self.dialog, padding=12)
        main_frame.pack(fill="both", expand=True)
        source_row = ttk.Frame(main_frame)
        source_row.pack(fill="x", pady=(0, 10))
        ttk.Label(source_row, text="Find and download missing eStamps", style="Heading.TLabel").pack(
            side="left", padx=(0, 24)
        )
        ttk.Label(source_row, text="Transaction source:").pack(side="left")
        ttk.Radiobutton(
            source_row,
            text="Fetch from Citizen IDs",
            value="fetch",
            variable=self.source_mode_var,
            command=self._update_source_mode,
        ).pack(side="left", padx=(10, 0))
        ttk.Radiobutton(
            source_row,
            text="Use existing CSV",
            value="existing",
            variable=self.source_mode_var,
            command=self._update_source_mode,
        ).pack(side="left", padx=(12, 0))

        self.fetch_frame = ttk.Frame(main_frame)
        self.fetch_frame.pack(fill="x")
        self._build_fetch_section(self.fetch_frame)

        self.existing_frame = ttk.LabelFrame(
            main_frame, text="3. Existing transactions CSV", padding=8
        )
        existing_row = ttk.Frame(self.existing_frame)
        existing_row.pack(fill="x")
        ttk.Label(existing_row, text="Transactions CSV:").pack(side="left")
        ttk.Entry(existing_row, textvariable=self.reconcile_csv_var).pack(
            side="left", fill="x", expand=True, padx=(8, 6)
        )
        ttk.Button(existing_row, text="Browse...", command=self._browse_reconcile_csv).pack(
            side="left"
        )
        existing_folder_row = ttk.Frame(self.existing_frame)
        existing_folder_row.pack(fill="x", pady=(8, 0))
        ttk.Label(existing_folder_row, text="eStamp PDF folder:").pack(side="left")
        ttk.Entry(existing_folder_row, textvariable=self.reconcile_folder_var).pack(
            side="left", fill="x", expand=True, padx=(8, 6)
        )
        ttk.Button(
            existing_folder_row, text="Browse...", command=self._browse_reconcile_folder
        ).pack(side="left")

        self.action_bar = ttk.Frame(main_frame)
        self.action_bar.pack(fill="x", pady=(0, 10))
        self.start_btn = ttk.Button(
            self.action_bar,
            text="Fetch, compare and find missing PDFs",
            style="Accent.TButton",
            command=self._start_primary_action,
        )
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(
            self.action_bar, text="Stop", command=self._stop_export, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(8, 0))
        ttk.Button(self.action_bar, text="Close", command=self._on_close).pack(side="right")

        self._build_results_section(main_frame)
        self._update_source_mode()

    # ──────────────────────────────────────────────────────────────────────────
    # Fetch transactions from the selected Citizen IDs.
    # ──────────────────────────────────────────────────────────────────────────

    def _build_fetch_section(self, parent: ttk.Frame) -> None:
        csv_group = ttk.LabelFrame(parent, text="3. Transactions to fetch", padding=8)
        csv_group.pack(fill="x", pady=(0, 6))

        folder_row = ttk.Frame(csv_group)
        folder_row.pack(fill="x", pady=(0, 10))
        ttk.Label(folder_row, text="eStamp PDF folder:").pack(side="left")
        ttk.Entry(folder_row, textvariable=self.reconcile_folder_var).pack(
            side="left", fill="x", expand=True, padx=(8, 6)
        )
        ttk.Button(folder_row, text="Browse...", command=self._browse_reconcile_folder).pack(
            side="left"
        )

        date_filter = ttk.Frame(csv_group)
        date_filter.pack(fill="x")
        ttk.Checkbutton(
            date_filter,
            text="Filter by payment date",
            variable=self.export_date_filter_enabled_var,
            command=self._update_date_filter_state,
        ).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(date_filter, text="From").grid(row=0, column=1, sticky="w", padx=(12, 0))
        from_date = DateEntry(
            date_filter,
            textvariable=self.export_date_from_var,
            date_pattern="yyyy-mm-dd",
            width=12,
            state="readonly",
        )
        from_date.grid(
            row=0, column=2, sticky="w", padx=(6, 12)
        )
        ttk.Label(date_filter, text="To").grid(row=0, column=3, sticky="w")
        to_date = DateEntry(
            date_filter,
            textvariable=self.export_date_to_var,
            date_pattern="yyyy-mm-dd",
            width=12,
            state="readonly",
        )
        to_date.grid(
            row=0, column=4, sticky="w", padx=(6, 12)
        )
        ttk.Label(date_filter, text="Name contains").grid(row=0, column=5, sticky="w")
        ttk.Entry(date_filter, textvariable=self.export_name_filter_var, width=26).grid(
            row=0, column=6, sticky="ew", padx=(6, 0)
        )
        date_filter.columnconfigure(6, weight=1)
        self.export_date_entries = [from_date, to_date]
        self._update_date_filter_state()

        ids_group = ttk.LabelFrame(parent, text="Select Citizen IDs", padding=8)
        ids_group.pack(fill="x", pady=(0, 6))

        info_label = ttk.Label(
            ids_group,
            text="Select none to sign in manually in the browser.",
            foreground="#555555",
            wraplength=480,
        )
        info_label.pack(anchor="w", pady=(0, 4))

        # Selection Toolbar
        tools_frame = ttk.Frame(ids_group)
        tools_frame.pack(fill="x", pady=(0, 4))
        ttk.Button(tools_frame, text="Select All", command=self._select_all_ids).pack(side="left")
        ttk.Button(tools_frame, text="Deselect All", command=self._deselect_all_ids).pack(
            side="left", padx=(6, 0)
        )
        ttk.Checkbutton(
            tools_frame,
            text="Use Chrome For All",
            variable=self.use_chrome_for_all_var,
        ).pack(side="left", padx=(10, 0))
        ttk.Label(
            tools_frame,
            textvariable=self.selection_summary_var,
            font=("Segoe UI", 8, "bold"),
            foreground="#2563eb",
        ).pack(side="right")

        # Scrollable checklist
        list_container = ttk.Frame(ids_group)
        list_container.pack(fill="x")

        canvas = tk.Canvas(list_container, borderwidth=0, highlightthickness=0, height=82)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width)
        )
        canvas.configure(yscrollcommand=scrollbar.set)

        def _on_mousewheel(event: tk.Event[Any]) -> None:
            if event.delta:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._populate_id_items(scrollable_frame)


    def _populate_id_items(self, parent: ttk.Frame) -> None:
        self.id_items.clear()
        seen_usernames: set[str] = set()
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=1)

        sorted_tabs = sorted(self.owner.tabs.values(), key=lambda t: t.tab_id)
        visible_index = 0
        for tab in sorted_tabs:
            creds = tab.entered_credentials()
            citizen_user = creds.citizen_username.strip()
            citizen_pwd = creds.citizen_password
            user_key = citizen_user.casefold()

            # Deduplicate by unique citizen username if provided
            if user_key:
                if user_key in seen_usernames:
                    continue
                seen_usernames.add(user_key)

            browser = tab._selected_browser() or self._first_available_browser()
            profile_slug = (
                re.sub(r"[^a-z0-9]+", "-", browser.name.casefold()).strip("-") or "browser"
                if browser is not None
                else "default"
            )
            engine_str = browser.engine.value if browser is not None else "chromium"
            profile_path = Path(tab.config.portal_profile_path) / engine_str / profile_slug

            var = tk.BooleanVar(value=True)  # Default all checked!
            item = IdSelectionItem(
                tab_id=tab.tab_id,
                display_name=tab.display_name,
                citizen_username=citizen_user,
                citizen_password=citizen_pwd,
                sms_user_id=tab.sms_user_id_var.get().strip() or tab.config.sms_user_id,
                sms_server_url=(
                    tab.sms_server_url_var.get().strip()
                    or tab.config.sms_server_url
                    or DEFAULT_SMS_SERVER_URL
                ),
                browser=browser,
                profile_path=profile_path,
                window_accent=self.owner.tab_accent_color(tab.tab_id),
                ocr_engine=OcrEngine(tab.ocr_engine_var.get()),
                ocr_enabled=tab.ocr_enabled_var.get(),
                captcha_copy_mode=CaptchaCopyMode(tab.captcha_copy_mode_var.get()),
                var=var,
            )
            self.id_items.append(item)

            row_frame = ttk.Frame(parent)
            row_frame.grid(
                row=visible_index // 3,
                column=visible_index % 3,
                sticky="ew",
                pady=2,
                padx=4,
            )
            visible_index += 1

            chk = ttk.Checkbutton(
                row_frame,
                variable=var,
                command=self._update_selection_summary,
            )
            chk.pack(side="left", padx=(0, 6))

            label_text = f"{tab.tab_id}. {citizen_user or 'No username configured'}"
            ttk.Label(row_frame, text=label_text).pack(side="left")

        self._update_selection_summary()

    def _first_available_browser(self) -> PortalBrowser | None:
        if self.owner.portal_browsers:
            return next(iter(self.owner.portal_browsers.values()))
        return None

    def _get_chrome_browser(self) -> PortalBrowser | None:
        for name, browser in self.owner.portal_browsers.items():
            if "chrome" in name.casefold() and browser.engine == BrowserEngine.CHROMIUM:
                return browser
        if (
            getattr(self.owner.config, "chrome_executable", "")
            and Path(self.owner.config.chrome_executable).is_file()
        ):
            return PortalBrowser(
                "Google Chrome",
                Path(self.owner.config.chrome_executable),
                BrowserEngine.CHROMIUM,
            )
        return None

    def _select_all_ids(self) -> None:
        for item in self.id_items:
            item.var.set(True)
        self._update_selection_summary()

    def _deselect_all_ids(self) -> None:
        for item in self.id_items:
            item.var.set(False)
        self._update_selection_summary()

    def _update_selection_summary(self) -> None:
        selected_count = sum(1 for item in self.id_items if item.var.get())
        total_count = len(self.id_items)
        if selected_count == 0:
            self.selection_summary_var.set("0 IDs selected (Manual Citizen login mode)")
        else:
            self.selection_summary_var.set(
                f"{selected_count} of {total_count} IDs selected (Automatic login mode)"
            )

    def _update_source_mode(self) -> None:
        fetching = self.source_mode_var.get() == "fetch"
        if fetching:
            self.existing_frame.pack_forget()
            self.fetch_frame.pack_forget()
            self.fetch_frame.pack(fill="x", before=self.action_bar)
            self.start_btn.configure(text="Fetch, compare and find missing PDFs")
        else:
            self.fetch_frame.pack_forget()
            self.existing_frame.pack_forget()
            self.existing_frame.pack(fill="x", pady=(0, 8), before=self.action_bar)
            self.start_btn.configure(text="Compare CSV and find missing PDFs")

    def _start_primary_action(self) -> None:
        if self.source_mode_var.get() == "existing":
            self._run_compare()
            return
        self._start_export()

    def _append_log(self, message: str) -> None:
        self.owner.append_session_log(message)

    def _start_export(self) -> None:
        output_path = self._new_fetched_csv_path()
        output_str = str(output_path)
        self.fetched_csv_path = output_path

        folder_path_str = self.reconcile_folder_var.get().strip()
        if not folder_path_str or not Path(folder_path_str).is_dir():
            messagebox.showwarning(
                "Fetch transactions",
                "Choose a valid folder for comparing and saving eStamp PDFs.",
                parent=self.dialog,
            )
            return

        payment_name_filter = self.export_name_filter_var.get().strip()

        payment_date_from: datetime.date | None = None
        payment_date_to: datetime.date | None = None
        if self.export_date_filter_enabled_var.get():
            try:
                payment_date_from = self._parse_export_date(self.export_date_from_var.get(), "From")
                payment_date_to = self._parse_export_date(self.export_date_to_var.get(), "To")
            except ValueError as error:
                messagebox.showwarning("Export transactions", str(error), parent=self.dialog)
                return
        if (
            payment_date_from is not None
            and payment_date_to is not None
            and payment_date_from > payment_date_to
        ):
            messagebox.showwarning(
                "Export transactions",
                "The From payment date must not be later than the To payment date.",
                parent=self.dialog,
            )
            return

        self.reconcile_csv_var.set(output_str)

        if self.owner.transaction_export_running:
            messagebox.showinfo(
                "Export transactions",
                "A payment transaction export is already running.",
                parent=self.dialog,
            )
            return

        use_chrome = self.use_chrome_for_all_var.get()
        chrome_browser: PortalBrowser | None = None
        if use_chrome:
            chrome_browser = self._get_chrome_browser()
            if chrome_browser is None:
                messagebox.showwarning(
                    "Export transactions",
                    "Google Chrome was not detected on this system. Uncheck 'Use Chrome For All' to use configured browsers.",
                    parent=self.dialog,
                )
                return

        selected_items = [item for item in self.id_items if item.var.get()]
        stamps_dir = Path(folder_path_str)

        # Build list of targets
        targets: list[TransactionExportTarget] = []
        if not selected_items:
            # Mode A (Manual): 0 checkboxes selected
            active_tab = self.owner._selected_tab()
            if use_chrome and chrome_browser is not None:
                browser = chrome_browser
            else:
                browser = (
                    (active_tab._selected_browser() if active_tab else None)
                    or self._first_available_browser()
                )
            if browser is None:
                messagebox.showwarning(
                    "Export transactions",
                    "Choose an installed portal browser first.",
                    parent=self.dialog,
                )
                return

            profile_path = self._export_profile_path("manual", browser)
            targets.append(
                TransactionExportTarget(
                    name="Manual Login",
                    browser=browser,
                    profile_path=profile_path,
                    auto_login=False,
                    payment_date_from=payment_date_from,
                    payment_date_to=payment_date_to,
                    payment_name_filter=payment_name_filter,
                )
            )
            self._append_log(
                f"Starting manual Citizen login export (Browser: {browser.name})..."
            )
        else:
            # Mode A (Automatic): >0 checkboxes selected
            for item in selected_items:
                tab = self.owner.tabs.get(item.tab_id)
                if tab and (tab.is_active or tab.portal_session_open):
                    messagebox.showwarning(
                        "Export transactions",
                        f"Stop {tab.display_name} before exporting its transaction list.",
                        parent=self.dialog,
                    )
                    return

            for item in selected_items:
                browser = (
                    chrome_browser
                    if (use_chrome and chrome_browser is not None)
                    else item.browser
                )
                if browser is None:
                    messagebox.showwarning(
                        "Export transactions",
                        f"{item.display_name} has no installed portal browser selected.",
                        parent=self.dialog,
                    )
                    return

                # Transaction export logs in with the selected Citizen
                # credentials, so it does not need the tab's normal browser
                # profile.  A separate app-owned profile prevents a saved
                # profile under another Windows user's folder from blocking
                # every export with Access Denied.
                profile_path = self._export_profile_path(f"id-{item.tab_id}", browser)

                solver: CaptchaSolver | None = None
                if item.ocr_enabled:
                    solver = self._get_solver_for_engine(item.ocr_engine)

                targets.append(
                    TransactionExportTarget(
                        name=item.display_name,
                        browser=browser,
                        profile_path=profile_path,
                        credentials=Credentials(
                            citizen_username=item.citizen_username,
                            citizen_password=item.citizen_password,
                        ),
                        sms_user_id=item.sms_user_id,
                        sms_server_url=item.sms_server_url,
                        solver=solver,
                        captcha_copy_mode=item.captcha_copy_mode,
                        window_accent=item.window_accent,
                        window_label=f"{item.display_name} | {item.citizen_username or 'Manual'}",
                        auto_login=bool(item.citizen_username and item.citizen_password),
                        payment_date_from=payment_date_from,
                        payment_date_to=payment_date_to,
                        payment_name_filter=payment_name_filter,
                    )
                )
            browser_info = (
                f"Chrome for all ({chrome_browser.name})"
                if use_chrome and chrome_browser
                else "individual ID browsers"
            )
            self._append_log(
                f"Starting automatic transaction export for {len(targets)} selected ID(s) using {browser_info}..."
            )

        self.is_running = True
        self.owner.transaction_export_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.current_controls = RunControls(lambda _e: None)

        def report_status(message: str) -> None:
            self.dialog.after(
                0,
                lambda: (
                    self.export_status_var.set(message),
                    self._append_log(message),
                ),
            )

        def run_worker() -> None:
            def refresh_comparison(_result: TransactionExportSummary) -> None:
                if not output_path.is_file():
                    return
                try:
                    report = reconcile_transactions(output_path, stamps_dir)
                except Exception as error:
                    report_status(f"Could not update missing-PDF comparison: {error}")
                    return
                self.dialog.after(0, lambda: self._show_reconciliation(report))

            try:
                summary = asyncio.run(
                    export_payment_transactions_batch(
                        targets,
                        output_path,
                        report_status,
                        self.current_controls,
                        on_target_finished=refresh_comparison,
                    )
                )
            except Exception as error:
                error_msg = str(error)
                report_status(f"Export stopped: {error_msg}")
                self.dialog.after(
                    0,
                    lambda: messagebox.showerror(
                        "Export transactions", error_msg, parent=self.dialog
                    ),
                )
            else:
                refresh_comparison(
                    TransactionExportSummary(0, 0, 0, output_path, target_name="final")
                )
                failed_results = [result for result in summary.results if result.error]
                msg = (
                    f"Saved {summary.appended_rows} new transaction(s) to:\n{summary.output_path}\n\n"
                    f"Skipped {summary.skipped_duplicates} duplicate(s) across {len(targets)} ID(s)."
                )
                if failed_results:
                    error_lines = "\n".join(
                        f"- {result.target_name}: {result.error}" for result in failed_results
                    )
                    report_status(f"Export finished with {len(failed_results)} ID error(s).")
                    self.dialog.after(
                        0,
                        lambda: messagebox.showwarning(
                            "Export payment transactions",
                            f"{msg}\n\nFailed IDs:\n{error_lines}",
                            parent=self.dialog,
                        ),
                    )
                else:
                    report_status("Export completed successfully.")
                    self.dialog.after(
                        0,
                        lambda: messagebox.showinfo(
                            "Export payment transactions", msg, parent=self.dialog
                        ),
                    )
            finally:
                self.dialog.after(0, self._on_export_finished)

        threading.Thread(target=run_worker, name="transaction-export-batch", daemon=True).start()

    def _get_solver_for_engine(self, engine: OcrEngine) -> CaptchaSolver | None:
        try:
            if engine == OcrEngine.PADDLEOCR:
                from services.paddleocr_ocr import PaddleOcrCaptchaSolver

                return PaddleOcrCaptchaSolver()
            elif engine == OcrEngine.EASYOCR:
                from services.gemini_ocr import EasyOcrCaptchaSolver

                return EasyOcrCaptchaSolver()
            elif engine == OcrEngine.GEMINI:
                # The export runs on a separate asyncio loop.  Do not return
                # controller.solver directly: its browser and asyncio lock
                # belong to the controller's worker thread.
                return self.owner.controller.gemini_solver_for_external_loop()
        except Exception as error:
            self._append_log(f"Could not start {engine.value} CAPTCHA OCR: {error}")
        return None

    @staticmethod
    def _parse_export_date(value: str, label: str) -> datetime.date | None:
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return datetime.date.fromisoformat(cleaned)
        except ValueError as error:
            raise ValueError(f"{label} payment date must use YYYY-MM-DD.") from error

    def _update_date_filter_state(self) -> None:
        state = "readonly" if self.export_date_filter_enabled_var.get() else "disabled"
        for entry in self.export_date_entries:
            entry.configure(state=state)

    @staticmethod
    def _export_profile_path(profile_name: str, browser: PortalBrowser) -> Path:
        browser_slug = re.sub(r"[^a-z0-9]+", "-", browser.name.casefold()).strip("-") or "browser"
        return (
            app_data_directory()
            / "transaction-export-profiles"
            / profile_name
            / browser.engine.value
            / browser_slug
        )

    @staticmethod
    def _new_fetched_csv_path() -> Path:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return app_data_directory() / "transaction-recovery" / f"transactions_{timestamp}.csv"

    def _stop_export(self) -> None:
        if self.current_controls is not None:
            self.current_controls.stop("Export cancelled by user")
        self._append_log("Stop requested. Waiting for active browser to close...")
        self.stop_btn.configure(state="disabled")

    def _on_export_finished(self) -> None:
        self.is_running = False
        self.owner.transaction_export_running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    # ──────────────────────────────────────────────────────────────────────────
    # Comparison results and recovery actions.
    # ──────────────────────────────────────────────────────────────────────────

    def _build_results_section(self, parent: ttk.Frame) -> None:
        results_group = ttk.LabelFrame(parent, text="4. Missing eStamp results", padding=8)
        results_group.pack(fill="both", expand=True, pady=(0, 6))
        self.results_group = results_group

        ttk.Label(
            results_group, textvariable=self.reconcile_status_var, style="Status.TLabel"
        ).pack(anchor="w", pady=(0, 4))

        r_btns = ttk.Frame(results_group)
        r_btns.pack(fill="x", pady=(0, 8))

        self.download_missing_btn = ttk.Button(
            r_btns,
            text="Download Missing Stamps (0)",
            style="Accent.TButton",
            command=self._start_download_missing_stamps,
            state="disabled",
        )
        self.download_missing_btn.pack(side="left", padx=(0, 6))

        self.stop_download_btn = ttk.Button(
            r_btns,
            text="Stop Download",
            command=self._stop_download_missing,
            state="disabled",
        )
        self.stop_download_btn.pack(side="left", padx=(0, 8))

        self.export_transactions_btn = ttk.Button(
            r_btns,
            text="Export fetched transactions CSV...",
            command=self._export_fetched_transactions_csv,
            state="disabled",
        )
        self.export_transactions_btn.pack(side="left", padx=(0, 8))

        self.export_csv_btn = ttk.Button(
            r_btns,
            text="Export comparison CSV...",
            command=self._export_reconcile_csv,
            state="disabled",
        )
        self.export_csv_btn.pack(side="left")

        self.export_txt_btn = ttk.Button(
            r_btns,
            text="Export comparison text...",
            command=self._export_reconcile_text,
            state="disabled",
        )
        self.export_txt_btn.pack(side="left", padx=(8, 0))

        self.results_notebook = ttk.Notebook(results_group)
        self.results_notebook.pack(fill="both", expand=True)

        f_missing = ttk.Frame(self.results_notebook)
        self.results_notebook.add(f_missing, text="Transactions without PDF (0)")
        self.missing_text = tk.Text(f_missing, wrap="none", font=("Consolas", 8))
        s_missing = ttk.Scrollbar(f_missing, orient="vertical", command=self.missing_text.yview)
        self.missing_text.configure(yscrollcommand=s_missing.set)
        self.missing_text.pack(side="left", fill="both", expand=True)
        s_missing.pack(side="right", fill="y")

        f_unmatched = ttk.Frame(self.results_notebook)
        self.results_notebook.add(f_unmatched, text="PDFs without CSV transaction (0)")
        self.unmatched_text = tk.Text(f_unmatched, wrap="none", font=("Consolas", 8))
        s_unmatched = ttk.Scrollbar(f_unmatched, orient="vertical", command=self.unmatched_text.yview)
        self.unmatched_text.configure(yscrollcommand=s_unmatched.set)
        self.unmatched_text.pack(side="left", fill="both", expand=True)
        s_unmatched.pack(side="right", fill="y")

    def _browse_reconcile_csv(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose exported payment transactions CSV",
            filetypes=[("CSV files", "*.csv")],
            parent=self.dialog,
        )
        if selected:
            self.reconcile_csv_var.set(selected)

    def _browse_reconcile_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose folder containing downloaded eStamp PDFs",
            parent=self.dialog,
            mustexist=True,
        )
        if selected:
            self.reconcile_folder_var.set(selected)

    def _run_compare(self) -> None:
        csv_path_str = self.reconcile_csv_var.get().strip()
        folder_path_str = self.reconcile_folder_var.get().strip()
        if not csv_path_str or not Path(csv_path_str).is_file():
            messagebox.showwarning(
                "Compare transactions",
                "Choose a valid exported transaction CSV file.",
                parent=self.dialog,
            )
            return
        if not folder_path_str or not Path(folder_path_str).is_dir():
            messagebox.showwarning(
                "Compare transactions",
                "Choose a valid folder containing downloaded eStamp PDFs.",
                parent=self.dialog,
            )
            return

        try:
            report = reconcile_transactions(Path(csv_path_str), Path(folder_path_str))
        except Exception as error:
            messagebox.showerror("Compare error", str(error), parent=self.dialog)
            return

        self._show_reconciliation(report)

    def _show_reconciliation(self, report: TransactionReconciliation) -> None:
        self.last_reconciliation = report
        self.reconcile_status_var.set(
            f"{len(report.missing_pdfs)} transaction(s) without a PDF | "
            f"{len(report.unmatched_pdfs)} PDF(s) without a CSV transaction"
        )

        self.results_notebook.tab(
            0, text=f"Transactions without PDF ({len(report.missing_pdfs)})"
        )
        self.results_notebook.tab(
            1, text=f"PDFs without CSV transaction ({len(report.unmatched_pdfs)})"
        )

        self.missing_text.configure(state="normal")
        self.unmatched_text.configure(state="normal")
        self.missing_text.delete("1.0", "end")
        self.unmatched_text.delete("1.0", "end")

        self.missing_text.insert(
            "1.0", "\n".join(self.owner._missing_pdf_lines(report)) or "No missing PDFs."
        )
        self.unmatched_text.insert(
            "1.0", "\n".join(self.owner._unmatched_pdf_lines(report)) or "No unmatched PDFs."
        )

        self.missing_text.configure(state="disabled")
        self.unmatched_text.configure(state="disabled")

        if not self.is_downloading_stamps:
            if report.missing_pdfs:
                self.download_missing_btn.configure(
                    state="normal", text=f"Download Missing Stamps ({len(report.missing_pdfs)})"
                )
            else:
                self.download_missing_btn.configure(
                    state="disabled", text="Download Missing Stamps (0)"
                )

        self.export_csv_btn.configure(state="normal")
        self.export_txt_btn.configure(state="normal")
        if self.fetched_csv_path is not None and self.fetched_csv_path.is_file():
            self.export_transactions_btn.configure(state="normal")

    def _export_fetched_transactions_csv(self) -> None:
        source = self.fetched_csv_path
        if source is None or not source.is_file():
            messagebox.showinfo(
                "Export fetched transactions",
                "Fetch transactions first. There is no fetched CSV to export yet.",
                parent=self.dialog,
            )
            return
        selected = filedialog.asksaveasfilename(
            title="Export fetched transactions CSV",
            initialdir=default_download_directory(),
            initialfile=source.name,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self.dialog,
        )
        if not selected:
            return
        try:
            shutil.copy2(source, selected)
        except OSError as error:
            messagebox.showerror("Export fetched transactions", str(error), parent=self.dialog)
            return
        self.owner.config.transaction_export_path = selected
        self.owner.save_config()
        self._append_log(f"Exported fetched transactions CSV to: {selected}")

    def _start_download_missing_stamps(self) -> None:
        if self.last_reconciliation is None or not self.last_reconciliation.missing_pdfs:
            messagebox.showinfo(
                "Download Missing Stamps", "No missing eStamps to download.", parent=self.dialog
            )
            return

        folder_path_str = self.reconcile_folder_var.get().strip()
        if not folder_path_str:
            messagebox.showwarning(
                "Download Missing Stamps",
                "Choose a valid stamps download folder.",
                parent=self.dialog,
            )
            return

        stamps_dir = Path(folder_path_str)
        if not stamps_dir.is_dir():
            try:
                stamps_dir.mkdir(parents=True, exist_ok=True)
            except Exception as err:
                messagebox.showerror(
                    "Download Missing Stamps", f"Could not create folder: {err}", parent=self.dialog
                )
                return

        missing_items = list(self.last_reconciliation.missing_pdfs)
        self.is_downloading_stamps = True
        self.download_missing_controls = RunControls(lambda _e: None)
        self.download_missing_btn.configure(state="disabled")
        self.stop_download_btn.configure(state="normal")
        self.reconcile_status_var.set(
            f"Starting download of {len(missing_items)} missing stamp(s)..."
        )

        def report_status(msg: str) -> None:
            self.dialog.after(0, lambda: self.reconcile_status_var.set(msg))

        def run_worker() -> None:
            try:
                summary = download_missing_stamps(
                    missing_items, stamps_dir, report_status, self.download_missing_controls
                )
            except Exception as error:
                err_msg = str(error)
                self.dialog.after(
                    0,
                    lambda: messagebox.showerror(
                        "Download Missing Stamps", err_msg, parent=self.dialog
                    ),
                )
            else:
                msg = (
                    f"Saved {summary.downloaded} stamp(s) to:\n{summary.output_directory}\n\n"
                    f"Already existed (skipped): {summary.skipped_existing}\n"
                    f"Failed: {summary.failed}"
                )
                self.dialog.after(
                    0,
                    lambda: (
                        messagebox.showinfo("Download Missing Stamps", msg, parent=self.dialog),
                        self._run_compare(),
                    ),
                )
            finally:
                self.dialog.after(0, self._on_download_missing_finished)

        threading.Thread(target=run_worker, name="download-missing-stamps", daemon=True).start()

    def _stop_download_missing(self) -> None:
        if self.download_missing_controls is not None:
            self.download_missing_controls.stop("Download stopped by user")
        self.reconcile_status_var.set("Stopping download...")
        self.stop_download_btn.configure(state="disabled")

    def _on_download_missing_finished(self) -> None:
        self.is_downloading_stamps = False
        self.stop_download_btn.configure(state="disabled")
        if self.last_reconciliation and self.last_reconciliation.missing_pdfs:
            self.download_missing_btn.configure(
                state="normal", text=f"Download Missing Stamps ({len(self.last_reconciliation.missing_pdfs)})"
            )
        else:
            self.download_missing_btn.configure(
                state="disabled", text="Download Missing Stamps (0)"
            )

    def _export_reconcile_csv(self) -> None:
        if self.last_reconciliation is not None:
            self.owner._export_reconciliation_csv(self.last_reconciliation)

    def _export_reconcile_text(self) -> None:
        if self.last_reconciliation is not None:
            self.owner._export_reconciliation_text(self.last_reconciliation)

    def _on_close(self) -> None:
        if self.is_running:
            if not messagebox.askyesno(
                "Export Running",
                "A transaction export is currently running.\nDo you want to stop the export and close?",
                parent=self.dialog,
            ):
                return
            self._stop_export()
        if self.is_downloading_stamps:
            if not messagebox.askyesno(
                "Download Running",
                "Missing stamp download is currently running.\nDo you want to stop downloading and close?",
                parent=self.dialog,
            ):
                return
            self._stop_download_missing()
        self.dialog.destroy()
