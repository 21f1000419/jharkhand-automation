from __future__ import annotations

import asyncio
import queue
import re
import shutil
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.browser_detection import detect_supported_browsers
from core.config import AppConfig, ConfigStore, TabConfig, app_data_directory, default_download_directory
from core.controller import AutomationController
from core.models import PortalBrowser, UiEvent
from core.playwright_browsers import (
    browser_install_directory,
    install_managed_firefox,
    managed_firefox_is_installed,
)
from services.credential_store import CredentialStore
from services.csv_store import CsvBatchStore
from services.estamp_transactions import export_payment_transactions
from services.transaction_reconciliation import (
    TransactionReconciliation,
    reconcile_transactions,
    write_reconciliation_csv,
    write_reconciliation_text,
)
from ui.automation_status import AutomationStatusWindow
from ui.run_tab import CUSTOM_BROWSER_OPTION as TAB_CUSTOM_BROWSER_OPTION
from ui.run_tab import AutomationTab

TAB_ACCENT_COLORS = (
    "#2563eb",
    "#059669",
    "#d97706",
    "#7c3aed",
    "#e11d48",
    "#0891b2",
    "#65a30d",
    "#ea580c",
)


class MainWindow:
    """Small host for the independent automation tabs."""

    CUSTOM_BROWSER_OPTION = TAB_CUSTOM_BROWSER_OPTION

    def __init__(
        self,
        root: tk.Tk,
        config: AppConfig,
        config_store: ConfigStore,
        controller: AutomationController,
    ) -> None:
        self.root = root
        self.config = config
        self.config_store = config_store
        self.controller = controller
        self.credential_store = CredentialStore()
        self.tabs: dict[int, AutomationTab] = {}
        self.tab_states: dict[int, str] = {}
        self.tab_accent_images: dict[tuple[int, bool], tk.PhotoImage] = {}
        self.portal_browsers: dict[str, PortalBrowser] = {}
        self.session_log_lines: list[str] = []
        self.managed_firefox_downloading = False
        self.gemini_ready = bool(config.gemini_verified)
        self.gemini_checking = False
        self.ocr_test_running = False
        self.transaction_export_running = False
        self.automation_status_window: AutomationStatusWindow | None = None
        self._closing = False

        # The OCR browser is shared by the controller. Run configuration stays
        # in each AutomationTab.
        self.chrome_var = tk.StringVar(value=config.chrome_executable)
        self.profile_var = tk.StringVar(value=config.chrome_profile_path)
        self.ocr_engine_var = tk.StringVar(value=config.ocr_engine)
        self.run_status_var = tk.StringVar(value="Idle")
        self.summary_var = tk.StringVar(value="0 active | 0 idle")
        self.run_summary_var = self.summary_var
        self.global_success_count = 0
        self.global_success_var = tk.StringVar(value="Session downloads: 0")

        self._detect_portal_browsers()
        self._build()
        self._build_menu()
        for tab_config in sorted(config.tabs, key=lambda item: item.tab_id):
            self._add_tab_widget(tab_config)
        self._update_summary()
        self.controller.record_activity("application_started", "Desktop application opened.")
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        self.root.title("Compitcom eStamp Batch Automation")
        self.root.minsize(980, 700)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = max(980, int(screen_width * 0.84))
        height = max(700, int(screen_height * 0.88))
        left = max(0, (screen_width - width) // 2)
        self.root.geometry(f"{width}x{height}+{left}+{int(screen_height * 0.02)}")

        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Heading.TLabel", font=("Segoe UI", 16, "bold"))  # type: ignore[no-untyped-call]
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))  # type: ignore[no-untyped-call]

        container = ttk.Frame(self.root, padding=12)
        container.pack(fill="both", expand=True)
        header = ttk.Frame(container)
        header.pack(fill="x", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="eStamp Batch Automation", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Each ID has its own CSV, browser session, credentials, and persistent portal profile.",
        ).grid(row=1, column=0, sticky="w")
        ttk.Label(header, textvariable=self.summary_var, style="Status.TLabel").grid(
            row=0, column=1, rowspan=1, sticky="e", padx=(12, 0)
        )
        success_label = ttk.Label(
            header,
            textvariable=self.global_success_var,
            style="Status.TLabel",
            foreground="#047857",
        )
        success_label.grid(row=1, column=1, sticky="e", padx=(12, 0))
        success_label.bind("<Button-3>", lambda _e: self._reset_global_success_count())
        success_label.bind("<Button-2>", lambda _e: self._reset_global_success_count())

        toolbar = ttk.Frame(container)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Add ID", command=self._add_tab).pack(side="left")
        ttk.Button(toolbar, text="Remove selected", command=self._remove_selected_tab).pack(
            side="left", padx=(7, 0)
        )
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(toolbar, text="Start All", command=self._start_all).pack(side="left")
        ttk.Button(toolbar, text="Stop All", command=self._stop_all).pack(side="left", padx=(7, 0))
        ttk.Button(toolbar, text="Show status dock", command=self._show_status_dock).pack(
            side="left", padx=(7, 0)
        )
        ttk.Button(toolbar, text="Refresh browsers", command=self._refresh_portal_browsers).pack(
            side="left", padx=(7, 0)
        )
        ttk.Label(toolbar, textvariable=self.run_status_var, foreground="#555555").pack(
            side="right", padx=(10, 0)
        )

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root, tearoff=False)
        self.application_menu = menu
        menu.add_command(label="Download managed Firefox", command=self._download_managed_firefox)
        self.managed_firefox_menu_index = 0
        self._update_managed_firefox_menu()
        menu.add_command(label="Download CSV format...", command=self._download_template)
        menu.add_command(
            label="Export eStamp payment transactions...",
            command=self._export_payment_transactions,
        )
        menu.add_command(
            label="Compare transaction CSV with downloaded stamps...",
            command=self._compare_transactions_with_stamps,
        )

        profile_menu = tk.Menu(menu, tearoff=False)
        profile_menu.add_command(
            label="Edit OCR profile settings...", command=self._show_ocr_profile_settings
        )
        profile_menu.add_command(label="Delete OCR profile...", command=self._delete_ocr_profile)
        menu.add_cascade(label="OCR profile", menu=profile_menu)

        activity_menu = tk.Menu(menu, tearoff=False)
        activity_menu.add_command(label="Current session...", command=self._show_session_log)
        activity_menu.add_command(label="Open daily log folder", command=self._open_log_folder)
        activity_menu.add_command(label="Open stored CAPTCHAs folder", command=self._open_captcha_folder)
        menu.add_cascade(label="Activity", menu=activity_menu)
        self.root.configure(menu=menu)

    def _detect_portal_browsers(self) -> None:
        try:
            browsers = detect_supported_browsers()
        except Exception as error:  # discovery should not prevent the UI from opening
            browsers = []
            self.append_session_log(f"Browser discovery failed: {error}")
        self.portal_browsers = {browser.name: browser for browser in browsers}

    def _refresh_portal_browsers(self) -> None:
        self._record_ui_action("refresh_portal_browsers_clicked")
        self._detect_portal_browsers()
        choices = [*self.portal_browsers, self.CUSTOM_BROWSER_OPTION]
        for tab in self.tabs.values():
            tab.portal_browser_box.configure(values=choices)
        self.run_status_var.set(f"Found {len(self.portal_browsers)} supported browser(s)")

    def _add_tab_widget(self, tab_config: TabConfig) -> AutomationTab:
        tab = AutomationTab(self, self.notebook, tab_config)
        self.tabs[tab.tab_id] = tab
        initial_state = "Idle" if tab.is_enabled else "Disabled"
        self.tab_states[tab.tab_id] = initial_state
        accent = self._tab_accent_image(tab.tab_id, enabled=tab.is_enabled)
        self.notebook.add(
            tab.frame,
            text=tab.display_name,
            image=accent,
            compound="left",
        )
        self.update_tab_state(tab, initial_state, "")
        return tab

    def _tab_accent_image(self, tab_id: int, *, enabled: bool) -> tk.PhotoImage:
        key = (tab_id, enabled)
        existing = self.tab_accent_images.get(key)
        if existing is not None:
            return existing
        color = self._tab_accent_color(tab_id) if enabled else "#9ca3af"
        image = tk.PhotoImage(master=self.root, width=13, height=13)
        image.put("#374151" if enabled else "#6b7280", to=(0, 0, 13, 13))
        image.put(color, to=(1, 1, 12, 12))
        self.tab_accent_images[key] = image
        return image

    def _tab_accent_color(self, tab_id: int) -> str:
        return TAB_ACCENT_COLORS[(tab_id - 1) % len(TAB_ACCENT_COLORS)]

    def tab_accent_color(self, tab_id: int) -> str:
        """Return the stable visual identity color assigned to an ID tab."""
        return self._tab_accent_color(tab_id)

    def _ensure_status_dock(self) -> AutomationStatusWindow:
        dock = self.automation_status_window
        if dock is None or not dock.exists:
            dock = AutomationStatusWindow(
                self.root,
                on_pause=self._dock_pause,
                on_resume=self._dock_resume,
                on_stop=self._dock_stop,
                on_error_decision=self._dock_error_decision,
                on_browser_recovery=self._dock_browser_recovery,
                on_focus_browser=self._dock_focus_browser,
                on_stop_all=self._stop_all,
            )
            self.automation_status_window = dock
        return dock

    def _dock_tab(self, run_id: str) -> AutomationTab | None:
        return self.tabs.get(int(run_id)) if run_id.isdigit() else None

    def _dock_pause(self, run_id: str) -> None:
        tab = self._dock_tab(run_id)
        if tab is not None:
            self._record_ui_action(f"id_{run_id}_dock_pause_clicked")
            tab.pause()

    def _dock_resume(self, run_id: str) -> None:
        tab = self._dock_tab(run_id)
        if tab is not None:
            self._record_ui_action(f"id_{run_id}_dock_resume_clicked")
            tab.resume()

    def _dock_stop(self, run_id: str) -> None:
        tab = self._dock_tab(run_id)
        if tab is not None:
            self._record_ui_action(f"id_{run_id}_dock_stop_clicked")
            if tab.browser_recovery_pending:
                tab.dismiss_browser_recovery()
            else:
                tab.stop()

    def _dock_error_decision(self, run_id: str, action: str) -> None:
        tab = self._dock_tab(run_id)
        if tab is not None:
            self._record_ui_action(f"id_{run_id}_dock_error_{action}_clicked")
            tab.decide_error(action)

    def _dock_browser_recovery(self, run_id: str, action: str) -> None:
        tab = self._dock_tab(run_id)
        if tab is not None:
            self._record_ui_action(f"id_{run_id}_dock_browser_{action}_clicked")
            tab.recover_browser(action)

    def _dock_focus_browser(self, run_id: str) -> None:
        self._record_ui_action(f"id_{run_id}_dock_focus_browser_clicked")
        handle = self.controller.get_portal_window_handle(run_id)
        if handle is None:
            self.run_status_var.set("No browser window found for this ID")
            return
        import os
        if os.name != "nt":
            return
        from automation.browser import _restore_and_activate_window
        if not _restore_and_activate_window(handle):
            self.run_status_var.set("Could not focus the browser window")

    def _show_status_dock(self) -> None:
        self._record_ui_action("show_status_dock_clicked")
        dock = self._ensure_status_dock()
        for tab in self.tabs.values():
            if (tab.is_active or tab.browser_recovery_pending) and not dock.has_run(tab.run_id):
                dock.begin_run(
                    tab.run_id,
                    tab.display_name,
                    self._tab_accent_color(tab.tab_id),
                    tab.run_status_var.get(),
                )
            if tab.browser_recovery_pending and dock.has_run(tab.run_id):
                dock.show_browser_recovery(
                    tab.run_id,
                    "Retry this row, skip it and open the next row, or stop this ID.",
                    ready=tab.browser_recovery_ready,
                )
        dock.show()
        if not any(tab.is_active for tab in self.tabs.values()) and not dock.has_cards:
            self.run_status_var.set("No active IDs to show")

    def should_offer_browser_recovery(self, closing_tab: AutomationTab) -> bool:
        active_runs = sum(
            tab.is_active or tab.portal_session_open or tab.browser_recovery_pending
            for tab in self.tabs.values()
        )
        return closing_tab.tab_id in self.tabs and active_runs > 1

    def show_browser_recovery(
        self, tab: AutomationTab, message: str, *, ready: bool
    ) -> None:
        dock = self._ensure_status_dock()
        if not dock.has_run(tab.run_id):
            dock.begin_run(
                tab.run_id,
                tab.display_name,
                self._tab_accent_color(tab.tab_id),
                message,
            )
        dock.show_browser_recovery(tab.run_id, message, ready=ready)

    def show_tab_error_in_dock(self, tab: AutomationTab, event: UiEvent) -> bool:
        dock = self._ensure_status_dock()
        if not dock.has_run(tab.run_id):
            dock.begin_run(
                tab.run_id,
                tab.display_name,
                self._tab_accent_color(tab.tab_id),
                event.message,
            )
        dock.show_error(
            tab.run_id,
            message=event.message,
            row=event.data.get("row"),
            quantity=event.data.get("quantity"),
            quantity_action=event.data.get("quantity_action", False),
            post_payment_warning=event.data.get("post_payment_warning", False),
            can_continue=event.data.get("can_continue", False),
            next_checkpoint_title=event.data.get("next_checkpoint_title", ""),
            next_checkpoint_instruction=event.data.get("next_checkpoint_instruction", ""),
        )
        return True

    def update_status_dock_progress(
        self, tab: AutomationTab, row: int | None, quantity: int | None
    ) -> None:
        dock = self.automation_status_window
        if dock is not None and dock.exists:
            dock.set_progress(tab.run_id, row, quantity)

    def update_status_dock_payment(self, tab: AutomationTab, state: str) -> None:
        dock = self.automation_status_window
        if dock is None or not dock.exists:
            return
        if state in {"slot_granted", "pay_now_ready", "qr_ready", "foreground_verified"}:
            dock.set_payment_active(tab.run_id, True)
        elif state in {"slot_released", "download_ready"}:
            dock.set_payment_active(tab.run_id, False)

    def _add_tab(self) -> None:
        self._record_ui_action("add_id_clicked")
        tab_config = self.config.create_tab()
        self.save_config()
        tab = self._add_tab_widget(tab_config)
        self.notebook.select(str(tab.frame))  # type: ignore[no-untyped-call]

    def _selected_tab(self) -> AutomationTab | None:
        selected = self.notebook.select()  # type: ignore[no-untyped-call]
        for tab in self.tabs.values():
            if str(tab.frame) == str(selected):
                return tab
        return None

    def _export_payment_transactions(self) -> None:
        if self.transaction_export_running:
            messagebox.showinfo(
                "Export payment transactions",
                "A payment transaction export is already running.",
                parent=self.root,
            )
            return
        tab = self._selected_tab()
        if tab is None:
            return
        if tab.is_active or tab.portal_session_open:
            messagebox.showwarning(
                "Export payment transactions",
                f"Stop {tab.display_name} before using its portal profile for this export.",
                parent=self.root,
            )
            return
        browser = tab._selected_browser()
        if browser is None:
            messagebox.showwarning(
                "Export payment transactions",
                "Choose an installed portal browser for the selected ID first.",
                parent=self.root,
            )
            return
        output_path = self._transaction_export_path()
        if output_path is None:
            return

        profile_slug = re.sub(r"[^a-z0-9]+", "-", browser.name.casefold()).strip("-") or "browser"
        profile_path = Path(tab.config.portal_profile_path) / browser.engine.value / profile_slug
        self.transaction_export_running = True
        tab.portal_session_open = True
        tab._set_buttons()
        self._record_ui_action(f"id_{tab.run_id}_export_payment_transactions_clicked")
        self.run_status_var.set(f"{tab.display_name}: waiting for manual Citizen login...")

        def report_status(message: str) -> None:
            self.root.after(0, lambda: self.run_status_var.set(f"{tab.display_name}: {message}"))

        def run_export() -> None:
            try:
                summary = asyncio.run(
                    export_payment_transactions(browser, profile_path, output_path, report_status)
                )
            except Exception as error:
                error_message = str(error)
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "Export payment transactions", error_message, parent=self.root
                    ),
                )
                report_status("payment transaction export failed")
            else:
                message = (
                    f"Saved {summary.appended_rows} new transaction(s) to:\n{summary.output_path}\n\n"
                    f"Skipped {summary.skipped_duplicates} duplicate(s)."
                )
                self.root.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Export payment transactions", message, parent=self.root
                    ),
                )
                report_status(message)
            finally:
                self.root.after(0, lambda: self._finish_transaction_export(tab))

        threading.Thread(target=run_export, name="payment-transaction-export", daemon=True).start()

    def _transaction_export_path(self) -> Path | None:
        configured = self.config.transaction_export_path.strip()
        previous_path = Path(configured).expanduser() if configured else None
        selected = filedialog.asksaveasfilename(
            title="Choose eStamp payment transactions CSV",
            initialdir=(
                previous_path.parent
                if previous_path is not None and previous_path.parent.is_dir()
                else default_download_directory()
            ),
            initialfile=(
                previous_path.name if previous_path is not None else "estamp_payment_transactions.csv"
            ),
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            confirmoverwrite=False,
            parent=self.root,
        )
        if not selected:
            return None
        self.config.transaction_export_path = selected
        self.save_config()
        return Path(selected)

    def _finish_transaction_export(self, tab: AutomationTab) -> None:
        self.transaction_export_running = False
        tab.portal_session_open = False
        tab._set_buttons()

    def _compare_transactions_with_stamps(self) -> None:
        self._record_ui_action("compare_transactions_with_stamps_clicked")
        transaction_csv = filedialog.askopenfilename(
            title="Choose exported payment transactions CSV",
            filetypes=[("CSV files", "*.csv")],
            parent=self.root,
        )
        if not transaction_csv:
            return
        stamps_directory = filedialog.askdirectory(
            title="Choose the folder containing downloaded eStamp PDFs",
            parent=self.root,
            mustexist=True,
        )
        if not stamps_directory:
            return
        try:
            report = reconcile_transactions(Path(transaction_csv), Path(stamps_directory))
        except Exception as error:
            messagebox.showerror("Compare transactions and stamps", str(error), parent=self.root)
            return
        self._show_transaction_reconciliation(report)

    def _show_transaction_reconciliation(self, report: TransactionReconciliation) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Transaction and stamp comparison")
        dialog.geometry("980x620")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=(
                f"{len(report.missing_pdfs)} transaction(s) without a PDF | "
                f"{len(report.unmatched_pdfs)} PDF(s) without a CSV transaction"
            ),
            style="Status.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)
        missing_text = tk.Text(notebook, wrap="none", font=("Consolas", 9))
        unmatched_text = tk.Text(notebook, wrap="none", font=("Consolas", 9))
        notebook.add(missing_text, text=f"Transactions without PDF ({len(report.missing_pdfs)})")
        notebook.add(unmatched_text, text=f"PDFs without CSV transaction ({len(report.unmatched_pdfs)})")

        missing_text.insert("1.0", "\n".join(self._missing_pdf_lines(report)) or "No missing PDFs.")
        unmatched_text.insert(
            "1.0",
            "\n".join(self._unmatched_pdf_lines(report)) or "No unmatched PDFs.",
        )
        missing_text.configure(state="disabled")
        unmatched_text.configure(state="disabled")

        buttons = ttk.Frame(frame)
        buttons.pack(anchor="w", pady=(10, 0))
        ttk.Button(
            buttons,
            text="Export CSV...",
            command=lambda: self._export_reconciliation_csv(report),
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="Export text...",
            command=lambda: self._export_reconciliation_text(report),
        ).pack(side="left", padx=(7, 0))
        ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side="left", padx=(7, 0))

    @staticmethod
    def _missing_pdf_lines(report: TransactionReconciliation) -> list[str]:
        return [
            " | ".join(
                f"{header}: {value}"
                for header, value in zip(report.headers, item.values, strict=True)
                if value
            )
            for item in report.missing_pdfs
        ]

    @staticmethod
    def _unmatched_pdf_lines(report: TransactionReconciliation) -> list[str]:
        return [
            f"Transaction ID: {item.transaction_id or '(not found)'} | Path: {item.path}"
            for item in report.unmatched_pdfs
        ]

    def _export_reconciliation_csv(self, report: TransactionReconciliation) -> None:
        selected = filedialog.asksaveasfilename(
            title="Export transaction and stamp comparison as CSV",
            defaultextension=".csv",
            initialfile="transaction_stamp_comparison.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self.root,
        )
        if not selected:
            return
        try:
            write_reconciliation_csv(Path(selected), report)
        except Exception as error:
            messagebox.showerror("Export comparison CSV", str(error), parent=self.root)
            return
        messagebox.showinfo(
            "Export comparison CSV", f"Saved comparison report to:\n{selected}", parent=self.root
        )

    def _export_reconciliation_text(self, report: TransactionReconciliation) -> None:
        selected = filedialog.asksaveasfilename(
            title="Export transaction and stamp comparison as text",
            defaultextension=".txt",
            initialfile="transaction_stamp_comparison.txt",
            filetypes=[("Text files", "*.txt")],
            parent=self.root,
        )
        if not selected:
            return
        try:
            write_reconciliation_text(Path(selected), report)
        except Exception as error:
            messagebox.showerror("Export comparison text", str(error), parent=self.root)
            return
        messagebox.showinfo(
            "Export comparison text", f"Saved comparison report to:\n{selected}", parent=self.root
        )

    def _remove_selected_tab(self) -> None:
        self._record_ui_action("remove_id_clicked")
        tab = self._selected_tab()
        if tab is None:
            return
        if tab.tab_id == 1:
            messagebox.showinfo(
                "Remove ID", "ID 1 is the default tab and cannot be removed.", parent=self.root
            )
            return
        if tab.is_active or tab.portal_session_open:
            messagebox.showwarning(
                "ID is active",
                "Stop this ID and close its portal browser before removing it.",
                parent=self.root,
            )
            return
        # Do not remove the profile directory. It is deliberately persistent.
        self.tabs.pop(tab.tab_id, None)
        self.tab_states.pop(tab.tab_id, None)
        if self.automation_status_window is not None:
            self.automation_status_window.remove_run(tab.run_id)
        self.tab_accent_images.pop((tab.tab_id, True), None)
        self.tab_accent_images.pop((tab.tab_id, False), None)
        self.config.tabs[:] = [item for item in self.config.tabs if item.tab_id != tab.tab_id]
        tab.destroy()
        self.save_config()
        self._update_summary()

    def _start_all(self) -> None:
        self._record_ui_action("start_all_clicked")
        started = 0
        disabled = 0
        for tab in self.tabs.values():
            if not tab.is_enabled:
                disabled += 1
                continue
            if tab.start(show_errors=False):
                started += 1
        self.run_status_var.set(f"Started {started} ID(s); skipped {disabled} disabled")
        self._update_summary()

    def _stop_all(self) -> None:
        self._record_ui_action("stop_all_clicked")
        self.controller.stop_all()
        self.run_status_var.set("Stopping active IDs...")

    def save_config(self) -> None:
        self.config.chrome_executable = self.chrome_var.get().strip()
        self.config.chrome_profile_path = self.profile_var.get().strip()
        # ConfigStore keeps deprecated flat fields for migration. Refresh
        # those fields from tab 1 before it writes them back.
        self.config._sync_legacy_fields_from_tab()
        self.config_store.save(self.config)

    def active_tab_for_csv(self, path: Path, excluding: int | None = None) -> AutomationTab | None:
        try:
            wanted = str(path.expanduser().resolve()).casefold()
        except OSError:
            wanted = str(path).casefold()
        for tab in self.tabs.values():
            if excluding is not None and tab.tab_id == excluding:
                continue
            if not tab.is_active:
                continue
            candidate = tab.csv_var.get().strip()
            if not candidate:
                continue
            try:
                current = str(Path(candidate).expanduser().resolve()).casefold()
            except OSError:
                current = candidate.casefold()
            if current == wanted:
                return tab
        return None

    def update_tab_state(self, tab: AutomationTab, state: str, detail: str = "") -> None:
        if tab.tab_id not in self.tabs:
            return
        self.tab_states[tab.tab_id] = state
        caption = f"{tab.display_name} - {state}" if state else tab.display_name
        accent = self._tab_accent_image(tab.tab_id, enabled=tab.is_enabled)
        self.notebook.tab(  # type: ignore[no-untyped-call]
            str(tab.frame), text=caption, image=accent
        )
        dock = self.automation_status_window
        if state == "Starting" and tab.is_enabled:
            dock = self._ensure_status_dock()
            dock.begin_run(
                tab.run_id,
                tab.display_name,
                self._tab_accent_color(tab.tab_id),
                detail or "Preparing this automation session...",
            )
        elif dock is not None and dock.exists and dock.has_run(tab.run_id):
            if state in {"Stopped", "Disabled"} and not tab.browser_recovery_pending:
                dock.remove_run(tab.run_id)
            else:
                dock.set_status(tab.run_id, state, detail)
        if dock is not None and dock.exists and dock.has_run(tab.run_id):
            dock.set_controls(
                tab.run_id,
                running=tab.running,
                starting=tab.starting,
                paused=tab.paused,
                auto_waiting=tab.auto_waiting,
                portal_open=tab.portal_session_open,
            )
        self._update_summary()

    def _tab_changed(self, _event: object = None) -> None:
        self._update_summary()

    def _update_summary(self) -> None:
        active = sum(tab.is_active for tab in self.tabs.values())
        paused = sum(tab.paused for tab in self.tabs.values())
        complete = sum(self.tab_states.get(tab.tab_id) == "Complete" for tab in self.tabs.values())
        errors = sum(
            self.tab_states.get(tab.tab_id) in {"Error", "Needs setup", "Browser closed"}
            for tab in self.tabs.values()
        )
        disabled = sum(not tab.is_enabled for tab in self.tabs.values())
        idle = max(0, len(self.tabs) - active - complete - errors - disabled)
        self.summary_var.set(
            f"{active} active | {paused} paused | {complete} complete | "
            f"{errors} attention | {disabled} disabled | {idle} idle"
        )

    def append_session_log(self, message: str) -> None:
        if message.strip():
            self.session_log_lines.append(f"{datetime.now():%H:%M:%S}  {message.strip()}")

    def _record_ui_action(self, action: str) -> None:
        self.controller.record_activity(action)
        self.append_session_log(action.replace("_", " ").title())

    def _reset_global_success_count(self) -> None:
        self.global_success_count = 0
        self.global_success_var.set("Session downloads: 0")
        self.append_session_log("Session download counter reset to zero.")

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.controller.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        finally:
            if not self._closing:
                self.root.after(100, self._drain_events)

    def _handle_event(self, event: UiEvent) -> None:
        run_id = str(event.run_id or event.data.get("run_id", ""))
        tab = self.tabs.get(int(run_id)) if run_id.isdigit() else None
        if tab is not None:
            tab.handle_event(event)
            if event.kind == "batch_update":
                self.update_status_dock_progress(
                    tab,
                    event.data.get("current_row"),
                    event.data.get("current_unit"),
                )
            elif event.kind == "payment_state":
                state = str(event.data.get("state", ""))
                self.update_status_dock_payment(tab, state)
                if state == "download_ready":
                    self.global_success_count += 1
                    self.global_success_var.set(
                        f"Session downloads: {self.global_success_count}"
                    )
                    dock = self.automation_status_window
                    if dock is not None and dock.exists:
                        dock.increment_download_count(tab.run_id)
            elif event.kind in {
                "run_completed",
                "run_stopped",
                "browser_closed",
                "portal_closed",
                "fatal_error",
            }:
                self.update_status_dock_payment(tab, "slot_released")
            self._update_summary()
            return
        self._handle_global_event(event)

    def _handle_global_event(self, event: UiEvent) -> None:
        if event.message:
            self.append_session_log(event.message)
        if event.kind == "gemini_verified":
            self.gemini_ready = True
            self.gemini_checking = False
            self.config.gemini_verified = True
            self.save_config()
            self.run_status_var.set("OCR is ready")
        elif event.kind == "gemini_not_ready":
            self.gemini_ready = False
            self.gemini_checking = False
            self.config.gemini_verified = False
            self.save_config()
            self.run_status_var.set("OCR is not ready")
        elif event.kind == "ocr_test_started":
            self.ocr_test_running = True
            self.run_status_var.set("Testing OCR...")
        elif event.kind in {"ocr_test_succeeded", "ocr_test_failed"}:
            self.ocr_test_running = False
            result = "OCR test passed" if event.kind == "ocr_test_succeeded" else "OCR test failed"
            self.run_status_var.set(result)
            if event.kind == "ocr_test_failed":
                messagebox.showerror("CAPTCHA OCR test failed", event.message, parent=self.root)
        elif event.kind == "ocr_test_progress":
            self.run_status_var.set(event.message)
        elif event.kind == "fatal_error":
            self.run_status_var.set("Automation error")
            messagebox.showerror("Automation error", event.message, parent=self.root)
        elif event.kind == "browser_closed":
            if event.data.get("profile_browser"):
                self.gemini_ready = False
                self.config.gemini_verified = False
                self.save_config()
                self.run_status_var.set("OCR browser closed")
            else:
                self.run_status_var.set("Portal browser closed")
                messagebox.showwarning("Browser closed", event.message, parent=self.root)

    def _update_managed_firefox_menu(self) -> None:
        if self.managed_firefox_downloading:
            label, state = "Downloading managed Firefox...", "disabled"
        elif managed_firefox_is_installed():
            label, state = "Managed Firefox downloaded", "disabled"
        else:
            label, state = "Download managed Firefox", "normal"
        self.application_menu.entryconfigure(self.managed_firefox_menu_index, label=label, state=state)

    def _download_managed_firefox(self) -> None:
        self._record_ui_action("download_managed_firefox_clicked")
        if not messagebox.askyesno(
            "Download managed Firefox",
            "Download the Firefox build used for portal automation?\n\n"
            f"It will be saved in:\n{browser_install_directory()}",
            parent=self.root,
        ):
            return
        self.managed_firefox_downloading = True
        self._update_managed_firefox_menu()
        self.run_status_var.set("Downloading managed Firefox...")

        def download() -> None:
            try:
                install_managed_firefox()
            except Exception as error:
                self.root.after(0, self._managed_firefox_download_finished, error)
            else:
                self.root.after(0, self._managed_firefox_download_finished, None)

        threading.Thread(target=download, name="managed-firefox-download", daemon=True).start()

    def _managed_firefox_download_finished(self, error: Exception | None) -> None:
        self.managed_firefox_downloading = False
        self._update_managed_firefox_menu()
        if error is not None:
            self.run_status_var.set("Managed Firefox download failed")
            messagebox.showerror("Firefox download failed", str(error), parent=self.root)
            return
        self.run_status_var.set("Managed Firefox downloaded")
        self.append_session_log("Managed Firefox downloaded.")
        messagebox.showinfo(
            "Managed Firefox ready", "Managed Firefox is ready for portal automation.", parent=self.root
        )

    def _download_template(self) -> None:
        self._record_ui_action("download_csv_format_clicked")
        selected = filedialog.asksaveasfilename(
            title="Save CSV format",
            defaultextension=".csv",
            initialfile="estamp_batch_template.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self.root,
        )
        if not selected:
            return
        try:
            CsvBatchStore.write_template(Path(selected))
            self.append_session_log("CSV template saved.")
            messagebox.showinfo(
                "CSV format", "Fill the template, then select it in an ID tab.", parent=self.root
            )
        except Exception as error:
            messagebox.showerror("Template error", str(error), parent=self.root)

    def _show_ocr_profile_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("OCR browser profile settings")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Chrome executable").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(frame, textvariable=self.chrome_var, width=62).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(frame, text="Browse...", command=self._browse_chrome).grid(row=0, column=2, padx=(7, 0))
        ttk.Label(frame, text="Profile path").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(frame, textvariable=self.profile_var, width=62).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=4
        )

        def save() -> None:
            if not self.chrome_var.get().strip() or not self.profile_var.get().strip():
                messagebox.showwarning(
                    "OCR profile", "Enter both a Chrome executable and profile path.", parent=dialog
                )
                return
            self.save_config()
            self.controller.reconfigure_browser()
            self.append_session_log("OCR browser profile settings saved.")
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Save", command=save).pack(side="left")
        ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side="left", padx=(7, 0))

    def _browse_chrome(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose Chrome executable",
            filetypes=[("Chrome executable", "chrome.exe"), ("Executables", "*.exe")],
            parent=self.root,
        )
        if selected:
            self.chrome_var.set(selected)
            self.save_config()
            self.controller.reconfigure_browser()

    def _delete_ocr_profile(self) -> None:
        configured = self.profile_var.get().strip()
        if not configured:
            messagebox.showinfo("Delete OCR profile", "No OCR profile path is selected.", parent=self.root)
            return
        try:
            profile = Path(configured).expanduser().resolve()
        except OSError as error:
            messagebox.showerror("Delete OCR profile", str(error), parent=self.root)
            return
        if not profile.is_dir():
            messagebox.showinfo(
                "Delete OCR profile", "The selected profile folder does not exist.", parent=self.root
            )
            return
        protected = (
            Path(profile.anchor),
            Path.home().resolve(),
            Path.cwd().resolve(),
            app_data_directory().resolve(),
        )
        if any(item == profile or item.is_relative_to(profile) for item in protected):
            messagebox.showerror("Delete OCR profile", "This path is too broad to delete.", parent=self.root)
            return
        if not messagebox.askyesno(
            "Delete OCR profile",
            f"Permanently remove this Chrome profile and its sign-in data?\n\n{profile}",
            parent=self.root,
            default=messagebox.NO,
        ):
            return
        self.controller.close_gemini_ocr()
        try:
            shutil.rmtree(profile)
        except OSError as error:
            messagebox.showerror("Delete OCR profile", str(error), parent=self.root)
            return
        self.gemini_ready = False
        self.config.gemini_verified = False
        self.save_config()
        self.append_session_log("OCR browser profile deleted.")

    def _show_session_log(self) -> None:
        self._record_ui_action("view_current_session_log_clicked")
        dialog = tk.Toplevel(self.root)
        dialog.title("Current session activity")
        dialog.geometry("760x460")
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="word", state="normal", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.insert("1.0", "\n".join(self.session_log_lines) or "No activity yet.")
        text.configure(state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _open_log_folder(self) -> None:
        self._record_ui_action("open_daily_log_folder_clicked")
        directory = self.controller.activity_log.directory
        directory.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer.exe", str(directory)])

    def _open_captcha_folder(self) -> None:
        self._record_ui_action("open_captcha_folder_clicked")
        directory = app_data_directory() / "captchas"
        directory.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer.exe", str(directory)])

    def _on_close(self) -> None:
        active = [tab for tab in self.tabs.values() if tab.is_active or tab.portal_session_open]
        if active and not messagebox.askyesno(
            "Exit application",
            "Automation is active. Stop all IDs and close their portal browsers?",
            parent=self.root,
            default=messagebox.NO,
        ):
            return
        self.controller.record_activity("application_closed", "Desktop application closed.")
        self._closing = True
        if self.automation_status_window is not None:
            self.automation_status_window.close()
        self.controller.shutdown()
        self.root.destroy()
