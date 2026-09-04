from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from core.config import DEFAULT_SMS_SERVER_URL, TabConfig
from core.form_options import ARTICLE_OPTIONS
from core.models import (
    BrowserEngine,
    CaptchaCopyMode,
    Credentials,
    OcrEngine,
    PortalBrowser,
    RunMode,
    RunOptions,
    Stage,
    UiEvent,
    available_ocr_engines,
)
from services.config_package import TabExportData
from services.credential_store import credential_store_label
from services.csv_store import CsvBatchStore
from services.windows_notifications import show_windows_notification

if TYPE_CHECKING:
    from ui.main_window import MainWindow


CUSTOM_BROWSER_OPTION = "Choose custom browser..."


class AutomationTab:
    """Configuration, progress, and controls for one independent ID."""

    def __init__(self, owner: MainWindow, notebook: ttk.Notebook, config: TabConfig) -> None:
        self.owner = owner
        self.notebook = notebook
        self.config = config
        self.run_id = str(config.tab_id)
        self.frame = ttk.Frame(notebook, padding=10)
        self.running = False
        self.starting = False
        self.paused = False
        self.auto_waiting = False
        self.portal_session_open = False
        self.browser_recovery_pending = False
        self.browser_recovery_ready = False
        self.current_row_number: int | None = None
        self.current_unit_number: int | None = None
        self.csv_valid = False
        self.error_window: tk.Toplevel | None = None
        self.credentials_panel: ttk.LabelFrame | None = None

        try:
            saved_credentials = owner.credential_store.load(config.tab_id)
        except (OSError, RuntimeError, ValueError):
            saved_credentials = None

        self.citizen_user_var = tk.StringVar(
            value=saved_credentials.citizen_username if saved_credentials else ""
        )
        self.citizen_password_var = tk.StringVar(
            value=saved_credentials.citizen_password if saved_credentials else ""
        )
        self.egras_user_var = tk.StringVar(
            value=saved_credentials.egras_username if saved_credentials else ""
        )
        self.egras_password_var = tk.StringVar(
            value=saved_credentials.egras_password if saved_credentials else ""
        )
        self.credentials_saved = saved_credentials is not None
        self.credentials_status_var = tk.StringVar(
            value=f"saved in {credential_store_label()}" if self.credentials_saved else ""
        )
        self.sms_user_id_var = tk.StringVar(value=config.sms_user_id)
        self.sms_server_url_var = tk.StringVar(value=config.sms_server_url or DEFAULT_SMS_SERVER_URL)
        self.csv_var = tk.StringVar(value=config.last_csv_path)
        self.download_var = tk.StringVar(value=config.last_download_path)
        self.article_var = tk.StringVar(value=config.last_article)
        self.payment_trigger_url_var = tk.StringVar(value=config.payment_trigger_url)
        method = config.payment_trigger_method.strip().upper()
        self.payment_trigger_method_var = tk.StringVar(value=method if method in {"GET", "POST"} else "GET")
        self.mode_var = tk.StringVar(
            value=(
                config.last_mode
                if config.last_mode in {mode.value for mode in RunMode}
                else RunMode.ASSISTED
            )
        )
        engines = available_ocr_engines()
        engine_values = {engine.value for engine in engines}
        selected_engine = config.ocr_engine if config.ocr_engine in engine_values else OcrEngine.PADDLEOCR
        self.ocr_engine_var = tk.StringVar(value=selected_engine)
        self.ocr_enabled_var = tk.BooleanVar(value=getattr(config, "ocr_enabled", True))
        copy_modes = {mode.value for mode in CaptchaCopyMode}
        copy_mode = (
            config.captcha_copy_mode
            if config.captcha_copy_mode in copy_modes
            else CaptchaCopyMode.DIRECT
        )
        self.captcha_copy_mode_var = tk.StringVar(value=copy_mode)
        self.save_captcha_images_var = tk.BooleanVar(value=config.save_captcha_images)
        self.fresh_browser_var = tk.BooleanVar(value=config.fresh_browser_per_unit)
        self.retry_egras_otp_once_var = tk.BooleanVar(value=config.retry_egras_otp_once)
        self.portal_browser_var = tk.StringVar(value=self._saved_browser_name())
        self.run_status_var = tk.StringVar(value="Idle" if config.enabled else "Disabled")
        self.profile_var = tk.StringVar(value=config.portal_profile_path)

        self._build()
        if config.last_csv_path and Path(config.last_csv_path).is_file():
            self._load_preview(Path(config.last_csv_path), quiet=True)
        self._set_buttons()

    @property
    def tab_id(self) -> int:
        return self.config.tab_id

    @property
    def display_name(self) -> str:
        return self.config.display_name

    @property
    def is_active(self) -> bool:
        return self.running or self.starting

    @property
    def is_enabled(self) -> bool:
        return self.config.enabled

    def _build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(3, weight=1)

        status = ttk.Frame(self.frame)
        status.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(status, text=self.display_name, style="Heading.TLabel").pack(side="left")
        ttk.Label(status, textvariable=self.run_status_var, style="Status.TLabel").pack(
            side="left", padx=(12, 0)
        )
        ttk.Label(status, text="Persistent profile").pack(side="left", padx=(24, 4))
        ttk.Entry(status, textvariable=self.profile_var, state="readonly", width=48).pack(
            side="left", fill="x", expand=True
        )
        self.delete_profile_button = ttk.Button(
            status,
            text="Delete profile",
            command=self._delete_portal_profile,
        )
        self.delete_profile_button.pack(side="left", padx=(8, 0))

        settings = ttk.Frame(self.frame)
        settings.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        settings.columnconfigure(0, weight=1)
        settings.columnconfigure(1, weight=1)
        self._build_credentials(settings)
        self._build_batch(settings)

        controls = ttk.Frame(self.frame)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.start_button = ttk.Button(controls, text="Start", command=self.start)
        self.pause_button = ttk.Button(controls, text="Pause", command=self.pause)
        self.resume_button = ttk.Button(controls, text="Resume", command=self.resume)
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop)
        self.toggle_enabled_button = ttk.Button(controls, command=self.toggle_enabled)
        self.start_button.pack(side="left")
        self.pause_button.pack(side="left", padx=(8, 0))
        self.resume_button.pack(side="left", padx=(8, 0))
        self.stop_button.pack(side="left", padx=(8, 0))
        self.toggle_enabled_button.pack(side="left", padx=(16, 0))

        table = ttk.LabelFrame(self.frame, text="Batch progress", padding=6)
        table.grid(row=3, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        columns = ("row", "first", "second", "district", "amount", "status", "completed", "error")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=9)
        headings = {
            "row": "CSV row",
            "first": "First party",
            "second": "Second party",
            "district": "District",
            "amount": "Amount",
            "status": "Status",
            "completed": "Success / Processed / Total",
            "error": "Last error",
        }
        widths = {"row": 62, "first": 150, "second": 150, "district": 105, "amount": 80,
                  "status": 92, "completed": 145, "error": 230}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], stretch=column in {"first", "second", "error"})
        self.tree.tag_configure("current", background="#bfdbfe")
        self.tree.tag_configure("completed", background="#e5e7eb", foreground="#555555")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

    def _build_credentials(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Credentials, SMS and OCR", padding=9)
        self.credentials_panel = panel
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        panel.columnconfigure(1, weight=1)
        entries = (
            ("Citizen username", self.citizen_user_var, False),
            ("Citizen password", self.citizen_password_var, False),
            ("eGRAS username", self.egras_user_var, False),
            ("eGRAS password", self.egras_password_var, False),
        )
        for row, (label, variable, secret) in enumerate(entries):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            ttk.Entry(panel, textvariable=variable, show="*" if secret else "").grid(
                row=row, column=1, columnspan=2, sticky="ew", pady=2
            )
        buttons = ttk.Frame(panel)
        buttons.grid(row=4, column=0, columnspan=3, sticky="w", pady=(7, 0))
        ttk.Button(buttons, text="Save credentials", command=self._save_credentials).pack(side="left")
        ttk.Button(buttons, text="Clear saved", command=self._clear_credentials).pack(
            side="left", padx=(6, 0)
        )
        if self.tab_id != 1:
            ttk.Button(
                buttons,
                text="Copy all from ID 1",
                command=self._copy_default_tab,
            ).pack(
                side="left", padx=(6, 0)
            )
        ttk.Button(
            buttons,
            text="Export ID...",
            command=self._export_this_tab,
        ).pack(
            side="left", padx=(6, 0)
        )
        ttk.Separator(panel).grid(
            row=5, column=0, columnspan=3, sticky="ew", pady=(8, 3)
        )

        ttk.Label(panel, text="Citizen SMS ID").grid(
            row=6, column=0, sticky="w", padx=(0, 8), pady=2
        )
        ttk.Entry(panel, textvariable=self.sms_user_id_var).grid(
            row=6, column=1, columnspan=2, sticky="ew", pady=2
        )
        ttk.Label(panel, text="SMS server").grid(
            row=7, column=0, sticky="w", padx=(0, 8), pady=2
        )
        ttk.Entry(panel, textvariable=self.sms_server_url_var).grid(
            row=7, column=1, columnspan=2, sticky="ew", pady=2
        )

        ocr = ttk.Frame(panel)
        ocr.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Checkbutton(ocr, text="Use OCR", variable=self.ocr_enabled_var).pack(side="left")
        self.ocr_box = ttk.Combobox(
            ocr,
            textvariable=self.ocr_engine_var,
            values=[engine.value for engine in available_ocr_engines()],
            state="readonly",
            width=11,
        )
        self.ocr_box.pack(side="left", padx=(3, 8))
        ttk.Label(ocr, text="CAPTCHA copy").pack(side="left")
        ttk.Combobox(
            ocr,
            textvariable=self.captcha_copy_mode_var,
            values=[mode.value for mode in CaptchaCopyMode],
            state="readonly",
            width=12,
        ).pack(side="left", padx=(3, 8))
        self.credentials_status_var.trace_add("write", self._update_credentials_title)
        self._update_credentials_title()

    def _build_batch(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Run configuration", padding=9)
        panel.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text="Portal browser").grid(row=0, column=0, sticky="w")
        self.portal_browser_box = ttk.Combobox(
            panel,
            textvariable=self.portal_browser_var,
            values=[*self.owner.portal_browsers, CUSTOM_BROWSER_OPTION],
            state="readonly",
        )
        self.portal_browser_box.grid(row=0, column=1, columnspan=2, sticky="ew")
        self.portal_browser_box.bind("<<ComboboxSelected>>", self._browser_selected)
        ttk.Label(panel, text="Article").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Combobox(panel, textvariable=self.article_var, values=ARTICLE_OPTIONS).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=(5, 0)
        )
        ttk.Label(panel, text="Filled CSV").grid(row=2, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(panel, textvariable=self.csv_var).grid(row=2, column=1, sticky="ew", pady=(5, 0))
        ttk.Button(panel, text="Select...", command=self._browse_csv).grid(
            row=2, column=2, padx=(6, 0), pady=(5, 0)
        )
        ttk.Label(panel, text="Download folder").grid(row=3, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(panel, textvariable=self.download_var).grid(row=3, column=1, sticky="ew", pady=(5, 0))
        ttk.Button(panel, text="Browse...", command=self._browse_download).grid(
            row=3, column=2, padx=(6, 0), pady=(5, 0)
        )
        ttk.Label(panel, text="Payment trigger").grid(row=4, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(panel, textvariable=self.payment_trigger_url_var).grid(
            row=4, column=1, sticky="ew", pady=(5, 0)
        )
        ttk.Combobox(
            panel,
            textvariable=self.payment_trigger_method_var,
            values=("GET", "POST"),
            state="readonly",
            width=7,
        ).grid(row=4, column=2, padx=(6, 0), pady=(5, 0))

        options = ttk.Frame(panel)
        options.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(7, 0))
        ttk.Label(options, text="Mode").pack(side="left", padx=(0, 3))
        ttk.Combobox(
            options,
            textvariable=self.mode_var,
            values=[mode.value for mode in RunMode],
            state="readonly",
            width=12,
        ).pack(side="left")
        ttk.Checkbutton(options, text="New browser per unit", variable=self.fresh_browser_var).pack(
            side="left", padx=(8, 0)
        )
        advanced = ttk.Frame(panel)
        advanced.grid(row=6, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Checkbutton(
            advanced, text="Save CAPTCHAs", variable=self.save_captcha_images_var
        ).pack(side="left")
        ttk.Checkbutton(
            advanced,
            text="Retry eGRAS OTP once",
            variable=self.retry_egras_otp_once_var,
        ).pack(side="left", padx=(8, 0))

        for variable in (
            self.article_var,
            self.download_var,
            self.sms_user_id_var,
            self.sms_server_url_var,
            self.payment_trigger_url_var,
            self.payment_trigger_method_var,
            self.mode_var,
            self.ocr_enabled_var,
            self.ocr_engine_var,
            self.captcha_copy_mode_var,
            self.fresh_browser_var,
            self.save_captcha_images_var,
            self.retry_egras_otp_once_var,
        ):
            variable.trace_add("write", self._settings_changed)

    def _update_credentials_title(self, *_args: object) -> None:
        if self.credentials_panel is None:
            return
        status = self.credentials_status_var.get().strip()
        title = "Credentials, SMS and OCR"
        if status:
            title = f"{title} ({status})"
        self.credentials_panel.configure(text=title)

    def _saved_browser_name(self) -> str:
        saved = self.config.last_portal_browser_path.casefold()
        for name, browser in self.owner.portal_browsers.items():
            if str(browser.executable).casefold() == saved:
                return name
        if self.config.custom_portal_browser_path and Path(self.config.custom_portal_browser_path).is_file():
            return self._custom_browser().name
        return next(iter(self.owner.portal_browsers), "")

    def _browser_selected(self, _event: object | None = None) -> None:
        if self.portal_browser_var.get() == CUSTOM_BROWSER_OPTION:
            selected = filedialog.askopenfilename(
                title=f"Choose portal browser for {self.display_name}",
                filetypes=[("Browser executable", "*.exe"), ("All files", "*.*")],
                parent=self.owner.root,
            )
            if not selected:
                self.portal_browser_var.set(self._saved_browser_name())
                return
            self.config.custom_portal_browser_path = selected
            self.config.custom_portal_browser_engine = self._infer_engine(Path(selected)).value
            browser = self._custom_browser()
            self.portal_browser_var.set(browser.name)
            values = list(self.portal_browser_box.cget("values"))
            if browser.name not in values:
                self.portal_browser_box.configure(values=[browser.name, *values])
        self._save_settings()

    def _custom_browser(self) -> PortalBrowser:
        executable = Path(self.config.custom_portal_browser_path)
        try:
            engine = BrowserEngine(self.config.custom_portal_browser_engine)
        except ValueError:
            engine = self._infer_engine(executable)
        return PortalBrowser(f"Custom browser ({executable.name})", executable, engine)

    @staticmethod
    def _infer_engine(executable: Path) -> BrowserEngine:
        if any(part in executable.name.casefold() for part in ("firefox", "zen", "floorp", "waterfox")):
            return BrowserEngine.FIREFOX
        return BrowserEngine.CHROMIUM

    def _selected_browser(self) -> PortalBrowser | None:
        browser = self.owner.portal_browsers.get(self.portal_browser_var.get())
        if browser is not None:
            return browser
        custom = self._custom_browser()
        if custom.executable.is_file() and custom.name == self.portal_browser_var.get():
            return custom
        return None

    def _settings_changed(self, *_args: object) -> None:
        self.owner.root.after_idle(self._save_settings)

    def _save_settings(self) -> None:
        self.config.last_download_path = self.download_var.get().strip()
        self.config.last_mode = self.mode_var.get()
        self.config.sms_user_id = self.sms_user_id_var.get().strip()
        self.config.sms_server_url = self.sms_server_url_var.get().strip() or DEFAULT_SMS_SERVER_URL
        self.config.payment_trigger_url = self.payment_trigger_url_var.get().strip()
        self.config.payment_trigger_method = self.payment_trigger_method_var.get().strip().upper()
        self.config.captcha_copy_mode = self.captcha_copy_mode_var.get()
        self.config.ocr_engine = self.ocr_engine_var.get()
        if hasattr(self.config, "ocr_enabled"):
            self.config.ocr_enabled = self.ocr_enabled_var.get()
        self.config.last_article = self.article_var.get().strip()
        self.config.last_csv_path = self.csv_var.get().strip()
        self.config.save_captcha_images = self.save_captcha_images_var.get()
        self.config.fresh_browser_per_unit = self.fresh_browser_var.get()
        self.config.retry_egras_otp_once = self.retry_egras_otp_once_var.get()
        browser = self._selected_browser()
        if browser is not None:
            self.config.last_portal_browser_path = str(browser.executable)
        self.owner.save_config()

    def entered_credentials(self) -> Credentials:
        return Credentials(
            self.citizen_user_var.get().strip(),
            self.citizen_password_var.get(),
            self.egras_user_var.get().strip(),
            self.egras_password_var.get(),
        )

    def _save_credentials(self) -> None:
        credentials = self.entered_credentials()
        incomplete = [
            label
            for label, username, password in (
                ("Citizen", credentials.citizen_username, credentials.citizen_password),
                ("eGRAS", credentials.egras_username, credentials.egras_password),
            )
            if bool(username) != bool(password)
        ]
        if incomplete:
            messagebox.showwarning(
                "Incomplete credentials",
                f"Enter both fields for: {', '.join(incomplete)}.",
                parent=self.owner.root,
            )
            return
        try:
            self.owner.credential_store.save(credentials, self.tab_id)
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("Save credentials", str(error), parent=self.owner.root)
            return
        self.credentials_saved = True
        self.credentials_status_var.set(f"saved in {credential_store_label()}")

    def _clear_credentials(self) -> None:
        if not messagebox.askyesno(
            "Clear credentials", f"Clear saved credentials for {self.display_name}?", parent=self.owner.root
        ):
            return
        try:
            self.owner.credential_store.clear(self.tab_id)
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("Clear credentials", str(error), parent=self.owner.root)
            return
        for variable in (
            self.citizen_user_var,
            self.citizen_password_var,
            self.egras_user_var,
            self.egras_password_var,
        ):
            variable.set("")
        self.credentials_saved = False
        self.credentials_status_var.set("")

    def _copy_default_tab(self) -> None:
        if self.is_active:
            messagebox.showwarning(
                "Copy ID 1 settings",
                "Stop this ID before replacing its settings.",
                parent=self.owner.root,
            )
            return
        source = self.owner.tabs.get(1)
        if source is None:
            return
        source._save_settings()
        self.config.copy_run_settings_from(source.config)

        credentials = source.entered_credentials()
        self.citizen_user_var.set(credentials.citizen_username)
        self.citizen_password_var.set(credentials.citizen_password)
        self.egras_user_var.set(credentials.egras_username)
        self.egras_password_var.set(credentials.egras_password)
        self.sms_user_id_var.set(self.config.sms_user_id)
        self.sms_server_url_var.set(self.config.sms_server_url or DEFAULT_SMS_SERVER_URL)
        self.csv_var.set(self.config.last_csv_path)
        self.download_var.set(self.config.last_download_path)
        self.article_var.set(self.config.last_article)
        self.payment_trigger_url_var.set(self.config.payment_trigger_url)
        self.payment_trigger_method_var.set(self.config.payment_trigger_method)
        self.mode_var.set(self.config.last_mode)
        self.ocr_enabled_var.set(self.config.ocr_enabled)
        self.ocr_engine_var.set(self.config.ocr_engine)
        self.captcha_copy_mode_var.set(self.config.captcha_copy_mode)
        self.save_captcha_images_var.set(self.config.save_captcha_images)
        self.fresh_browser_var.set(self.config.fresh_browser_per_unit)
        self.retry_egras_otp_once_var.set(self.config.retry_egras_otp_once)

        browser_name = self._saved_browser_name()
        if self.config.custom_portal_browser_path:
            custom_name = self._custom_browser().name
            values = list(self.portal_browser_box.cget("values"))
            if custom_name not in values:
                self.portal_browser_box.configure(values=[custom_name, *values])
        self.portal_browser_var.set(browser_name)

        try:
            if source.credentials_saved:
                self.owner.credential_store.save(credentials, self.tab_id)
                self.credentials_saved = True
                self.credentials_status_var.set("saved, copied from ID 1")
            else:
                self.owner.credential_store.clear(self.tab_id)
                self.credentials_saved = False
                self.credentials_status_var.set("not saved, copied from ID 1")
        except (OSError, RuntimeError, ValueError) as error:
            self.credentials_saved = False
            self.credentials_status_var.set("credentials could not be saved")
            messagebox.showerror("Copy ID 1 credentials", str(error), parent=self.owner.root)

        self._save_settings()
        csv_path = Path(self.csv_var.get().strip())
        if csv_path.is_file():
            self._load_preview(csv_path, quiet=True)
        else:
            self.csv_valid = False
            self._render_rows([])
        self.set_state("Idle", "Copied all settings from ID 1")
        self._set_buttons()

    def _browse_csv(self) -> None:
        if self.is_active:
            return
        selected = filedialog.askopenfilename(
            title=f"Choose CSV for {self.display_name}",
            filetypes=[("CSV files", "*.csv")],
            parent=self.owner.root,
        )
        if selected:
            self.csv_var.set(selected)
            self._load_preview(Path(selected))
            self._save_settings()
            self._set_buttons()

    def _browse_download(self) -> None:
        selected = filedialog.askdirectory(
            title=f"Choose download folder for {self.display_name}",
            parent=self.owner.root,
            mustexist=True,
        )
        if selected:
            self.download_var.set(selected)
            self._save_settings()

    def _load_preview(self, path: Path, *, quiet: bool = False) -> None:
        try:
            store = CsvBatchStore(path)
            store.load()
            rows = store.summaries()
            issues: list[str] = []
            for index, row in enumerate(store.rows):
                errors = store.validate_row(row)
                if errors:
                    rows[index]["error"] = "; ".join(errors)
                    issues.append(f"Row {index + 1}: {'; '.join(errors)}")
            self.csv_valid = not issues
            self._render_rows(rows)
            if issues and not quiet:
                messagebox.showwarning("CSV needs correction", "\n".join(issues[:8]), parent=self.owner.root)
        except Exception as error:
            self.csv_valid = False
            self._render_rows([])
            if not quiet:
                messagebox.showerror("CSV error", str(error), parent=self.owner.root)

    def validation_error(self) -> str:
        csv_path = Path(self.csv_var.get().strip())
        if not csv_path.is_file():
            return "Choose an existing CSV batch file."
        if not self.csv_valid:
            self._load_preview(csv_path, quiet=True)
            if not self.csv_valid:
                return "The selected CSV contains invalid rows."
        if not self.article_var.get().strip():
            return "Choose or type an Article."
        if self._selected_browser() is None:
            return "Choose an installed portal browser."
        duplicate = self.owner.active_tab_for_csv(csv_path, excluding=self.tab_id)
        if duplicate is not None:
            return f"The same CSV is already active in {duplicate.display_name}."
        return ""

    def start(self, *, show_errors: bool = True) -> bool:
        if not self.is_enabled:
            self.set_state("Disabled", "Skipped by user")
            return False
        if self.is_active or self.portal_session_open:
            return False
        if self.browser_recovery_pending and not self.browser_recovery_ready:
            return False
        error = self.validation_error()
        if error:
            self.set_state("Needs setup", error)
            if show_errors:
                messagebox.showwarning(f"{self.display_name} needs setup", error, parent=self.owner.root)
            return False
        browser = self._selected_browser()
        if browser is None:
            return False
        self._save_settings()
        profile_slug = re.sub(r"[^a-z0-9]+", "-", browser.name.casefold()).strip("-") or "browser"
        profile_path = Path(self.config.portal_profile_path) / browser.engine.value / profile_slug
        download_text = self.download_var.get().strip()
        credentials = self.entered_credentials()
        citizen_id_label = credentials.citizen_username or "not set"
        options = RunOptions(
            csv_path=Path(self.csv_var.get().strip()),
            download_root=Path(download_text) if download_text else None,
            article=self.article_var.get().strip(),
            portal_browser=browser,
            mode=RunMode(self.mode_var.get()),
            credentials=credentials,
            ocr_enabled=self.ocr_enabled_var.get(),
            ocr_engine=OcrEngine(self.ocr_engine_var.get()),
            sms_user_id=self.sms_user_id_var.get().strip(),
            sms_server_url=self.sms_server_url_var.get().strip() or DEFAULT_SMS_SERVER_URL,
            payment_trigger_url=self.payment_trigger_url_var.get().strip(),
            payment_trigger_method=self.payment_trigger_method_var.get().strip().upper(),
            captcha_copy_mode=CaptchaCopyMode(self.captcha_copy_mode_var.get()),
            save_captcha_images=self.save_captcha_images_var.get(),
            fresh_browser_per_unit=self.fresh_browser_var.get(),
            retry_egras_otp_once=self.retry_egras_otp_once_var.get(),
            run_id=self.run_id,
            portal_profile_path=profile_path,
            portal_window_accent=self.owner.tab_accent_color(self.tab_id),
            portal_window_label=f"{self.display_name} | {citizen_id_label}",
        )
        try:
            self.owner.controller.start(self.run_id, options)
        except (RuntimeError, ValueError) as start_error:
            self.set_state("Error", str(start_error))
            if show_errors:
                messagebox.showerror("Start automation", str(start_error), parent=self.owner.root)
            return False
        self.browser_recovery_pending = False
        self.browser_recovery_ready = False
        self.starting = True
        self.set_state("Starting", f"Opening {browser.name}")
        self._set_buttons()
        return True

    def pause(self) -> None:
        self.owner.controller.pause(self.run_id)

    def resume(self) -> None:
        self.owner.controller.resume(self.run_id)

    def stop(self) -> None:
        self.owner.controller.stop(self.run_id)

    def toggle_enabled(self) -> None:
        if self.is_active or self.portal_session_open:
            messagebox.showwarning(
                "Change ID state",
                "Stop this ID before disabling it.",
                parent=self.owner.root,
            )
            return
        self.config.enabled = not self.config.enabled
        self.owner.save_config()
        if self.is_enabled:
            self.set_state("Idle", "Included in Start All")
        else:
            self.set_state("Disabled", "Skipped by Start All")
        self._set_buttons()

    def handle_event(self, event: UiEvent) -> None:
        if event.message:
            self.owner.append_session_log(f"{self.display_name}: {event.message}")
        kind = event.kind
        if kind == "run_started":
            self.browser_recovery_pending = False
            self.browser_recovery_ready = False
            self.starting = False
            self.running = True
            self.portal_session_open = True
            self.paused = False
            self.set_state("Running", event.message)
        elif kind == "stage":
            stage = event.message.replace("_", " ").title()
            self.set_state("Running", stage)
        elif kind == "status":
            self.set_state("Running", event.message)
        elif kind in {"manual_checkpoint", "paused"}:
            self.paused = True
            self.auto_waiting = bool(event.data.get("auto_continue"))
            self.set_state("Paused", event.message)
        elif kind == "resumed":
            self.paused = False
            self.auto_waiting = False
            self.set_state("Running", event.message)
        elif kind == "payment_queue":
            position = event.data.get("position", "?")
            self.set_state(f"Queued #{position}", event.message)
        elif kind == "payment_state":
            state = event.data.get("state")
            if state in {
                "slot_granted",
                "pay_now_ready",
                "qr_ready",
                "foreground_verified",
            }:
                self.set_state("Payment", event.message)
            elif state == "slot_released" and self.running:
                self.set_state("Running", event.message)
        elif kind == "batch_update":
            current_row = event.data.get("current_row")
            current_unit = event.data.get("current_unit")
            if isinstance(current_row, int):
                self.current_row_number = current_row
            if isinstance(current_unit, int):
                self.current_unit_number = current_unit
            self._render_rows(event.data.get("rows", []), event.data.get("current_row"))
        elif kind == "error_prompt":
            self.paused = True
            self.auto_waiting = True
            if not self.owner.show_tab_error_in_dock(self, event):
                self._show_error(event)
            self.set_state("Error", event.message)
        elif kind == "notification":
            show_windows_notification(event.data.get("title", self.display_name), event.message)
        elif kind == "browser_closed":
            offer_recovery = self.owner.should_offer_browser_recovery(self)
            self.starting = False
            self.running = False
            self.paused = False
            self.auto_waiting = False
            self.portal_session_open = False
            self.browser_recovery_pending = offer_recovery
            self.browser_recovery_ready = False
            if offer_recovery:
                message = "This browser was closed. Finishing cleanup before it can restart..."
                self.set_state("Browser closed", message)
                self.owner.show_browser_recovery(self, message, ready=False)
            else:
                self.set_state("Stopped", event.message)
        elif kind == "session_finished":
            if self.browser_recovery_pending:
                self.browser_recovery_ready = True
                message = "Retry this row, skip it and open the next row, or stop this ID."
                self.set_state("Browser closed", message)
                self.owner.show_browser_recovery(self, message, ready=True)
        elif kind in {"run_completed", "run_stopped", "portal_closed", "fatal_error"}:
            browser_closed = kind == "run_stopped" and bool(event.data.get("browser_closed"))
            if browser_closed and not self.browser_recovery_pending:
                self.browser_recovery_pending = self.owner.should_offer_browser_recovery(self)
                self.browser_recovery_ready = False
            self.starting = False
            self.running = False
            self.paused = False
            self.auto_waiting = False
            self.portal_session_open = False
            if kind == "run_stopped" and self.browser_recovery_pending:
                message = "This browser was closed. Finishing cleanup before it can restart..."
                self.set_state("Browser closed", message)
                self.owner.show_browser_recovery(self, message, ready=False)
            else:
                self.browser_recovery_pending = False
                self.browser_recovery_ready = False
                state = (
                    "Complete"
                    if kind == "run_completed"
                    else "Error" if kind == "fatal_error" else "Stopped"
                )
                self.set_state(state, event.message)
            if kind == "fatal_error":
                show_windows_notification(f"{self.display_name} automation error", event.message)
        self._set_buttons()

    def _show_error(self, event: UiEvent) -> None:
        if self.error_window is not None and self.error_window.winfo_exists():
            self.error_window.destroy()
        window = tk.Toplevel(self.owner.root)
        self.error_window = window
        window.title(f"{self.display_name} needs attention")
        window.attributes("-topmost", True)
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=event.message, wraplength=560).pack(anchor="w")
        if event.data.get("post_payment_warning"):
            ttk.Label(
                frame,
                text="Payment may already have started. Retrying can create a duplicate charge.",
                foreground="#b91c1c",
                wraplength=560,
            ).pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="w", pady=(12, 0))
        ttk.Button(buttons, text="Retry", command=lambda: self._error_choice("retry")).pack(side="left")
        if event.data.get("can_continue"):
            ttk.Button(buttons, text="Continue", command=lambda: self._error_choice("continue")).pack(
                side="left", padx=(7, 0)
            )
        ttk.Button(buttons, text="Move to next", command=lambda: self._error_choice("next")).pack(
            side="left", padx=(7, 0)
        )

    def _error_choice(self, action: str) -> None:
        if self.error_window is not None and self.error_window.winfo_exists():
            self.error_window.destroy()
        self.error_window = None
        self.paused = False
        self.auto_waiting = False
        self.owner.controller.decide_error(self.run_id, action)
        self.set_state("Running", "Applying error decision")

    def decide_error(self, action: str) -> None:
        self._error_choice(action)

    def recover_browser(self, action: str) -> None:
        if not self.browser_recovery_pending or not self.browser_recovery_ready:
            return
        if action == "next" and not self._skip_browser_closed_row():
            self.owner.show_browser_recovery(
                self,
                "Could not save the skipped row. Close the CSV in Excel, then try again.",
                ready=True,
            )
            return
        self.browser_recovery_pending = False
        self.browser_recovery_ready = False
        if not self.start():
            self.browser_recovery_pending = True
            self.browser_recovery_ready = True
            self.owner.show_browser_recovery(
                self,
                "The browser could not restart. Check this ID's settings and try again.",
                ready=True,
            )

    def dismiss_browser_recovery(self) -> None:
        self.browser_recovery_pending = False
        self.browser_recovery_ready = False
        self.set_state("Stopped", "Browser recovery dismissed for this ID.")

    def _skip_browser_closed_row(self) -> bool:
        try:
            path = Path(self.csv_var.get().strip())
            store = CsvBatchStore(path)
            store.load()
            pending = list(store.pending_rows())
            if not pending:
                return True
            selected: tuple[int, dict[str, str]] | None = None
            if self.current_row_number is not None:
                index = self.current_row_number - 1
                if 0 <= index < len(store.rows):
                    row = store.rows[index]
                    if int(row["processed_quantity"]) < int(row["quantity"]):
                        selected = (index, row)
            _index, row = selected or pending[0]
            try:
                stage = Stage(row.get("last_stage", ""))
            except ValueError:
                stage = Stage.IDLE
            store.mark_skipped_row(
                row,
                stage,
                "Browser was closed; row skipped from the status dock.",
            )
            store.persist()
            self._load_preview(path, quiet=True)
            return True
        except Exception as error:
            messagebox.showerror("Skip row", str(error), parent=self.owner.root)
            return False

    def _render_rows(self, rows: list[dict[str, str]], current_row: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            tags: tuple[str, ...] = ()
            if row.get("row_number") == str(current_row):
                tags = ("current",)
            elif row.get("status") == "completed":
                tags = ("completed",)
            self.tree.insert(
                "",
                "end",
                tags=tags,
                values=(
                    row.get("row_number", ""),
                    row.get("first_party_name", ""),
                    row.get("second_party_name", ""),
                    row.get("district", ""),
                    row.get("amount", ""),
                    row.get("status", ""),
                    f"{row.get('completed', '0')} / {row.get('processed', '0')} / {row.get('quantity', '1')}",
                    row.get("error", ""),
                ),
            )

    def set_state(self, state: str, detail: str = "") -> None:
        self.run_status_var.set(f"{state}: {detail}" if detail else state)
        self.owner.update_tab_state(self, state, detail)

    def _set_buttons(self) -> None:
        self.start_button.configure(
            state=(
                "disabled"
                if self.is_active
                or self.portal_session_open
                or not self.is_enabled
                or (self.browser_recovery_pending and not self.browser_recovery_ready)
                else "normal"
            )
        )
        self.pause_button.configure(state="normal" if self.running and not self.paused else "disabled")
        self.resume_button.configure(state="normal" if self.running and self.paused else "disabled")
        self.stop_button.configure(
            state="normal" if self.running or self.starting or self.portal_session_open else "disabled"
        )
        self.toggle_enabled_button.configure(
            text="Disable ID" if self.is_enabled else "Enable ID",
            state="disabled" if self.is_active or self.portal_session_open else "normal",
        )
        self.delete_profile_button.configure(
            state="disabled" if self.is_active or self.portal_session_open else "normal"
        )

    def _delete_portal_profile(self) -> None:
        try:
            profile_path, result = self.config.reset_portal_profile()
        except OSError as error:
            messagebox.showerror(
                "Delete profile",
                f"Could not delete this ID's local browser profile:\n{error}",
                parent=self.owner.root,
            )
            return

        self.profile_var.set(str(profile_path))
        self.owner.save_config()
        if result == "deleted":
            message = "Portal browser profile deleted. It will be recreated when this ID starts."
        elif result == "path_reset":
            message = "Portal profile path reset to this ID's local profile path."
        else:
            message = "No saved portal browser profile folder was found."
        self.owner.append_session_log(f"{self.display_name}: {message}")
        messagebox.showinfo("Delete profile", message, parent=self.owner.root)

    def _export_this_tab(self) -> None:
        self.owner._export_specific_tab(self)

    def export_data(self) -> TabExportData:
        self._save_settings()
        creds = self.entered_credentials()
        has_creds = any((
            creds.citizen_username,
            creds.citizen_password,
            creds.egras_username,
            creds.egras_password,
        ))
        if not has_creds:
            try:
                loaded = self.owner.credential_store.load(self.tab_id)
                if loaded is not None:
                    creds = loaded
            except Exception:
                pass
        return TabExportData(
            tab_config=self.config.to_dict(),
            credentials={
                "citizen_username": creds.citizen_username,
                "citizen_password": creds.citizen_password,
                "egras_username": creds.egras_username,
                "egras_password": creds.egras_password,
            },
            credentials_saved=self.credentials_saved,
        )

    def refresh_from_config(self) -> None:
        try:
            saved_credentials = self.owner.credential_store.load(self.tab_id)
        except Exception:
            saved_credentials = None

        self.citizen_user_var.set(saved_credentials.citizen_username if saved_credentials else "")
        self.citizen_password_var.set(saved_credentials.citizen_password if saved_credentials else "")
        self.egras_user_var.set(saved_credentials.egras_username if saved_credentials else "")
        self.egras_password_var.set(saved_credentials.egras_password if saved_credentials else "")
        self.credentials_saved = saved_credentials is not None
        self.credentials_status_var.set(
            f"saved in {credential_store_label()}" if self.credentials_saved else ""
        )

        self.sms_user_id_var.set(self.config.sms_user_id)
        self.sms_server_url_var.set(self.config.sms_server_url or DEFAULT_SMS_SERVER_URL)
        self.csv_var.set(self.config.last_csv_path)
        self.download_var.set(self.config.last_download_path)
        self.article_var.set(self.config.last_article)
        self.payment_trigger_url_var.set(self.config.payment_trigger_url)
        method = self.config.payment_trigger_method.strip().upper()
        self.payment_trigger_method_var.set(method if method in {"GET", "POST"} else "GET")
        self.mode_var.set(
            self.config.last_mode
            if self.config.last_mode in {mode.value for mode in RunMode}
            else RunMode.ASSISTED
        )
        engines = available_ocr_engines()
        engine_values = {engine.value for engine in engines}
        selected_engine = (
            self.config.ocr_engine if self.config.ocr_engine in engine_values else OcrEngine.PADDLEOCR
        )
        self.ocr_engine_var.set(selected_engine)
        self.ocr_enabled_var.set(getattr(self.config, "ocr_enabled", True))
        copy_modes = {mode.value for mode in CaptchaCopyMode}
        copy_mode = (
            self.config.captcha_copy_mode
            if self.config.captcha_copy_mode in copy_modes
            else CaptchaCopyMode.DIRECT
        )
        self.captcha_copy_mode_var.set(copy_mode)
        self.save_captcha_images_var.set(self.config.save_captcha_images)
        self.fresh_browser_var.set(self.config.fresh_browser_per_unit)
        self.retry_egras_otp_once_var.set(self.config.retry_egras_otp_once)
        self.profile_var.set(self.config.portal_profile_path)
        self.portal_browser_var.set(self._saved_browser_name())

        csv_path = Path(self.config.last_csv_path) if self.config.last_csv_path else None
        if csv_path and csv_path.is_file():
            self._load_preview(csv_path, quiet=True)
        else:
            self.csv_valid = False
            self._render_rows([])
        self._set_buttons()

    def destroy(self) -> None:
        if self.error_window is not None and self.error_window.winfo_exists():
            self.error_window.destroy()
        self.notebook.forget(self.frame)
        self.frame.destroy()
