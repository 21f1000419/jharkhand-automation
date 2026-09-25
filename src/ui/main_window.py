from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

from core.browser_detection import detect_supported_browsers
from core.config import (
    DEFAULT_SMS_SERVER_URL,
    AppConfig,
    ConfigStore,
    TabConfig,
    app_data_directory,
    default_download_directory,
)
from core.controller import AutomationController
from core.form_options import ARTICLE_OPTIONS
from core.models import (
    BrowserEngine,
    CaptchaCopyMode,
    PortalBrowser,
    RunMode,
    UiEvent,
    available_ocr_engines,
)
from core.playwright_browsers import (
    browser_install_directory,
    install_managed_firefox,
    managed_firefox_is_installed,
)
from services.config_package import (
    apply_imported_package,
    create_export_package,
    export_package_to_file,
    import_package_from_file,
)
from services.credential_store import CredentialStore
from services.csv_store import CsvBatchStore
from services.qr_images import clear_qr_image_directory, remove_qr_file
from services.transaction_reconciliation import (
    TransactionReconciliation,
    write_reconciliation_csv,
    write_reconciliation_text,
)
from ui.automation_status import AutomationStatusWindow
from ui.download_certificates_dialog import DownloadUndownloadedCertificatesDialog
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

# Tk expects multiple extensions as separate patterns. A semicolon-delimited
# Windows pattern can crash the native macOS file dialog inside Cocoa/Tk.
CONFIG_PACKAGE_FILE_TYPES = (
    ("eStamp Config Package (*.estampcfg, *.ecfg)", ("*.estampcfg", "*.ecfg")),
    ("All files", "*.*"),
)


@dataclass
class QrItem:
    qr_id: str
    file_path: Path
    created_at: float
    expires_at: float
    run_id: str
    dock_id: str
    worker_label: str
    row: int
    quantity: int
    paid: bool = False


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
        self.tab_details: dict[int, str] = {}
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

        run_config = config.run_config
        self.chrome_var = tk.StringVar(value=config.chrome_executable)
        self.profile_var = tk.StringVar(value=config.chrome_profile_path)
        self.csv_var = tk.StringVar(value=run_config.last_csv_path)
        self.download_var = tk.StringVar(value=run_config.last_download_path)
        self.article_var = tk.StringVar(value=run_config.last_article)
        self.payment_trigger_url_var = tk.StringVar(value=run_config.payment_trigger_url)
        self.payment_trigger_method_var = tk.StringVar(value=run_config.payment_trigger_method)
        self.mode_var = tk.StringVar(value=run_config.last_mode)
        self.ocr_engine_var = tk.StringVar(value=run_config.ocr_engine)
        self.ocr_enabled_var = tk.BooleanVar(value=run_config.ocr_enabled)
        self.captcha_copy_mode_var = tk.StringVar(value=run_config.captcha_copy_mode)
        self.save_captcha_images_var = tk.BooleanVar(value=run_config.save_captcha_images)
        self.fresh_browser_var = tk.BooleanVar(value=run_config.fresh_browser_per_unit)
        self.retry_egras_otp_once_var = tk.BooleanVar(value=run_config.retry_egras_otp_once)
        self.sms_server_url_var = tk.StringVar(value=run_config.sms_server_url or DEFAULT_SMS_SERVER_URL)
        self.portal_browser_var = tk.StringVar(value="")
        self.run_status_var = tk.StringVar(value="Idle")
        self.summary_var = tk.StringVar(value="0 active | 0 idle")
        self.run_summary_var = self.summary_var
        self.global_success_count = 0
        self.global_success_var = tk.StringVar(value="Session downloads: 0")
        self.csv_valid = False
        self.selected_id: int | None = None
        self.id_buttons: dict[int, tk.Button] = {}
        self.id_settings_dialog: tk.Toplevel | None = None
        self.id_settings_notebook: ttk.Notebook | None = None
        self.id_settings_root_resize_binding: str | None = None
        self.id_settings_pages: dict[int, ttk.Frame] = {}
        self.id_settings_editors: dict[int, dict[str, Any]] = {}
        self.provisional_id_tabs: set[int] = set()
        self.qr_items: list[QrItem] = []
        self.qr_index = -1
        self.qr_photo: ImageTk.PhotoImage | None = None
        self.worker_assignments: dict[str, tuple[int, int, str]] = {}

        self._detect_portal_browsers()
        self.portal_browser_var.set(self._saved_browser_name())
        clear_qr_image_directory()
        self._build()
        self._build_menu()
        for tab_config in sorted(config.tabs, key=lambda item: item.tab_id):
            self._add_tab_widget(tab_config)
        self.load_batch_preview(quiet=True)
        self.refresh_id_strip()
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
            text="One shared batch, with separate credentials, profiles, and browser counts for each ID.",
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

        self._build_global_run_configuration(container)
        self._build_workspace(container)
        self._build_id_strip(container)
        self.hidden_id_host = ttk.Frame(container)
        self.root.after(1000, self._tick_qr_carousel)

    def _build_global_run_configuration(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent)
        panel.pack(fill="x", pady=(0, 6))
        self.global_config_widgets: list[Any] = []

        first_row = ttk.Frame(panel)
        first_row.pack(fill="x", pady=(0, 2))
        ttk.Label(first_row, text="Browser").pack(side="left")
        self.portal_browser_box = ttk.Combobox(
            first_row,
            textvariable=self.portal_browser_var,
            values=[*self.portal_browsers, self.CUSTOM_BROWSER_OPTION],
            state="readonly",
        )
        self.portal_browser_box.pack(side="left", fill="x", expand=True, padx=(6, 12))
        self.portal_browser_box.bind("<<ComboboxSelected>>", self._browser_selected)
        ttk.Label(first_row, text="Article").pack(side="left")
        article = ttk.Combobox(first_row, textvariable=self.article_var, values=ARTICLE_OPTIONS)
        self.article_box = article
        article.pack(side="left", fill="x", expand=True, padx=(6, 0))

        paths_row = ttk.Frame(panel)
        paths_row.pack(fill="x", pady=2)
        csv_group = ttk.Frame(paths_row)
        csv_group.pack(side="left", fill="x", expand=True, padx=(0, 12))
        ttk.Label(csv_group, text="CSV").pack(side="left")
        csv_entry = ttk.Entry(csv_group, textvariable=self.csv_var)
        csv_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        csv_button = ttk.Button(csv_group, text="Select...", command=self._browse_csv)
        csv_button.pack(side="left")
        download_group = ttk.Frame(paths_row)
        download_group.pack(side="left", fill="x", expand=True)
        ttk.Label(download_group, text="Downloads").pack(side="left")
        download_entry = ttk.Entry(download_group, textvariable=self.download_var)
        download_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        download_button = ttk.Button(download_group, text="Browse...", command=self._browse_download)
        download_button.pack(side="left")

        services_row = ttk.Frame(panel)
        services_row.pack(fill="x", pady=2)
        sms_group = ttk.Frame(services_row)
        sms_group.pack(side="left", fill="x", expand=True, padx=(0, 16))
        ttk.Label(sms_group, text="SMS server").pack(side="left")
        sms_server = ttk.Entry(sms_group, textvariable=self.sms_server_url_var)
        sms_server.pack(side="left", fill="x", expand=True, padx=(6, 0))
        trigger_group = ttk.Frame(services_row)
        trigger_group.pack(side="right")
        ttk.Label(trigger_group, text="Trigger").pack(side="left")
        trigger_entry = ttk.Entry(
            trigger_group,
            textvariable=self.payment_trigger_url_var,
            width=24,
        )
        trigger_entry.pack(side="left", padx=(6, 4))
        trigger_method = ttk.Combobox(
            trigger_group,
            textvariable=self.payment_trigger_method_var,
            values=("GET", "POST"),
            state="readonly",
            width=5,
        )
        trigger_method.pack(side="left")

        options = ttk.Frame(panel)
        options.pack(fill="x", pady=2)
        ttk.Label(options, text="Mode").pack(side="left")
        mode = ttk.Combobox(
            options,
            textvariable=self.mode_var,
            values=[item.value for item in RunMode],
            state="readonly",
            width=12,
        )
        mode.pack(side="left", padx=(4, 12))
        fresh = ttk.Checkbutton(options, text="New browser per unit", variable=self.fresh_browser_var)
        fresh.pack(side="left")
        save_captchas = ttk.Checkbutton(options, text="Save CAPTCHAs", variable=self.save_captcha_images_var)
        save_captchas.pack(side="left", padx=(12, 0))
        retry_otp = ttk.Checkbutton(
            options, text="Retry eGRAS OTP once", variable=self.retry_egras_otp_once_var
        )
        retry_otp.pack(side="left", padx=(12, 0))
        use_ocr = ttk.Checkbutton(options, text="Use OCR", variable=self.ocr_enabled_var)
        use_ocr.pack(side="left", padx=(12, 0))
        ocr = ttk.Combobox(
            options,
            textvariable=self.ocr_engine_var,
            values=[item.value for item in available_ocr_engines()],
            state="readonly",
            width=11,
        )
        ocr.pack(side="left", padx=(4, 10))
        ttk.Label(options, text="CAPTCHA copy").pack(side="left")
        copy_mode = ttk.Combobox(
            options,
            textvariable=self.captcha_copy_mode_var,
            values=[item.value for item in CaptchaCopyMode],
            state="readonly",
            width=12,
        )
        copy_mode.pack(side="left", padx=(4, 0))

        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(4, 0))
        ttk.Button(actions, text="Start All", command=self._start_all).pack(side="left")
        self.start_menu = tk.Menu(actions, tearoff=False)
        self.start_menu_button = ttk.Menubutton(actions, text="Start ID", menu=self.start_menu)
        self.start_menu_button.pack(side="left", padx=(7, 0))
        ttk.Button(actions, text="Stop All", command=self._stop_all).pack(side="left", padx=(7, 0))
        ttk.Button(actions, text="Show status", command=self._show_status_dock).pack(
            side="left", padx=(16, 0)
        )
        ttk.Button(actions, text="Refresh browsers", command=self._refresh_portal_browsers).pack(
            side="left", padx=(7, 0)
        )
        ttk.Label(actions, textvariable=self.run_status_var, foreground="#555555").pack(side="right")

        self.global_config_widgets.extend(
            [
                self.portal_browser_box,
                article,
                csv_entry,
                csv_button,
                download_entry,
                download_button,
                trigger_entry,
                trigger_method,
                mode,
                fresh,
                save_captchas,
                retry_otp,
                use_ocr,
                ocr,
                copy_mode,
                sms_server,
            ]
        )
        for variable in (
            self.article_var,
            self.download_var,
            self.payment_trigger_url_var,
            self.payment_trigger_method_var,
            self.mode_var,
            self.ocr_enabled_var,
            self.ocr_engine_var,
            self.captcha_copy_mode_var,
            self.save_captcha_images_var,
            self.fresh_browser_var,
            self.retry_egras_otp_once_var,
            self.sms_server_url_var,
        ):
            variable.trace_add("write", self._global_settings_changed)

    def _build_workspace(self, parent: ttk.Frame) -> None:
        pane = ttk.Panedwindow(parent, orient="horizontal")
        pane.pack(fill="both", expand=True, pady=(0, 8))
        table_panel = ttk.Frame(pane, padding=(0, 0, 6, 0))
        qr_panel = ttk.Frame(pane, padding=(10, 0, 0, 0))
        pane.add(table_panel, weight=1)
        pane.add(qr_panel, weight=1)

        def balance_workspace(event: tk.Event[tk.Misc]) -> None:
            if event.width > 1:
                pane.sashpos(0, event.width // 2)  # type: ignore[no-untyped-call]

        pane.bind("<Configure>", balance_workspace)

        table_panel.rowconfigure(0, weight=1)
        table_panel.columnconfigure(0, weight=1)
        columns = ("row", "first", "second", "amount", "status", "completed", "assigned", "error")
        self.batch_tree = ttk.Treeview(table_panel, columns=columns, show="headings", height=12)
        headings = {
            "row": "CSV row",
            "first": "First party",
            "second": "Second party",
            "amount": "Amount",
            "status": "Status",
            "completed": "Success / Processed / Total",
            "assigned": "Assigned to",
            "error": "Last error",
        }
        widths = {
            "row": 60,
            "first": 135,
            "second": 135,
            "amount": 75,
            "status": 88,
            "completed": 145,
            "assigned": 145,
            "error": 210,
        }
        for column in columns:
            self.batch_tree.heading(column, text=headings[column])
            self.batch_tree.column(
                column, width=widths[column], stretch=column in {"first", "second", "error"}
            )
        self.batch_tree.tag_configure("active", background="#dbeafe")
        self.batch_tree.tag_configure("completed", background="#e5e7eb", foreground="#555555")
        scrollbar = ttk.Scrollbar(table_panel, orient="vertical", command=self.batch_tree.yview)
        self.batch_tree.configure(yscrollcommand=scrollbar.set)
        self.batch_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        qr_panel.columnconfigure(0, weight=1)
        qr_panel.rowconfigure(1, weight=1)
        self.qr_title_var = tk.StringVar(value="Waiting for payment QR codes")
        self.qr_detail_var = tk.StringVar(value="New QR codes will appear here without focusing the browser.")
        self.qr_title_label = ttk.Label(
            qr_panel,
            textvariable=self.qr_title_var,
            style="Status.TLabel",
            anchor="center",
        )
        self.qr_title_label.grid(row=0, column=0, sticky="ew")
        qr_image_host = ttk.Frame(qr_panel)
        qr_image_host.grid(row=1, column=0, sticky="nsew", pady=8)
        qr_image_host.rowconfigure(0, weight=1)
        qr_image_host.columnconfigure(0, weight=1)
        self.qr_border_frame = tk.Frame(qr_image_host, background="#d1d5db", padx=5, pady=5)
        self.qr_border_frame.grid(row=0, column=0)
        self.qr_image_label = tk.Label(self.qr_border_frame, anchor="center", background="white")
        self.qr_image_label.pack()
        ttk.Label(qr_panel, textvariable=self.qr_detail_var, anchor="center", wraplength=360).grid(
            row=2, column=0, sticky="ew"
        )
        nav = ttk.Frame(qr_panel)
        nav.grid(row=3, column=0, pady=(10, 0))
        self.qr_previous_button = ttk.Button(
            nav, text="‹ Previous", command=lambda: self._move_qr(-1), state="disabled"
        )
        self.qr_previous_button.pack(side="left")
        self.qr_position_var = tk.StringVar(value="0 / 0")
        ttk.Label(nav, textvariable=self.qr_position_var, width=9, anchor="center").pack(side="left", padx=8)
        self.qr_next_button = ttk.Button(
            nav, text="Next ›", command=lambda: self._move_qr(1), state="disabled"
        )
        self.qr_next_button.pack(side="left")
        self.qr_paid_button = ttk.Button(
            nav,
            text="Mark paid",
            command=self._mark_current_qr_paid,
            state="disabled",
        )
        self.qr_paid_button.pack(side="left", padx=(12, 0))

    def _build_id_strip(self, parent: ttk.Frame) -> None:
        self.id_strip = ttk.Frame(parent)
        self.id_strip.pack(fill="x", pady=(2, 0))

    def _global_settings_changed(self, *_args: object) -> None:
        self.root.after_idle(self.save_global_settings)

    def save_global_settings(self) -> None:
        run = self.config.run_config
        run.last_download_path = self.download_var.get().strip()
        run.last_mode = self.mode_var.get()
        run.sms_server_url = self.sms_server_url_var.get().strip() or DEFAULT_SMS_SERVER_URL
        run.payment_trigger_url = self.payment_trigger_url_var.get().strip()
        run.payment_trigger_method = self.payment_trigger_method_var.get().strip().upper()
        run.captcha_copy_mode = self.captcha_copy_mode_var.get()
        run.ocr_engine = self.ocr_engine_var.get()
        run.ocr_enabled = self.ocr_enabled_var.get()
        run.last_article = self.article_var.get().strip()
        run.last_csv_path = self.csv_var.get().strip()
        run.save_captcha_images = self.save_captcha_images_var.get()
        run.fresh_browser_per_unit = self.fresh_browser_var.get()
        run.retry_egras_otp_once = self.retry_egras_otp_once_var.get()
        browser = self.selected_portal_browser()
        if browser is not None:
            run.last_portal_browser_path = str(browser.executable)
        self.save_config()

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root, tearoff=False)
        self.application_menu = menu

        # Aqua Tk only supports cascade entries in the macOS menu bar. Direct
        # commands added to the top-level menu are silently omitted, so keep
        # all download actions in a submenu on every platform.
        downloads_menu = tk.Menu(menu, tearoff=False)
        self.downloads_menu = downloads_menu
        downloads_menu.add_command(
            label="Download managed Firefox", command=self._download_managed_firefox
        )
        self.managed_firefox_menu_index = 0
        self._update_managed_firefox_menu()
        downloads_menu.add_command(
            label="Download CSV format...", command=self._download_template
        )
        downloads_menu.add_command(
            label="Download Undownloaded Certificates...",
            command=self._open_download_undownloaded_certificates_dialog,
        )
        menu.add_cascade(label="Downloads", menu=downloads_menu)

        config_menu = tk.Menu(menu, tearoff=False)
        config_menu.add_command(
            label="Configure IDs...", command=self._show_selected_id_settings
        )
        config_menu.add_separator()
        config_menu.add_command(label="Export all IDs & settings...", command=self._export_all_configs)
        config_menu.add_command(
            label="Export selected ID configuration...", command=self._export_selected_config
        )
        config_menu.add_separator()
        config_menu.add_command(label="Import IDs & settings...", command=self._import_configs)
        menu.add_cascade(label="IDs & Settings", menu=config_menu)

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
        custom = self._custom_browser()
        if custom is not None:
            choices.insert(0, custom.name)
        self.portal_browser_box.configure(values=choices)
        self.portal_browser_var.set(self._saved_browser_name())
        self.run_status_var.set(f"Found {len(self.portal_browsers)} supported browser(s)")

    def _saved_browser_name(self) -> str:
        run = self.config.run_config
        saved = run.last_portal_browser_path.casefold()
        for name, browser in self.portal_browsers.items():
            if str(browser.executable).casefold() == saved:
                return name
        custom = self._custom_browser()
        if custom is not None:
            return custom.name
        return next(iter(self.portal_browsers), "")

    def _custom_browser(self) -> PortalBrowser | None:
        run = self.config.run_config
        executable = Path(run.custom_portal_browser_path)
        if not executable.is_file():
            return None
        try:
            engine = BrowserEngine(run.custom_portal_browser_engine)
        except ValueError:
            engine = self._infer_browser_engine(executable)
        return PortalBrowser(f"Custom browser ({executable.name})", executable, engine)

    @staticmethod
    def _infer_browser_engine(executable: Path) -> BrowserEngine:
        if any(part in executable.name.casefold() for part in ("firefox", "zen", "floorp", "waterfox")):
            return BrowserEngine.FIREFOX
        return BrowserEngine.CHROMIUM

    def _browser_selected(self, _event: object | None = None) -> None:
        if self.portal_browser_var.get() == self.CUSTOM_BROWSER_OPTION:
            selected = filedialog.askopenfilename(
                title="Choose portal browser",
                filetypes=[("Browser executable", "*.exe"), ("All files", "*.*")],
                parent=self.root,
            )
            if not selected:
                self.portal_browser_var.set(self._saved_browser_name())
                return
            run = self.config.run_config
            run.custom_portal_browser_path = selected
            run.custom_portal_browser_engine = self._infer_browser_engine(Path(selected)).value
            custom = self._custom_browser()
            if custom is not None:
                values = list(self.portal_browser_box.cget("values"))
                if custom.name not in values:
                    self.portal_browser_box.configure(values=[custom.name, *values])
                self.portal_browser_var.set(custom.name)
        self.save_global_settings()

    def selected_portal_browser(self) -> PortalBrowser | None:
        browser = self.portal_browsers.get(self.portal_browser_var.get())
        if browser is not None:
            return browser
        custom = self._custom_browser()
        if custom is not None and custom.name == self.portal_browser_var.get():
            return custom
        return None

    def selected_captcha_copy_mode(self) -> CaptchaCopyMode:
        try:
            return CaptchaCopyMode(self.captcha_copy_mode_var.get())
        except ValueError:
            return CaptchaCopyMode.DIRECT

    def _browse_csv(self) -> None:
        if any(tab.is_active for tab in self.tabs.values()):
            return
        selected = filedialog.askopenfilename(
            title="Choose shared CSV batch",
            filetypes=[("CSV files", "*.csv")],
            parent=self.root,
        )
        if selected:
            self.csv_var.set(selected)
            self.load_batch_preview()
            self.save_global_settings()

    def _browse_download(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose stamp download folder", parent=self.root, mustexist=True
        )
        if selected:
            self.download_var.set(selected)
            self.save_global_settings()

    def load_batch_preview(self, *, quiet: bool = False) -> None:
        text = self.csv_var.get().strip()
        if not text or not Path(text).is_file():
            self.csv_valid = False
            self.render_batch_rows([])
            return
        try:
            store = CsvBatchStore(Path(text))
            store.load()
            rows = store.summaries()
            issues: list[str] = []
            for index, row in enumerate(store.rows):
                errors = store.validate_row(row)
                if errors:
                    rows[index]["error"] = "; ".join(errors)
                    issues.append(f"Row {index + 1}: {'; '.join(errors)}")
            self.csv_valid = not issues
            self.render_batch_rows(rows)
            if issues and not quiet:
                messagebox.showwarning("CSV needs correction", "\n".join(issues[:8]), parent=self.root)
        except Exception as error:
            self.csv_valid = False
            self.render_batch_rows([])
            if not quiet:
                messagebox.showerror("CSV error", str(error), parent=self.root)

    def global_validation_error(self) -> str:
        path = Path(self.csv_var.get().strip())
        if not path.is_file():
            return "Choose an existing CSV batch file."
        if not self.csv_valid:
            self.load_batch_preview(quiet=True)
            if not self.csv_valid:
                return "The selected CSV contains invalid rows."
        if not self.article_var.get().strip():
            return "Choose or type an Article."
        if self.selected_portal_browser() is None:
            return "Choose an installed portal browser."
        return ""

    def render_batch_rows(self, rows: list[dict[str, str]]) -> None:
        if not hasattr(self, "batch_tree"):
            return
        assignments: dict[int, list[str]] = {}
        for assigned_row, quantity, label in self.worker_assignments.values():
            assignments.setdefault(assigned_row, []).append(f"{label} - Q{quantity}")
        self.batch_tree.delete(*self.batch_tree.get_children())
        for row in rows:
            row_number = int(row.get("row_number", "0") or 0)
            active = ", ".join(sorted(assignments.get(row_number, [])))
            tag = "active" if active else "completed" if row.get("status") == "completed" else ""
            self.batch_tree.insert(
                "",
                "end",
                tags=(tag,) if tag else (),
                values=(
                    row.get("row_number", ""),
                    row.get("first_party_name", ""),
                    row.get("second_party_name", ""),
                    row.get("amount", ""),
                    row.get("status", ""),
                    f"{row.get('completed', '0')} / {row.get('processed', '0')} / {row.get('quantity', '1')}",
                    active,
                    row.get("error", ""),
                ),
            )

    def _append_qr(self, event: UiEvent) -> None:
        try:
            item = QrItem(
                qr_id=str(event.data["qr_id"]),
                file_path=Path(str(event.data["file_path"])),
                created_at=float(event.data["created_at"]),
                expires_at=float(event.data["expires_at"]),
                run_id=str(event.run_id or event.data.get("run_id", "")),
                dock_id=str(event.data.get("dock_id", "")),
                worker_label=str(event.data.get("worker_label", "")),
                row=int(event.data.get("row", 0)),
                quantity=int(event.data.get("quantity", 0)),
            )
        except (KeyError, TypeError, ValueError) as error:
            self.append_session_log(f"Invalid QR event ignored: {error}")
            return
        was_empty = not self.qr_items
        self.qr_items.append(item)
        if was_empty:
            self.qr_index = 0
        self._render_qr()

    def _remove_qr_for_worker(self, dock_id: str) -> None:
        for index in range(len(self.qr_items) - 1, -1, -1):
            if self.qr_items[index].dock_id == dock_id:
                self._remove_qr_at(index)
                return

    def _remove_qr_at(self, index: int) -> None:
        if not 0 <= index < len(self.qr_items):
            return
        item = self.qr_items.pop(index)
        remove_qr_file(item.file_path)
        if not self.qr_items:
            self.qr_index = -1
        elif index < self.qr_index:
            self.qr_index -= 1
        elif index == self.qr_index:
            self.qr_index = min(index, len(self.qr_items) - 1)
        self._render_qr()

    def _move_qr(self, offset: int) -> None:
        if not self.qr_items:
            return
        self.qr_index = min(len(self.qr_items) - 1, max(0, self.qr_index + offset))
        self._render_qr()

    def _mark_current_qr_paid(self) -> None:
        if not 0 <= self.qr_index < len(self.qr_items):
            return
        self.qr_items[self.qr_index].paid = True
        self._render_qr()

    def _qr_accent_color(self, item: QrItem) -> str:
        base_id = self._dock_base_id(item.dock_id or item.run_id)
        return self._tab_accent_color(int(base_id)) if base_id.isdigit() else "#2563eb"

    def _qr_display_label(self, item: QrItem) -> str:
        base_id = self._dock_base_id(item.dock_id or item.run_id)
        id_label = f"ID {base_id}" if base_id else "Payment QR"
        worker_label = item.worker_label.strip()
        return f"{id_label} | {worker_label}" if worker_label and worker_label != id_label else id_label

    def _tick_qr_carousel(self) -> None:
        now = time.time()
        for index in range(len(self.qr_items) - 1, -1, -1):
            if self.qr_items[index].expires_at <= now:
                self._remove_qr_at(index)
        self._render_qr()
        if not self._closing:
            self.root.after(1000, self._tick_qr_carousel)

    def _render_qr(self) -> None:
        if not hasattr(self, "qr_image_label"):
            return
        if not self.qr_items or not 0 <= self.qr_index < len(self.qr_items):
            self.qr_photo = None
            self.qr_image_label.configure(image="")
            self.qr_border_frame.configure(background="#d1d5db")
            self.qr_title_label.configure(foreground="#374151")
            self.qr_title_var.set("Waiting for payment QR codes")
            self.qr_detail_var.set("New QR codes will appear here without focusing the browser.")
            self.qr_position_var.set("0 / 0")
            self.qr_previous_button.configure(state="disabled")
            self.qr_next_button.configure(state="disabled")
            self.qr_paid_button.configure(text="Mark paid", state="disabled")
            return
        item = self.qr_items[self.qr_index]
        accent_color = self._qr_accent_color(item)
        self.qr_border_frame.configure(background=accent_color)
        self.qr_title_label.configure(foreground=accent_color)
        try:
            with Image.open(item.file_path) as source:
                image = source.convert("RGB")
                image.thumbnail((340, 340), Image.Resampling.NEAREST)
                self.qr_photo = ImageTk.PhotoImage(image)
            self.qr_image_label.configure(image=self.qr_photo)
        except Exception as error:
            self.append_session_log(f"Could not display QR {item.qr_id}: {error}")
            self.qr_photo = None
            self.qr_image_label.configure(image="")
        self.qr_title_var.set(self._qr_display_label(item))
        remaining = max(0, int(item.expires_at - time.time()))
        expiry = f"{remaining // 60:02d}:{remaining % 60:02d}"
        paid_status = " | Marked paid" if item.paid else ""
        self.qr_detail_var.set(
            f"CSV row {item.row} | Quantity {item.quantity} | Expires in {expiry}{paid_status}"
        )
        self.qr_position_var.set(f"{self.qr_index + 1} / {len(self.qr_items)}")
        self.qr_previous_button.configure(state="normal" if self.qr_index > 0 else "disabled")
        self.qr_next_button.configure(
            state="normal" if self.qr_index < len(self.qr_items) - 1 else "disabled"
        )
        self.qr_paid_button.configure(
            text="Paid" if item.paid else "Mark paid",
            state="disabled" if item.paid else "normal",
        )

    def _add_tab_widget(self, tab_config: TabConfig) -> AutomationTab:
        tab = AutomationTab(self, self.hidden_id_host, tab_config)
        self.tabs[tab.tab_id] = tab
        initial_state = "Idle" if tab.is_enabled else "Disabled"
        self.tab_states[tab.tab_id] = initial_state
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

    @staticmethod
    def _dock_base_id(dock_id: str) -> str:
        text = str(dock_id)
        if "::" in text:
            return text.split("::", 1)[0]
        if "." in text:
            base, _, worker = text.rpartition(".")
            if base and worker.isdigit():
                return base
        return text

    def _dock_ids_for_tab(self, tab: AutomationTab) -> list[str]:
        try:
            count = min(20, max(1, int(getattr(tab.config, "browser_count", 1))))
        except (TypeError, ValueError):
            count = 1
        if count <= 1:
            return [tab.run_id]
        return [f"{tab.run_id}.{worker}" for worker in range(1, count + 1)]

    @staticmethod
    def _dock_title_for_tab(tab: AutomationTab, dock_id: str) -> str:
        try:
            count = min(20, max(1, int(getattr(tab.config, "browser_count", 1))))
        except (TypeError, ValueError):
            count = 1
        if count <= 1:
            return tab.display_name
        text = str(dock_id)
        worker = ""
        if "." in text and "::" not in text:
            _, _, worker = text.rpartition(".")
        if worker.isdigit():
            return f"{tab.display_name} | B{tab.run_id}.{worker}"
        return f"{tab.display_name} | B{tab.run_id}.1"

    def _dock_tab(self, run_id: str) -> AutomationTab | None:
        base = self._dock_base_id(run_id)
        return self.tabs.get(int(base)) if base.isdigit() else None

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
        if tab is None:
            return
        self._record_ui_action(f"id_{run_id}_dock_stop_clicked")
        if tab.browser_recovery_pending:
            tab.dismiss_browser_recovery()
            return
        # Dock Stop targets one browser only; other browsers of the ID continue.
        # Tab-level Stop (run_tab) still stops the whole ID group.
        self.controller.stop(run_id)

    def _dock_error_decision(self, run_id: str, action: str) -> None:
        tab = self._dock_tab(run_id)
        if tab is None:
            return
        self._record_ui_action(f"id_{run_id}_dock_error_{action}_clicked")
        # Decide only for this browser's pending prompt; others keep working.
        self.controller.decide_error(run_id, action)

    def _dock_browser_recovery(self, run_id: str, action: str) -> None:
        tab = self._dock_tab(run_id)
        if tab is not None:
            self._record_ui_action(f"id_{run_id}_dock_browser_{action}_clicked")
            tab.recover_browser(action)

    def _dock_focus_browser(self, run_id: str) -> None:
        self._record_ui_action(f"id_{run_id}_dock_focus_browser_clicked")
        handle = self.controller.get_portal_window_handle(run_id)
        if handle is None:
            base = self._dock_base_id(run_id)
            self.run_status_var.set(f"No browser window found for {run_id} (ID {base})")
            return

        if os.name != "nt":
            return
        from automation.browser import _restore_and_activate_window

        if not _restore_and_activate_window(handle):
            self.run_status_var.set(f"Could not focus the browser window {run_id}")

    def _show_status_dock(self) -> None:
        self._record_ui_action("show_status_dock_clicked")
        dock = self._ensure_status_dock()
        for tab in self.tabs.values():
            if tab.is_active or tab.browser_recovery_pending:
                for dock_id in self._dock_ids_for_tab(tab):
                    if not dock.has_run(dock_id):
                        dock.begin_run(
                            dock_id,
                            self._dock_title_for_tab(tab, dock_id),
                            self._tab_accent_color(tab.tab_id),
                            tab.run_status_var.get(),
                        )
            if tab.browser_recovery_pending:
                for dock_id in self._dock_ids_for_tab(tab):
                    if dock.has_run(dock_id):
                        dock.show_browser_recovery(
                            dock_id,
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

    def _resolve_dock_id(self, tab: AutomationTab, event: UiEvent | None = None) -> str:
        if event is not None:
            candidate = str(event.data.get("dock_id", "") or "").strip()
            if candidate:
                return candidate
        return self._dock_ids_for_tab(tab)[0]

    def show_browser_recovery(
        self, tab: AutomationTab, message: str, *, ready: bool, dock_id: str | None = None
    ) -> None:
        dock = self._ensure_status_dock()
        target = dock_id or self._resolve_dock_id(tab)
        if not dock.has_run(target):
            dock.begin_run(
                target,
                self._dock_title_for_tab(tab, target),
                self._tab_accent_color(tab.tab_id),
                message,
            )
        dock.show_browser_recovery(target, message, ready=ready)

    def show_tab_error_in_dock(self, tab: AutomationTab, event: UiEvent) -> bool:
        dock = self._ensure_status_dock()
        dock_id = self._resolve_dock_id(tab, event)
        if not dock.has_run(dock_id):
            dock.begin_run(
                dock_id,
                self._dock_title_for_tab(tab, dock_id),
                self._tab_accent_color(tab.tab_id),
                event.message,
            )
        dock.show_error(
            dock_id,
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
        self,
        tab: AutomationTab,
        row: int | None,
        quantity: int | None,
        dock_id: str | None = None,
    ) -> None:
        dock = self.automation_status_window
        if dock is not None and dock.exists:
            target = dock_id or self._resolve_dock_id(tab)
            if not dock.has_run(target):
                dock.begin_run(
                    target,
                    self._dock_title_for_tab(tab, target),
                    self._tab_accent_color(tab.tab_id),
                    tab.run_status_var.get(),
                )
            dock.set_progress(target, row, quantity)

    def update_status_dock_payment(self, tab: AutomationTab, state: str, dock_id: str | None = None) -> None:
        dock = self.automation_status_window
        if dock is None or not dock.exists:
            return
        target = dock_id or self._resolve_dock_id(tab)
        if state in {"slot_granted", "pay_now_ready", "qr_ready", "foreground_verified"}:
            dock.set_payment_active(target, True)
        elif state in {"slot_released", "download_ready"}:
            dock.set_payment_active(target, False)

    def _add_tab(self) -> None:
        self._record_ui_action("add_id_clicked")
        tab_config = self.config.create_tab()
        tab = self._add_tab_widget(tab_config)
        self._show_id_settings(tab.tab_id, new_id=True)

    def _selected_tab(self) -> AutomationTab | None:
        return self.tabs.get(self.selected_id) if self.selected_id is not None else None

    def _show_selected_id_settings(self) -> None:
        tab_id = self.selected_id if self.selected_id in self.tabs else min(self.tabs, default=None)
        if tab_id is not None:
            self._show_id_settings(tab_id)

    def refresh_id_strip(self) -> None:
        if not hasattr(self, "id_strip"):
            return
        for widget in self.id_strip.winfo_children():
            widget.destroy()
        for column in range(self.id_strip.grid_size()[0]):
            self.id_strip.columnconfigure(column, weight=0, uniform="")
        self.id_buttons.clear()
        tabs = sorted(self.tabs.values(), key=lambda item: item.tab_id)
        for column, tab in enumerate(tabs):
            state = self.tab_states.get(tab.tab_id, "Idle" if tab.is_enabled else "Disabled")
            detail = getattr(self, "tab_details", {}).get(tab.tab_id, "")
            text = self._id_button_text(tab, state, detail)
            enabled = tab.is_enabled
            color = self._tab_accent_color(tab.tab_id) if enabled else "#9ca3af"
            button = tk.Button(
                self.id_strip,
                text=text,
                command=partial(self._show_id_settings, tab.tab_id),
                background=color,
                activebackground=color,
                foreground="white",
                activeforeground="white",
                relief="flat",
                borderwidth=0,
                font=("Segoe UI", 9, "bold"),
                justify="center",
                width=1,
                height=5,
                wraplength=140,
                padx=5,
                pady=4,
                cursor="hand2",
            )
            self.id_strip.columnconfigure(column, weight=1, uniform="id-buttons")
            button.grid(row=0, column=column, sticky="ew", padx=(0, 4))
            self.id_buttons[tab.tab_id] = button
        add_column = len(tabs)
        self.id_strip.columnconfigure(add_column, weight=1, uniform="id-buttons")
        tk.Button(
            self.id_strip,
            text="+ Add ID",
            command=self._add_tab,
            background="#374151",
            activebackground="#1f2937",
            foreground="white",
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 9, "bold"),
            width=1,
            height=5,
            padx=5,
            pady=4,
            cursor="hand2",
        ).grid(row=0, column=add_column, sticky="ew")
        self._refresh_start_menu()
        if hasattr(self, "id_settings_editors"):
            self._refresh_id_settings_lock_states()

    def _refresh_start_menu(self) -> None:
        if not hasattr(self, "start_menu"):
            return
        self.start_menu.delete(0, "end")
        candidates = [
            tab
            for tab in sorted(self.tabs.values(), key=lambda item: item.tab_id)
            if tab.is_enabled and not tab.is_active and not tab.portal_session_open
        ]
        if not candidates:
            self.start_menu.add_command(label="No enabled idle IDs", state="disabled")
            self.start_menu_button.configure(state="disabled")
            return
        self.start_menu_button.configure(state="normal")
        for tab in candidates:
            self.start_menu.add_command(label=self._id_citizen_label(tab), command=partial(tab.start))

    @staticmethod
    def _id_citizen_label(tab: AutomationTab) -> str:
        citizen_id = tab.citizen_user_var.get().strip() or "No Citizen ID"
        return f"{tab.tab_id}. {citizen_id}"

    @staticmethod
    def _status_symbol(state: str) -> str:
        if state in {"Starting", "Running", "Payment"}:
            return "●"
        if state == "Paused":
            return "◐"
        if state == "Complete":
            return "✓"
        if state in {"Error", "Needs setup", "Browser closed"}:
            return "!"
        return "○"

    @staticmethod
    def _compact_status_detail(detail: str, limit: int = 48) -> str:
        compact = " ".join(detail.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 1].rstrip()}…"

    def _id_button_text(self, tab: AutomationTab, state: str, detail: str = "") -> str:
        status = f"{self._status_symbol(state)} {state}"
        compact_detail = self._compact_status_detail(detail)
        if compact_detail:
            status = f"{status}\n{compact_detail}"
        return f"{self._id_citizen_label(tab)}\n{status}"

    def _show_id_settings(self, tab_id: int, *, new_id: bool = False) -> None:
        tab = self.tabs.get(tab_id)
        if tab is None:
            return
        self.selected_id = tab_id
        if new_id:
            self.provisional_id_tabs.add(tab_id)
        dialog = self.id_settings_dialog
        if dialog is None or not dialog.winfo_exists():
            self._create_id_settings_dialog()
            dialog = self.id_settings_dialog
        elif tab_id not in self.id_settings_pages:
            self._add_id_settings_page(tab)
        if dialog is None:
            return
        if tab_id in self.id_settings_pages:
            self._select_id_settings_page(tab_id)
        self._size_id_settings_dialog()
        dialog.deiconify()
        dialog.lift()
        dialog.focus_force()

    def _create_id_settings_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("ID settings")
        dialog.geometry("900x520")
        dialog.minsize(680, 440)
        dialog.transient(self.root)
        container = ttk.Frame(dialog, padding=12)
        container.pack(fill="both", expand=True)
        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)
        notebook.bind("<<NotebookTabChanged>>", self._id_settings_tab_changed)
        footer = ttk.Frame(container)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="Close", command=self._close_id_settings_dialog).pack(side="right")
        self.id_settings_dialog = dialog
        self.id_settings_notebook = notebook
        self.id_settings_pages.clear()
        self.id_settings_editors.clear()
        for tab in sorted(self.tabs.values(), key=lambda item: item.tab_id):
            self._add_id_settings_page(tab)
        if self.selected_id in self.id_settings_pages:
            self._select_id_settings_page(self.selected_id)
        self._size_id_settings_dialog()
        self.id_settings_root_resize_binding = self.root.bind(
            "<Configure>", self._main_window_resized_for_id_settings, add="+"
        )
        dialog.protocol("WM_DELETE_WINDOW", self._close_id_settings_dialog)

    def _main_window_resized_for_id_settings(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is self.root:
            self._size_id_settings_dialog()

    def _size_id_settings_dialog(self) -> None:
        dialog = self.id_settings_dialog
        if dialog is None or not dialog.winfo_exists():
            return
        width = max(680, self.root.winfo_width())
        height = max(440, dialog.winfo_height())
        x = max(0, min(self.root.winfo_rootx(), self.root.winfo_screenwidth() - width))
        y = max(0, self.root.winfo_rooty())
        dialog.minsize(width, 440)
        dialog.geometry(f"{width}x{height}+{x}+{y}")

    def _add_id_settings_page(self, tab: AutomationTab) -> None:
        notebook = self.id_settings_notebook
        dialog = self.id_settings_dialog
        if notebook is None or dialog is None or tab.tab_id in self.id_settings_pages:
            return
        page = ttk.Frame(notebook, padding=14)
        page.columnconfigure(1, weight=1)
        credentials = tab.entered_credentials()
        editor: dict[str, Any] = {
            "citizen_user": tk.StringVar(value=credentials.citizen_username),
            "citizen_password": tk.StringVar(value=credentials.citizen_password),
            "egras_user": tk.StringVar(value=credentials.egras_username),
            "egras_password": tk.StringVar(value=credentials.egras_password),
            "sms_user": tk.StringVar(value=tab.config.sms_user_id),
            "browser_count": tk.IntVar(value=tab.config.browser_count),
            "enabled": tk.BooleanVar(value=tab.config.enabled),
            "status": tk.StringVar(value=tab.credentials_status_var.get()),
            "controls": [],
        }
        entries = (
            ("Citizen username", editor["citizen_user"], ""),
            ("Citizen password", editor["citizen_password"], ""),
            ("eGRAS username", editor["egras_user"], ""),
            ("eGRAS password", editor["egras_password"], ""),
            ("Citizen SMS ID", editor["sms_user"], ""),
        )
        for row, (text, variable, show) in enumerate(entries):
            ttk.Label(page, text=text).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
            entry = ttk.Entry(page, textvariable=variable, show=show, width=52)
            entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            editor["controls"].append(entry)
        ttk.Label(page, text="Browsers").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=4)
        browser_spin = ttk.Spinbox(
            page, from_=1, to=20, textvariable=editor["browser_count"], width=6
        )
        browser_spin.grid(row=5, column=1, sticky="w", pady=4)
        editor["controls"].append(browser_spin)
        enabled_check = ttk.Checkbutton(
            page, text="Enabled for Start All", variable=editor["enabled"]
        )
        enabled_check.grid(row=6, column=0, columnspan=3, sticky="w", pady=(7, 4))
        editor["controls"].append(enabled_check)
        ttk.Label(page, text="Persistent profile").grid(
            row=7, column=0, sticky="w", padx=(0, 10), pady=4
        )
        ttk.Entry(page, textvariable=tab.profile_var, state="readonly", width=52).grid(
            row=7, column=1, columnspan=2, sticky="ew", pady=4
        )
        ttk.Label(page, textvariable=editor["status"], foreground="#6b7280").grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(7, 0)
        )
        buttons = ttk.Frame(page)
        buttons.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        save_button = ttk.Button(
            buttons, text="Save", command=partial(self._save_id_settings_page, tab.tab_id)
        )
        save_button.pack(side="left")
        clear_button = ttk.Button(
            buttons,
            text="Clear saved",
            command=partial(self._clear_id_settings_credentials, tab.tab_id),
        )
        clear_button.pack(side="left", padx=(6, 0))
        profile_button = ttk.Button(
            buttons,
            text="Delete profile",
            command=partial(self._delete_id_settings_profile, tab.tab_id),
        )
        profile_button.pack(side="left", padx=(6, 0))
        remove_button: ttk.Button | None = None
        if tab.tab_id != 1:
            remove_button = ttk.Button(
                buttons,
                text="Remove ID",
                command=partial(self._remove_id_from_settings, tab.tab_id),
            )
            remove_button.pack(side="left", padx=(6, 0))
        editor.update(
            {
                "save_button": save_button,
                "clear_button": clear_button,
                "profile_button": profile_button,
                "remove_button": remove_button,
            }
        )
        self.id_settings_pages[tab.tab_id] = page
        self.id_settings_editors[tab.tab_id] = editor
        accent = self._tab_accent_image(tab.tab_id, enabled=tab.is_enabled)
        notebook.add(
            page,
            text=self._id_settings_tab_label(tab),
            image=accent,
            compound="left",
        )
        self._refresh_id_settings_lock_states()

    def _id_settings_tab_label(self, tab: AutomationTab) -> str:
        suffix = " *" if tab.tab_id in self.provisional_id_tabs else ""
        return f"{self._id_citizen_label(tab)}{suffix}"

    def _id_settings_tab_changed(self, _event: object = None) -> None:
        notebook = self.id_settings_notebook
        if notebook is None:
            return
        selected = notebook.select()  # type: ignore[no-untyped-call]
        for tab_id, page in self.id_settings_pages.items():
            if str(page) == str(selected):
                self.selected_id = tab_id
                return

    def _select_id_settings_page(self, tab_id: int) -> None:
        page = self.id_settings_pages.get(tab_id)
        notebook = self.id_settings_notebook
        if page is None or notebook is None:
            return
        self.selected_id = tab_id
        notebook.select(page)  # type: ignore[no-untyped-call]

    def _save_id_settings_page(self, tab_id: int) -> None:
        tab = self.tabs.get(tab_id)
        editor = self.id_settings_editors.get(tab_id)
        dialog = self.id_settings_dialog or self.root
        if tab is None or editor is None:
            return
        if tab.is_active or tab.portal_session_open:
            messagebox.showwarning(
                "ID settings", "Stop this ID before changing its settings.", parent=dialog
            )
            return
        try:
            count = int(editor["browser_count"].get())
        except (tk.TclError, ValueError):
            count = 0
        if not 1 <= count <= 20:
            messagebox.showwarning("ID settings", "Browsers must be from 1 to 20.", parent=dialog)
            return
        tab.citizen_user_var.set(editor["citizen_user"].get().strip())
        tab.citizen_password_var.set(editor["citizen_password"].get())
        tab.egras_user_var.set(editor["egras_user"].get().strip())
        tab.egras_password_var.set(editor["egras_password"].get())
        tab.sms_user_id_var.set(editor["sms_user"].get().strip())
        tab.browser_count_var.set(count)
        tab.config.enabled = editor["enabled"].get()
        try:
            error = tab.save_credentials()
        except (OSError, RuntimeError, ValueError) as save_error:
            messagebox.showerror("Save credentials", str(save_error), parent=dialog)
            return
        if error:
            messagebox.showwarning("Incomplete credentials", error, parent=dialog)
            return
        tab.save_id_settings()
        self.provisional_id_tabs.discard(tab_id)
        editor["status"].set(tab.credentials_status_var.get() or "Saved")
        self._refresh_id_settings_tab_caption(tab_id)
        tab.set_state("Idle" if tab.config.enabled else "Disabled")
        self.refresh_id_strip()

    def _refresh_id_settings_tab_caption(self, tab_id: int) -> None:
        tab = self.tabs.get(tab_id)
        page = self.id_settings_pages.get(tab_id)
        notebook = self.id_settings_notebook
        if tab is None or page is None or notebook is None:
            return
        notebook.tab(  # type: ignore[no-untyped-call]
            page,
            text=self._id_settings_tab_label(tab),
            image=self._tab_accent_image(tab.tab_id, enabled=tab.is_enabled),
        )

    def _clear_id_settings_credentials(self, tab_id: int) -> None:
        tab = self.tabs.get(tab_id)
        editor = self.id_settings_editors.get(tab_id)
        dialog = self.id_settings_dialog or self.root
        if tab is None or editor is None:
            return
        if not messagebox.askyesno(
            "Clear credentials", f"Clear saved credentials for {tab.display_name}?", parent=dialog
        ):
            return
        try:
            tab.clear_credentials()
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("Clear credentials", str(error), parent=dialog)
            return
        for name in ("citizen_user", "citizen_password", "egras_user", "egras_password"):
            editor[name].set("")
        editor["status"].set("")
        self._refresh_id_settings_tab_caption(tab_id)
        self._refresh_start_menu()

    def _delete_id_settings_profile(self, tab_id: int) -> None:
        tab = self.tabs.get(tab_id)
        dialog = self.id_settings_dialog or self.root
        if tab is None:
            return
        try:
            message = tab.delete_portal_profile()
        except OSError as error:
            messagebox.showerror("Delete profile", str(error), parent=dialog)
            return
        messagebox.showinfo("Delete profile", message, parent=dialog)

    def _remove_id_from_settings(self, tab_id: int) -> None:
        tab = self.tabs.get(tab_id)
        if tab is not None:
            self._remove_tab(tab, confirm=tab_id not in self.provisional_id_tabs)

    def _remove_id_settings_page(self, tab_id: int) -> None:
        page = self.id_settings_pages.pop(tab_id, None)
        self.id_settings_editors.pop(tab_id, None)
        self.provisional_id_tabs.discard(tab_id)
        notebook = self.id_settings_notebook
        if page is not None and notebook is not None and notebook.winfo_exists():
            notebook.forget(page)
            page.destroy()

    def _refresh_id_settings_lock_states(self) -> None:
        for tab_id, editor in self.id_settings_editors.items():
            tab = self.tabs.get(tab_id)
            if tab is None:
                continue
            active = tab.is_active or tab.portal_session_open
            state = "disabled" if active else "normal"
            for control in editor["controls"]:
                control.configure(state=state)
            for name in ("save_button", "clear_button", "profile_button", "remove_button"):
                button = editor.get(name)
                if button is not None:
                    button.configure(state=state)
            editor["status"].set(
                "Settings are locked while this ID is active."
                if active
                else tab.credentials_status_var.get()
            )

    def _rebuild_id_settings_dialog(self, selected_tab_id: int | None = None) -> None:
        dialog = self.id_settings_dialog
        if dialog is None or not dialog.winfo_exists():
            return
        notebook = self.id_settings_notebook
        if notebook is None:
            return
        for page in list(self.id_settings_pages.values()):
            notebook.forget(page)
            page.destroy()
        self.id_settings_pages.clear()
        self.id_settings_editors.clear()
        for tab in sorted(self.tabs.values(), key=lambda item: item.tab_id):
            self._add_id_settings_page(tab)
        target = selected_tab_id if selected_tab_id in self.id_settings_pages else self.selected_id
        if target in self.id_settings_pages:
            self._select_id_settings_page(target)

    def _close_id_settings_dialog(self) -> None:
        dialog = self.id_settings_dialog
        binding = self.id_settings_root_resize_binding
        if binding is not None:
            self.root.unbind("<Configure>", binding)
        self.id_settings_root_resize_binding = None
        self.id_settings_dialog = None
        self.id_settings_notebook = None
        self.id_settings_pages.clear()
        self.id_settings_editors.clear()
        provisional = sorted(self.provisional_id_tabs)
        self.provisional_id_tabs.clear()
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
        for tab_id in provisional:
            tab = self.tabs.get(tab_id)
            if tab is not None:
                self._remove_tab(tab, confirm=False)

    def _open_download_undownloaded_certificates_dialog(self, initial_tab: int = 0) -> None:
        self._record_ui_action("download_undownloaded_certificates_clicked")
        DownloadUndownloadedCertificatesDialog(self, initial_tab=initial_tab)

    def _export_payment_transactions(self) -> None:
        self._open_download_undownloaded_certificates_dialog(initial_tab=0)

    def _compare_transactions_with_stamps(self) -> None:
        self._open_download_undownloaded_certificates_dialog(initial_tab=1)

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
        self._remove_tab(tab, confirm=True)

    def _remove_tab(self, tab: AutomationTab, *, confirm: bool) -> bool:
        dialog = getattr(self, "id_settings_dialog", None)
        parent = dialog if dialog is not None and dialog.winfo_exists() else self.root
        if tab.tab_id == 1:
            if confirm:
                messagebox.showinfo(
                    "Remove ID", "ID 1 is the default ID and cannot be removed.", parent=parent
                )
            return False
        if tab.is_active or tab.portal_session_open:
            messagebox.showwarning(
                "ID is active",
                "Stop this ID and close its portal browser before removing it.",
                parent=parent,
            )
            return False
        if confirm and not messagebox.askyesno(
            "Remove ID",
            f"Remove {tab.display_name}? Its persistent profile will remain on disk.",
            parent=parent,
        ):
            return False
        was_selected = self.selected_id == tab.tab_id
        self.tabs.pop(tab.tab_id, None)
        self.tab_states.pop(tab.tab_id, None)
        if hasattr(self, "tab_details"):
            self.tab_details.pop(tab.tab_id, None)
        if hasattr(self, "id_settings_pages"):
            self._remove_id_settings_page(tab.tab_id)
        if self.automation_status_window is not None:
            for dock_id in self._dock_ids_for_tab(tab):
                self.automation_status_window.remove_run(dock_id)
        self.tab_accent_images.pop((tab.tab_id, True), None)
        self.tab_accent_images.pop((tab.tab_id, False), None)
        self.config.tabs[:] = [item for item in self.config.tabs if item.tab_id != tab.tab_id]
        tab.destroy()
        if was_selected:
            self.selected_id = min(self.tabs, default=None)
            if self.selected_id is not None and self.selected_id in self.id_settings_pages:
                self._select_id_settings_page(self.selected_id)
        self.save_config()
        self._update_summary()
        self.refresh_id_strip()
        return True

    def _start_all(self) -> None:
        self._record_ui_action("start_all_clicked")
        if hasattr(self, "config"):
            self.save_global_settings()
            self.load_batch_preview(quiet=True)
            error = self.global_validation_error()
            if error:
                messagebox.showwarning("Run configuration", error, parent=self.root)
                self.run_status_var.set("Run configuration needs attention")
                return
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
        self.config_store.save(self.config)

    def active_tab_for_csv(self, path: Path, excluding: int | None = None) -> AutomationTab | None:
        # All IDs intentionally share the global CSV.
        return None

    def update_tab_state(
        self,
        tab: AutomationTab,
        state: str,
        detail: str = "",
        dock_id: str | None = None,
    ) -> None:
        if tab.tab_id not in self.tabs:
            return
        self.tab_states[tab.tab_id] = state
        if not hasattr(self, "tab_details"):
            self.tab_details = {}
        self.tab_details[tab.tab_id] = detail.strip()
        dock_ids = self._dock_ids_for_tab(tab)
        dock = self.automation_status_window
        if state == "Starting" and tab.is_enabled:
            dock = self._ensure_status_dock()
            for target in dock_ids:
                dock.begin_run(
                    target,
                    self._dock_title_for_tab(tab, target),
                    self._tab_accent_color(tab.tab_id),
                    detail or "Preparing this automation session...",
                )
        elif dock is not None and dock.exists:
            if state in {"Stopped", "Disabled"} and not tab.browser_recovery_pending:
                for target in dock_ids:
                    if dock.has_run(target):
                        dock.remove_run(target)
            else:
                targets = (
                    [dock_id]
                    if dock_id and dock.has_run(dock_id)
                    else [target for target in dock_ids if dock.has_run(target)]
                )
                if not targets and dock_id:
                    dock.begin_run(
                        dock_id,
                        self._dock_title_for_tab(tab, dock_id),
                        self._tab_accent_color(tab.tab_id),
                        detail or "Preparing this automation session...",
                    )
                    targets = [dock_id]
                for target in targets:
                    dock.set_status(target, state, detail)
        if dock is not None and dock.exists:
            targets = (
                [dock_id]
                if dock_id and dock.has_run(dock_id)
                else [target for target in dock_ids if dock.has_run(target)]
            )
            for target in targets:
                dock.set_controls(
                    target,
                    running=tab.running,
                    starting=tab.starting,
                    paused=tab.paused,
                    auto_waiting=tab.auto_waiting,
                    portal_open=tab.portal_session_open,
                )
        self._update_summary()
        self.refresh_id_strip()
        self.update_configuration_lock()

    def _tab_changed(self, _event: object = None) -> None:
        self._update_summary()

    def update_configuration_lock(self) -> None:
        if not hasattr(self, "global_config_widgets"):
            return
        active = any(tab.is_active or tab.portal_session_open for tab in self.tabs.values())
        for widget in self.global_config_widgets:
            if isinstance(widget, ttk.Combobox):
                normal_state = "normal" if widget is self.article_box else "readonly"
                widget.configure(state="disabled" if active else normal_state)
            else:
                widget.configure(state="disabled" if active else "normal")

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
        base_id = self._dock_base_id(run_id)
        tab = self.tabs.get(int(base_id)) if base_id.isdigit() else None
        if tab is not None:
            dock_id = str(event.data.get("dock_id", "") or "").strip() or None
            tab.handle_event(event)
            if event.kind == "qr_available":
                self._append_qr(event)
            if event.kind in {"worker_stopped", "worker_finished"}:
                if dock_id:
                    getattr(self, "worker_assignments", {}).pop(dock_id, None)
                dock = self.automation_status_window
                if dock is not None and dock.exists and dock_id and dock.has_run(dock_id):
                    dock.remove_run(dock_id)
                self._update_summary()
                return
            if event.kind == "batch_update":
                row = event.data.get("current_row")
                unit = event.data.get("current_unit")
                if dock_id and isinstance(row, int) and isinstance(unit, int):
                    label = str(event.data.get("worker_label", "") or tab.display_name)
                    self.worker_assignments[dock_id] = (row, unit, label)
                self.update_status_dock_progress(
                    tab,
                    row,
                    unit,
                    dock_id,
                )
            elif event.kind == "payment_state":
                state = str(event.data.get("state", ""))
                self.update_status_dock_payment(tab, state, dock_id)
                if state == "download_ready":
                    if dock_id:
                        self._remove_qr_for_worker(dock_id)
                    self.global_success_count += 1
                    self.global_success_var.set(f"Session downloads: {self.global_success_count}")
                    dock = self.automation_status_window
                    if dock is not None and dock.exists:
                        target = dock_id or self._resolve_dock_id(tab)
                        if dock.has_run(target):
                            dock.increment_download_count(target)
            elif event.kind in {
                "run_completed",
                "run_stopped",
                "browser_closed",
                "portal_closed",
                "fatal_error",
            }:
                self.update_status_dock_payment(tab, "slot_released", dock_id)
                prefix = f"{tab.run_id}."
                for worker in list(self.worker_assignments):
                    if worker == tab.run_id or worker.startswith(prefix):
                        self.worker_assignments.pop(worker, None)
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
        self.downloads_menu.entryconfigure(
            self.managed_firefox_menu_index, label=label, state=state
        )

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

    def _export_all_configs(self) -> None:
        self._record_ui_action("export_all_configs_clicked")
        tabs_data = [tab.export_data() for tab in sorted(self.tabs.values(), key=lambda t: t.tab_id)]
        payload = create_export_package(self.config, tabs_data, include_global_settings=True)
        selected = filedialog.asksaveasfilename(
            title="Export All IDs & Settings",
            defaultextension=".estampcfg",
            initialfile=f"estamp_all_ids_export_{datetime.now():%Y%m%d_%H%M%S}.estampcfg",
            filetypes=CONFIG_PACKAGE_FILE_TYPES,
            parent=self.root,
        )
        if not selected:
            return
        try:
            exported_path = export_package_to_file(selected, payload)
            self.append_session_log(f"Exported {len(tabs_data)} ID(s) to: {exported_path.name}")
            messagebox.showinfo(
                "Export configuration",
                f"Successfully exported {len(tabs_data)} ID(s) and settings to:\n{exported_path}",
                parent=self.root,
            )
        except Exception as error:
            messagebox.showerror("Export configuration", str(error), parent=self.root)

    def _export_selected_config(self) -> None:
        tab = self._selected_tab()
        if tab is None:
            messagebox.showinfo("Export configuration", "No ID tab is currently selected.", parent=self.root)
            return
        self._export_specific_tab(tab)

    def _export_specific_tab(self, tab: AutomationTab) -> None:
        self._record_ui_action(f"id_{tab.run_id}_export_config_clicked")
        tab_data = tab.export_data()
        payload = create_export_package(self.config, [tab_data], include_global_settings=False)
        selected = filedialog.asksaveasfilename(
            title=f"Export {tab.display_name} Configuration",
            defaultextension=".estampcfg",
            initialfile=f"estamp_id_{tab.tab_id}_export_{datetime.now():%Y%m%d_%H%M%S}.estampcfg",
            filetypes=CONFIG_PACKAGE_FILE_TYPES,
            parent=self.root,
        )
        if not selected:
            return
        try:
            exported_path = export_package_to_file(selected, payload)
            self.append_session_log(f"Exported {tab.display_name} to: {exported_path.name}")
            messagebox.showinfo(
                "Export configuration",
                f"Successfully exported {tab.display_name} configuration to:\n{exported_path}",
                parent=self.root,
            )
        except Exception as error:
            messagebox.showerror("Export configuration", str(error), parent=self.root)

    def _import_configs(self) -> None:
        self._record_ui_action("import_configs_clicked")
        active = [tab.display_name for tab in self.tabs.values() if tab.is_active or tab.portal_session_open]
        if active:
            messagebox.showwarning(
                "Import configuration",
                f"The following ID(s) are currently active:\n{', '.join(active)}\n\n"
                "Stop all active IDs and close their portal browsers before importing.",
                parent=self.root,
            )
            return

        selected = filedialog.askopenfilename(
            title="Import IDs & Settings",
            filetypes=CONFIG_PACKAGE_FILE_TYPES,
            parent=self.root,
        )
        if not selected:
            return

        try:
            package = import_package_from_file(selected)
        except Exception as error:
            messagebox.showerror(
                "Import configuration",
                f"Could not read configuration package:\n{error}",
                parent=self.root,
            )
            return

        raw_tabs = package.get("tabs", [])
        num_tabs = len(raw_tabs)
        if num_tabs == 0:
            messagebox.showwarning(
                "Import configuration",
                "The selected file contains no ID configurations.",
                parent=self.root,
            )
            return

        selected_tab = self._selected_tab()
        mode, target_tab_id = self._show_import_options_dialog(
            package, selected_path=Path(selected), current_selected_tab=selected_tab
        )
        if not mode:
            return

        try:
            updated_config, imported_ids = apply_imported_package(
                package,
                self.config,
                self.credential_store,
                mode=mode,
                target_tab_id=target_tab_id,
            )
        except Exception as error:
            messagebox.showerror(
                "Import configuration",
                f"Failed to apply configuration:\n{error}",
                parent=self.root,
            )
            return

        if mode == "replace":
            self._reload_all_tabs_after_replace()
        elif mode == "merge":
            for tab_id in imported_ids:
                tab_cfg = self.config.get_tab(tab_id)
                self._add_tab_widget(tab_cfg)
            if imported_ids:
                first_new = self.tabs.get(imported_ids[0])
                if first_new is not None:
                    self.selected_id = first_new.tab_id
        elif mode == "single_tab" and target_tab_id is not None:
            target_tab = self.tabs.get(target_tab_id)
            if target_tab is not None:
                target_tab.refresh_from_config()

        self.save_config()
        self._update_summary()
        self.append_session_log(
            f"Imported {len(imported_ids)} ID(s) ({mode} mode) from {Path(selected).name}."
        )
        self.refresh_id_strip()
        self._rebuild_id_settings_dialog(self.selected_id)
        messagebox.showinfo(
            "Import configuration",
            f"Successfully imported {len(imported_ids)} ID(s) into the application.",
            parent=self.root,
        )

    def _reload_all_tabs_after_replace(self) -> None:
        for tab in list(self.tabs.values()):
            if self.automation_status_window is not None:
                for dock_id in self._dock_ids_for_tab(tab):
                    self.automation_status_window.remove_run(dock_id)
            tab.destroy()
        self.tabs.clear()
        self.tab_states.clear()
        self.tab_details.clear()
        self.tab_accent_images.clear()
        self.chrome_var.set(self.config.chrome_executable)
        self.profile_var.set(self.config.chrome_profile_path)
        run = self.config.run_config
        self.csv_var.set(run.last_csv_path)
        self.download_var.set(run.last_download_path)
        self.article_var.set(run.last_article)
        self.payment_trigger_url_var.set(run.payment_trigger_url)
        self.payment_trigger_method_var.set(run.payment_trigger_method)
        self.mode_var.set(run.last_mode)
        self.ocr_engine_var.set(run.ocr_engine)
        self.ocr_enabled_var.set(run.ocr_enabled)
        self.captcha_copy_mode_var.set(run.captcha_copy_mode)
        self.save_captcha_images_var.set(run.save_captcha_images)
        self.fresh_browser_var.set(run.fresh_browser_per_unit)
        self.retry_egras_otp_once_var.set(run.retry_egras_otp_once)
        self.sms_server_url_var.set(run.sms_server_url)
        self.portal_browser_var.set(self._saved_browser_name())
        self.gemini_ready = bool(self.config.gemini_verified)
        for tab_config in sorted(self.config.tabs, key=lambda t: t.tab_id):
            self._add_tab_widget(tab_config)
        if self.tabs:
            first_tab = next(iter(self.tabs.values()))
            self.selected_id = first_tab.tab_id
        self.refresh_id_strip()
        self._rebuild_id_settings_dialog(self.selected_id)
        self.load_batch_preview(quiet=True)

    def _show_import_options_dialog(
        self,
        package: dict[str, Any],
        selected_path: Path,
        current_selected_tab: AutomationTab | None,
    ) -> tuple[str | None, int | None]:
        dialog = tk.Toplevel(self.root)
        dialog.title("Import Options")
        dialog.geometry("540x340")
        dialog.transient(self.root)
        dialog.grab_set()

        raw_tabs = package.get("tabs", [])
        num_tabs = len(raw_tabs)
        exported_at = package.get("exported_at", "Unknown date")
        if "T" in exported_at:
            exported_at = exported_at.replace("T", " ").split(".")[0]

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=f"File: {selected_path.name}", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, text=f"Contains: {num_tabs} ID(s) | Exported: {exported_at}").pack(
            anchor="w", pady=(2, 12)
        )

        ttk.Label(frame, text="Select import action:").pack(anchor="w", pady=(0, 6))

        default_mode = "replace" if num_tabs > 1 else ("single_tab" if current_selected_tab else "replace")
        mode_var = tk.StringVar(value=default_mode)

        ttk.Radiobutton(
            frame,
            text=f"Replace all current IDs ({len(self.tabs)} present) with imported IDs ({num_tabs})",
            variable=mode_var,
            value="replace",
        ).pack(anchor="w", pady=3)

        ttk.Radiobutton(
            frame,
            text=f"Add / Merge as new ID(s) (Keep existing {len(self.tabs)} IDs and append {num_tabs})",
            variable=mode_var,
            value="merge",
        ).pack(anchor="w", pady=3)

        if num_tabs == 1 and current_selected_tab is not None:
            ttk.Radiobutton(
                frame,
                text=f"Update currently selected {current_selected_tab.display_name} only",
                variable=mode_var,
                value="single_tab",
            ).pack(anchor="w", pady=3)

        result_mode: list[str | None] = [None]
        result_tab_id: list[int | None] = [None]

        def on_confirm() -> None:
            result_mode[0] = mode_var.get()
            if result_mode[0] == "single_tab" and current_selected_tab:
                result_tab_id[0] = current_selected_tab.tab_id
            dialog.destroy()

        def on_cancel() -> None:
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e", pady=(18, 0), side="bottom", fill="x")
        ttk.Button(buttons, text="Cancel", command=on_cancel).pack(side="right", padx=(7, 0))
        ttk.Button(buttons, text="Import", command=on_confirm).pack(side="right")

        self.root.wait_window(dialog)
        return result_mode[0], result_tab_id[0]

    def _on_close(self) -> None:
        active = [tab for tab in self.tabs.values() if tab.is_active or tab.portal_session_open]
        if active and not messagebox.askyesno(
            "Exit application",
            "Automation is active. Stop all IDs and close their portal browsers?",
            parent=self.root,
            default=messagebox.NO,
        ):
            return
        self._close_id_settings_dialog()
        self.controller.record_activity("application_closed", "Desktop application closed.")
        self._closing = True
        if self.automation_status_window is not None:
            self.automation_status_window.close()
        for item in self.qr_items:
            remove_qr_file(item.file_path)
        self.qr_items.clear()
        self.controller.shutdown()
        self.root.destroy()
