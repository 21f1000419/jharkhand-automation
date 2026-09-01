"""Unified dialog for Downloading Undownloaded Certificates.

Presents a 2-column layout with clear headings:
- Left column (Mode A): Export eStamp Payment Transactions (automatic/manual Citizen login)
- Right column (Mode B): Compare Transaction CSV with Downloaded Stamps
"""

from __future__ import annotations

import asyncio
import datetime
import re
import threading
import tkinter as tk
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from core.config import DEFAULT_SMS_SERVER_URL, default_download_directory
from core.controls import RunControls
from core.models import BrowserEngine, CaptchaCopyMode, Credentials, OcrEngine, PortalBrowser
from services.captcha_ocr import CaptchaSolver
from services.estamp_transactions import (
    BatchTransactionExportSummary,
    MissingStampDownloadSummary,
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
    """Side-by-side 2-column dialog offering transaction export and stamp reconciliation."""

    def __init__(self, owner: MainWindow, initial_tab: int = 0) -> None:
        self.owner = owner
        self.dialog = tk.Toplevel(owner.root)
        self.dialog.title("Download Undownloaded Certificates")
        self.dialog.geometry("1180x700")
        self.dialog.minsize(960, 540)
        self.dialog.transient(owner.root)

        self.is_running = False
        self.current_controls: RunControls | None = None
        self.is_downloading_stamps = False
        self.download_missing_controls: RunControls | None = None
        self.last_reconciliation: TransactionReconciliation | None = None

        # Variables - Mode A (Export)
        self.export_output_var = tk.StringVar(
            value=self.owner.config.transaction_export_path.strip()
            or str(default_download_directory() / "estamp_payment_transactions.csv")
        )
        self.use_chrome_for_all_var = tk.BooleanVar(value=True)
        self.selection_summary_var = tk.StringVar()
        self.export_status_var = tk.StringVar(value="Ready")

        # Variables - Mode B (Compare)
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

        # 2-Column Content Area
        cols_container = ttk.Frame(main_frame)
        cols_container.pack(fill="both", expand=True)

        col_left = ttk.Frame(cols_container)
        col_left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        separator = ttk.Separator(cols_container, orient="vertical")
        separator.pack(side="left", fill="y", padx=4)

        col_right = ttk.Frame(cols_container)
        col_right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        # Build Left and Right columns
        self._build_export_column(col_left)
        self._build_reconcile_column(col_right)

        # Bottom Bar
        bottom_bar = ttk.Frame(main_frame)
        bottom_bar.pack(fill="x", pady=(10, 0))
        ttk.Separator(bottom_bar, orient="horizontal").pack(fill="x", pady=(0, 8))
        ttk.Button(bottom_bar, text="Close", command=self._on_close).pack(side="right")

    # ──────────────────────────────────────────────────────────────────────────
    # Column 1: Mode A - Export eStamp Payment Transactions
    # ──────────────────────────────────────────────────────────────────────────

    def _build_export_column(self, parent: ttk.Frame) -> None:
        # Header
        header = ttk.Label(
            parent,
            text="Mode A: Export eStamp Payment Transactions",
            font=("Segoe UI", 11, "bold"),
            foreground="#1e3a8a",
        )
        header.pack(anchor="w", pady=(0, 6))

        # Group 1: Output CSV Destination
        csv_group = ttk.LabelFrame(parent, text="1. Save Transactions to CSV", padding=8)
        csv_group.pack(fill="x", pady=(0, 6))

        ttk.Label(csv_group, text="Destination CSV:").pack(side="left", padx=(0, 6))
        ttk.Entry(csv_group, textvariable=self.export_output_var).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ttk.Button(csv_group, text="Browse...", command=self._browse_export_output).pack(side="left")

        # Group 2: Target IDs Selection
        ids_group = ttk.LabelFrame(parent, text="2. Select IDs to Fetch Transactions", padding=8)
        ids_group.pack(fill="both", expand=True, pady=(0, 6))

        info_label = ttk.Label(
            ids_group,
            text=(
                "• Check the IDs you want to log into and fetch transactions from.\n"
                "• If 0 checkboxes are selected, a browser opens for manual Citizen login."
            ),
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
        list_container.pack(fill="both", expand=True)

        canvas = tk.Canvas(list_container, borderwidth=0, highlightthickness=0, height=100)
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

        # Group 3: Progress & Activity Log
        log_group = ttk.LabelFrame(parent, text="3. Export Progress & Status", padding=8)
        log_group.pack(fill="x", pady=(0, 6))

        ttk.Label(log_group, textvariable=self.export_status_var, style="Status.TLabel").pack(
            anchor="w", pady=(0, 4)
        )

        log_container = ttk.Frame(log_group)
        log_container.pack(fill="x")
        self.log_text = tk.Text(log_container, height=4, font=("Consolas", 8), wrap="word")
        log_scroll = ttk.Scrollbar(log_container, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        # Action Buttons
        btn_bar = ttk.Frame(parent)
        btn_bar.pack(fill="x", pady=(2, 0))

        self.start_btn = ttk.Button(
            btn_bar, text="Start Export", style="Accent.TButton", command=self._start_export
        )
        self.start_btn.pack(side="left")

        self.stop_btn = ttk.Button(
            btn_bar, text="Stop", command=self._stop_export, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(8, 0))

    def _populate_id_items(self, parent: ttk.Frame) -> None:
        self.id_items.clear()
        seen_usernames: set[str] = set()

        sorted_tabs = sorted(self.owner.tabs.values(), key=lambda t: t.tab_id)
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
            row_frame.pack(fill="x", pady=2, padx=4)

            chk = ttk.Checkbutton(
                row_frame,
                variable=var,
                command=self._update_selection_summary,
            )
            chk.pack(side="left", padx=(0, 6))

            if citizen_user:
                label_text = f"{tab.display_name} — Citizen ID: {citizen_user}"
                if browser:
                    label_text += f" ({browser.name})"
            else:
                label_text = f"{tab.display_name} — (No Citizen username configured)"
                if browser:
                    label_text += f" ({browser.name})"

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

    def _browse_export_output(self) -> None:
        current = self.export_output_var.get().strip()
        prev = Path(current).expanduser() if current else None
        selected = filedialog.asksaveasfilename(
            title="Choose eStamp payment transactions CSV",
            initialdir=(
                prev.parent
                if prev is not None and prev.parent.is_dir()
                else default_download_directory()
            ),
            initialfile=prev.name if prev is not None else "estamp_payment_transactions.csv",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            confirmoverwrite=False,
            parent=self.dialog,
        )
        if selected:
            self.export_output_var.set(selected)
            self.owner.config.transaction_export_path = selected
            self.owner.save_config()

    def _append_log(self, message: str) -> None:
        time_str = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{time_str}] {message}\n")
        self.log_text.see("end")

    def _start_export(self) -> None:
        output_str = self.export_output_var.get().strip()
        if not output_str:
            messagebox.showwarning(
                "Export transactions", "Choose a destination CSV file.", parent=self.dialog
            )
            return

        self.owner.config.transaction_export_path = output_str
        self.owner.save_config()

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
        output_path = Path(output_str)

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

            profile_slug = re.sub(r"[^a-z0-9]+", "-", browser.name.casefold()).strip("-") or "browser"
            profile_base = (
                active_tab.config.portal_profile_path
                if active_tab
                else self.owner.config.chrome_profile_path
            )
            profile_path = Path(profile_base) / browser.engine.value / f"manual-export-{profile_slug}"
            targets.append(
                TransactionExportTarget(
                    name="Manual Login",
                    browser=browser,
                    profile_path=profile_path,
                    auto_login=False,
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
                        f"Stop {tab.display_name} before using its profile for this export.",
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

                tab = self.owner.tabs.get(item.tab_id)
                base_profile = (
                    tab.config.portal_profile_path
                    if tab
                    else str(item.profile_path.parent.parent)
                )
                profile_slug = (
                    re.sub(r"[^a-z0-9]+", "-", browser.name.casefold()).strip("-") or "browser"
                )
                profile_path = Path(base_profile) / browser.engine.value / profile_slug

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
            try:
                summary = asyncio.run(
                    export_payment_transactions_batch(
                        targets, output_path, report_status, self.current_controls
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
                msg = (
                    f"Saved {summary.appended_rows} new transaction(s) to:\n{summary.output_path}\n\n"
                    f"Skipped {summary.skipped_duplicates} duplicate(s) across {len(targets)} ID(s)."
                )
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
            elif engine == OcrEngine.GEMINI and self.owner.controller.solver:
                return self.owner.controller.solver
        except Exception:
            pass
        return None

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
    # Column 2: Mode B - Compare Transaction CSV with Downloaded Stamps
    # ──────────────────────────────────────────────────────────────────────────

    def _build_reconcile_column(self, parent: ttk.Frame) -> None:
        # Header
        header = ttk.Label(
            parent,
            text="Mode B: Compare Transactions with Stamps",
            font=("Segoe UI", 11, "bold"),
            foreground="#1e3a8a",
        )
        header.pack(anchor="w", pady=(0, 6))

        # Group 1: Input Paths
        input_group = ttk.LabelFrame(parent, text="1. Input Paths", padding=8)
        input_group.pack(fill="x", pady=(0, 6))

        # Row 1: CSV path
        r1 = ttk.Frame(input_group)
        r1.pack(fill="x", pady=2)
        ttk.Label(r1, text="Transactions CSV:", width=18, anchor="w").pack(side="left")
        ttk.Entry(r1, textvariable=self.reconcile_csv_var).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ttk.Button(r1, text="Browse...", command=self._browse_reconcile_csv).pack(side="left")

        # Row 2: Stamps directory
        r2 = ttk.Frame(input_group)
        r2.pack(fill="x", pady=2)
        ttk.Label(r2, text="Stamps Folder:", width=18, anchor="w").pack(side="left")
        ttk.Entry(r2, textvariable=self.reconcile_folder_var).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ttk.Button(r2, text="Browse...", command=self._browse_reconcile_folder).pack(side="left")

        # Compare Button
        ttk.Button(
            input_group,
            text="Compare Transactions & Stamps",
            style="Accent.TButton",
            command=self._run_compare,
        ).pack(anchor="w", pady=(6, 0))

        # Group 2: Results
        results_group = ttk.LabelFrame(parent, text="2. Comparison Results", padding=8)
        results_group.pack(fill="both", expand=True, pady=(0, 6))

        ttk.Label(
            results_group, textvariable=self.reconcile_status_var, style="Status.TLabel"
        ).pack(anchor="w", pady=(0, 4))

        # Results Notebook
        self.results_notebook = ttk.Notebook(results_group)
        self.results_notebook.pack(fill="both", expand=True, pady=(0, 6))

        # Missing PDFs tab
        f_missing = ttk.Frame(self.results_notebook)
        self.results_notebook.add(f_missing, text="Transactions without PDF (0)")
        self.missing_text = tk.Text(f_missing, wrap="none", font=("Consolas", 8))
        s_missing = ttk.Scrollbar(f_missing, orient="vertical", command=self.missing_text.yview)
        self.missing_text.configure(yscrollcommand=s_missing.set)
        self.missing_text.pack(side="left", fill="both", expand=True)
        s_missing.pack(side="right", fill="y")

        # Unmatched PDFs tab
        f_unmatched = ttk.Frame(self.results_notebook)
        self.results_notebook.add(f_unmatched, text="PDFs without CSV transaction (0)")
        self.unmatched_text = tk.Text(f_unmatched, wrap="none", font=("Consolas", 8))
        s_unmatched = ttk.Scrollbar(f_unmatched, orient="vertical", command=self.unmatched_text.yview)
        self.unmatched_text.configure(yscrollcommand=s_unmatched.set)
        self.unmatched_text.pack(side="left", fill="both", expand=True)
        s_unmatched.pack(side="right", fill="y")

        # Action Buttons
        r_btns = ttk.Frame(parent)
        r_btns.pack(fill="x", pady=(2, 0))

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

        self.export_csv_btn = ttk.Button(
            r_btns, text="Export CSV...", command=self._export_reconcile_csv, state="disabled"
        )
        self.export_csv_btn.pack(side="left")

        self.export_txt_btn = ttk.Button(
            r_btns, text="Export text...", command=self._export_reconcile_text, state="disabled"
        )
        self.export_txt_btn.pack(side="left", padx=(8, 0))

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
