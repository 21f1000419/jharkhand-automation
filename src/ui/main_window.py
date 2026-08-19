from __future__ import annotations

import queue
import shutil
import subprocess
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.browser_detection import detect_supported_browsers
from core.config import DEFAULT_SMS_SERVER_URL, AppConfig, ConfigStore, app_data_directory
from core.controller import AutomationController
from core.form_options import ARTICLE_OPTIONS
from core.models import BrowserEngine, Credentials, PortalBrowser, RunMode, RunOptions, UiEvent
from services.credential_store import WindowsCredentialStore
from services.csv_store import CsvBatchStore
from services.windows_notifications import show_windows_notification


class MainWindow:
    CUSTOM_BROWSER_OPTION = "Choose custom browser..."

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
        self.gemini_ready = False
        self.gemini_checking = True
        # self.gemini_login_required = False
        # self.gemini_login_browser_open = False
        self.running = False
        self.starting = False
        self.portal_session_open = False
        self.paused = False
        self.auto_waiting = False
        self.csv_valid = False
        self.credential_store = WindowsCredentialStore()
        try:
            saved_credentials = self.credential_store.load()
        except (OSError, RuntimeError, ValueError):
            saved_credentials = None

        self.chrome_var = tk.StringVar(value=config.chrome_executable)
        self.profile_var = tk.StringVar(value=str(config.profile_path))
        self.csv_var = tk.StringVar(value=config.last_csv_path)
        self.download_var = tk.StringVar(value=config.last_download_path)
        self.article_var = tk.StringVar(value=config.last_article)
        self.portal_browser_var = tk.StringVar()
        valid_engines = {engine.value for engine in BrowserEngine}
        saved_custom_engine = (
            config.custom_portal_browser_engine
            if config.custom_portal_browser_engine in valid_engines
            else self._infer_browser_engine(Path(config.custom_portal_browser_path))
        )
        self.custom_portal_engine_var = tk.StringVar(
            value=saved_custom_engine
        )
        valid_modes = {mode.value for mode in RunMode}
        saved_mode = config.last_mode if config.last_mode in valid_modes else RunMode.ASSISTED
        self.mode_var = tk.StringVar(value=saved_mode)
        self.sms_user_id_var = tk.StringVar(value=config.sms_user_id)
        self.sms_server_url_var = tk.StringVar(value=config.sms_server_url or DEFAULT_SMS_SERVER_URL)
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
            value="Saved securely in Windows Credential Manager." if self.credentials_saved else ""
        )
        self.gemini_status_var = tk.StringVar(
            value=(
                "Browser profile: checking existing Google/Gemini login..."
                if config.gemini_verified
                else "Browser profile: checking Google/Gemini login..."
            )
        )
        self.captcha_warning_var = tk.StringVar(
            value="CAPTCHA warning: Gemini OCR is inactive. CAPTCHAs must be entered manually."
        )
        self.run_status_var = tk.StringVar(value="Idle")
        self.session_log_lines: list[str] = []
        self.portal_browsers: dict[str, PortalBrowser] = {}
        self._detect_portal_browsers()

        self._build_menu()
        self._build()
        self._update_custom_engine_control()
        if self.config.last_csv_path:
            csv_path = Path(self.config.last_csv_path)
            if csv_path.is_file():
                self._load_preview(csv_path, quiet=True)
        self._set_run_buttons()
        self.controller.record_activity("application_started", "Desktop application opened.")
        self.root.after(100, self._drain_events)
        self.root.after(200, self._initial_gemini_check)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        self.root.title("Compitcom eStamp Batch Automation")
        self.root.minsize(980, 700)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = max(980, int(screen_width * 0.84))
        window_height = max(700, int(screen_height * 0.88))
        window_left = max(0, (screen_width - window_width) // 2)
        window_top = int(screen_height * 0.02)
        self.root.geometry(f"{window_width}x{window_height}+{window_left}+{window_top}")

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
        heading = ttk.Frame(header)
        heading.grid(row=0, column=0, sticky="w")
        ttk.Label(heading, text="eStamp Batch Automation", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            heading,
            text="Visible browser automation for Jharkhand NGDRS/eGRAS. Payment remains manual.",
        ).pack(anchor="w")

        sms_settings = ttk.Frame(header)
        sms_settings.grid(row=0, column=1, sticky="e", padx=(16, 0))
        ttk.Label(sms_settings, text="SMS User ID").grid(row=0, column=0, sticky="w")
        sms_user_id_entry = ttk.Entry(sms_settings, textvariable=self.sms_user_id_var, width=16)
        sms_user_id_entry.grid(row=0, column=1, padx=(5, 10))
        sms_user_id_entry.bind("<FocusOut>", self._save_non_secret_settings)
        ttk.Label(sms_settings, text="Server").grid(row=0, column=2, sticky="w")
        sms_server_url_entry = ttk.Entry(sms_settings, textvariable=self.sms_server_url_var, width=33)
        sms_server_url_entry.grid(row=0, column=3, padx=(5, 0))
        sms_server_url_entry.bind("<FocusOut>", self._save_non_secret_settings)
        ttk.Label(
            sms_settings,
            text="Warning: Empty or incorrect User ID prevents automatic OTP fetch.",
            foreground="#b7791f",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(3, 0))

        gemini_status = ttk.Frame(container)
        gemini_status.pack(fill="x", pady=(0, 8))
        ttk.Label(gemini_status, textvariable=self.gemini_status_var).pack(side="left")
        self.ocr_browser_button = ttk.Button(
            gemini_status, text="Start OCR browser", command=self._verify_gemini
        )
        self.ocr_browser_button.pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(
            gemini_status,
            textvariable=self.captcha_warning_var,
            foreground="#b7791f",
            wraplength=720,
        ).pack(side="left", padx=(12, 0))

        middle = ttk.Frame(container)
        middle.pack(fill="x", pady=(0, 8))
        middle.columnconfigure(0, weight=1)
        middle.columnconfigure(1, weight=1)

        credentials = ttk.LabelFrame(middle, text="1. Optional login credentials", padding=10)
        self.credentials_panel = credentials
        credentials.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        credentials.columnconfigure(1, weight=1)
        entries = [
            ("Citizen username", self.citizen_user_var, False),
            ("Citizen password", self.citizen_password_var, True),
            ("eGRAS username", self.egras_user_var, False),
            ("eGRAS password", self.egras_password_var, True),
        ]
        for row, (label, variable, secret) in enumerate(entries):
            ttk.Label(credentials, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            ttk.Entry(credentials, textvariable=variable, show="•" if secret else "").grid(
                row=row, column=1, sticky="ew", pady=3
            )
        credential_buttons = ttk.Frame(credentials)
        credential_buttons.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(
            credential_buttons, text="Save credentials", command=self._save_credentials
        ).pack(side="left")
        self.clear_credentials_button = ttk.Button(
            credential_buttons, text="Clear saved credentials", command=self._clear_saved_credentials
        )
        if self.credentials_saved:
            self.clear_credentials_button.pack(side="left", padx=(8, 0))
        self.credentials_status_label = ttk.Label(
            credential_buttons, textvariable=self.credentials_status_var
        )
        self.credentials_status_label.pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(
            credentials,
            text=(
                "Blank credentials cause a manual login checkpoint. Values remain session-only unless "
                "you explicitly save them."
            ),
            foreground="#555555",
            wraplength=500,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        batch = ttk.LabelFrame(middle, text="2. Batch setup", padding=10)
        self.batch_panel = batch
        batch.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        batch.columnconfigure(1, weight=1)
        ttk.Label(batch, text="Portal browser").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.portal_browser_box = ttk.Combobox(
            batch,
            textvariable=self.portal_browser_var,
            values=self._portal_browser_choices(),
            state="readonly",
        )
        self.portal_browser_box.grid(row=0, column=1, sticky="ew")
        self.portal_browser_box.bind("<<ComboboxSelected>>", self._portal_browser_selected)
        ttk.Button(batch, text="Refresh", command=self._refresh_portal_browsers).grid(
            row=0, column=2, padx=(8, 0)
        )
        self.portal_browser_note = ttk.Label(
            batch, text=self._portal_browser_note(), foreground="#555555", wraplength=500
        )
        self.custom_engine_frame = ttk.Frame(batch)
        ttk.Label(self.custom_engine_frame, text="Custom engine").pack(side="left")
        self.custom_portal_engine_box = ttk.Combobox(
            self.custom_engine_frame,
            textvariable=self.custom_portal_engine_var,
            values=[BrowserEngine.CHROMIUM, BrowserEngine.FIREFOX],
            state="readonly",
            width=12,
        )
        self.custom_portal_engine_box.pack(side="left", padx=(8, 0))
        self.custom_portal_engine_box.bind("<<ComboboxSelected>>", self._custom_portal_engine_changed)
        self.portal_browser_note.grid(row=2, column=0, columnspan=3, sticky="w", pady=(5, 0))

        ttk.Label(batch, text="Article for all rows").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=(8, 0)
        )
        self.article_box = ttk.Combobox(batch, textvariable=self.article_var, values=ARTICLE_OPTIONS)
        self.article_box.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        self.article_box.bind("<<ComboboxSelected>>", self._save_non_secret_settings)
        self.article_box.bind("<FocusOut>", self._save_non_secret_settings)
        ttk.Label(
            batch,
            text="Choose a listed article or type one. The portal validates it before every submission.",
            foreground="#555555",
            wraplength=500,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Label(batch, text="Filled batch CSV").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.csv_entry = ttk.Entry(batch, textvariable=self.csv_var)
        self.csv_entry.grid(row=5, column=1, sticky="ew", pady=(8, 0))
        self.csv_entry.bind("<FocusOut>", self._on_csv_entry_changed)
        self.csv_entry.bind("<Return>", self._on_csv_entry_changed)
        ttk.Button(batch, text="Select CSV…", command=self._browse_csv).grid(
            row=5, column=2, padx=(8, 0), pady=(8, 0)
        )
        ttk.Label(
            batch,
            text="eStamp download folder",
        ).grid(row=6, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.download_entry = ttk.Entry(batch, textvariable=self.download_var)
        self.download_entry.grid(row=6, column=1, sticky="ew", pady=(8, 0))
        self.download_entry.bind("<FocusOut>", self._save_non_secret_settings)
        ttk.Button(batch, text="Browse…", command=self._browse_download).grid(
            row=6, column=2, padx=(8, 0), pady=(8, 0)
        )
        modes = ttk.Frame(batch)
        modes.grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Radiobutton(
            modes,
            text="Assisted errors",
            variable=self.mode_var,
            value=RunMode.ASSISTED,
            command=self._save_non_secret_settings,
        ).pack(side="left")
        ttk.Radiobutton(
            modes,
            text="Continuous (skip errors)",
            variable=self.mode_var,
            value=RunMode.CONTINUOUS,
            command=self._save_non_secret_settings,
        ).pack(side="left", padx=12)

        controls = ttk.Frame(container)
        controls.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(controls, text="Start", command=self._start)
        self.pause_resume_controls = ttk.Frame(controls)
        self.pause_button = tk.Button(
            self.pause_resume_controls,
            text="Pause",
            command=self._pause,
            background="#b45309",
            activebackground="#92400e",
            foreground="white",
            activeforeground="white",
            relief="flat",
            padx=10,
        )
        self.resume_button = tk.Button(
            self.pause_resume_controls,
            text="Resume",
            command=self._resume,
            background="#15803d",
            activebackground="#166534",
            foreground="white",
            activeforeground="white",
            relief="flat",
            padx=10,
        )
        self.stop_button = tk.Button(
            controls,
            text="Stop",
            command=self._stop,
            background="#b91c1c",
            activebackground="#991b1b",
            foreground="white",
            activeforeground="white",
            relief="flat",
            padx=10,
        )
        self.start_button.pack(side="left")
        self.pause_resume_controls.pack(side="left", padx=(8, 0))
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Label(controls, textvariable=self.run_status_var, style="Status.TLabel").pack(side="left")

        table_frame = ttk.LabelFrame(container, text="Batch progress", padding=6)
        table_frame.pack(fill="both", expand=True, pady=(0, 8))
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = ("row", "first_party", "second_party", "district", "amount", "status", "completed", "error")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        self.tree.tag_configure("current", background="#bfdbfe")
        self.tree.tag_configure("completed", background="#e5e7eb", foreground="#555555")
        headings = {
            "row": "CSV row",
            "first_party": "First party",
            "second_party": "Second party",
            "district": "District",
            "amount": "Amount",
            "status": "Status",
            "completed": "Completed",
            "error": "Last error",
        }
        widths = {
            "row": 65,
            "first_party": 170,
            "second_party": 170,
            "district": 120,
            "amount": 90,
            "status": 100,
            "completed": 85,
            "error": 260,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                stretch=column in {"first_party", "second_party", "error"},
            )
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        menu.add_command(label="Download CSV Format…", command=self._download_template)
        profile_menu = tk.Menu(menu, tearoff=False)
        profile_menu.add_command(label="Edit profile path…", command=self._show_ocr_profile_settings)
        profile_menu.add_command(label="Delete selected profile…", command=self._delete_ocr_profile)
        menu.add_cascade(label="OCR Profile", menu=profile_menu)

        activity_menu = tk.Menu(menu, tearoff=False)
        activity_menu.add_command(label="Current Session…", command=self._show_session_log)
        activity_menu.add_command(label="Open Daily Log Folder", command=self._open_log_folder)
        menu.add_cascade(label="Activity", menu=activity_menu)

        self.root.configure(menu=menu)

    def _initial_gemini_check(self) -> None:
        self.gemini_checking = True
        self.gemini_status_var.set("Gemini OCR: checking...")
        # self.gemini_status_var.set("Gemini OCR: starting headless check...")  # Headless mode
        self._set_run_buttons()
        self.controller.record_activity("gemini_startup_check_started")
        self.controller.verify_gemini()

    def _detect_portal_browsers(self) -> None:
        browsers = detect_supported_browsers()
        custom_browser = self._custom_portal_browser()
        if custom_browser is not None and all(
            browser.executable != custom_browser[1].executable for browser in browsers
        ):
            browsers.append(custom_browser[1])
        self.portal_browsers = {browser.name: browser for browser in browsers}
        selected = next(
            (
                name
                for name, browser in self.portal_browsers.items()
                if str(browser.executable).casefold() == self.config.last_portal_browser_path.casefold()
            ),
            next(iter(self.portal_browsers), ""),
        )
        self.portal_browser_var.set(selected)

    def _portal_browser_choices(self) -> list[str]:
        return [*self.portal_browsers, self.CUSTOM_BROWSER_OPTION]

    def _custom_portal_browser(self) -> tuple[str, PortalBrowser] | None:
        executable = Path(self.config.custom_portal_browser_path)
        if not executable.is_file():
            return None
        try:
            engine = BrowserEngine(self.config.custom_portal_browser_engine)
        except ValueError:
            engine = self._infer_browser_engine(executable)
        name = f"Custom browser ({executable.name})"
        return name, PortalBrowser(name, executable, engine)

    @staticmethod
    def _infer_browser_engine(executable: Path) -> BrowserEngine:
        name = executable.name.casefold()
        if any(marker in name for marker in ("firefox", "zen", "floorp", "librewolf", "waterfox")):
            return BrowserEngine.FIREFOX
        return BrowserEngine.CHROMIUM

    def _portal_browser_note(self) -> str:
        if not self.portal_browsers:
            return (
                "No supported browser was found. Install Chrome, Edge, Brave, Opera, or Firefox, "
                "then refresh or choose Custom browser from the list."
            )
        return (
            "The portal opens in a fresh session; it does not use your personal browser profile "
            "or saved login. Firefox-based choices use Playwright's managed Firefox build."
        )

    def _refresh_portal_browsers(self) -> None:
        self._record_ui_action("refresh_portal_browsers_clicked")
        previous = self.portal_browser_var.get()
        self._detect_portal_browsers()
        if previous in self.portal_browsers:
            self.portal_browser_var.set(previous)
        self.portal_browser_box.configure(values=self._portal_browser_choices())
        self.portal_browser_note.configure(text=self._portal_browser_note())
        self._update_custom_engine_control()
        self._set_run_buttons()

    def _portal_browser_selected(self, _event: object) -> None:
        if self.portal_browser_var.get() == self.CUSTOM_BROWSER_OPTION:
            self._choose_custom_portal_browser()
            return
        self._update_custom_engine_control()
        self._save_non_secret_settings()

    def _choose_custom_portal_browser(self) -> None:
        self._record_ui_action("choose_custom_portal_browser_clicked")
        selected = filedialog.askopenfilename(
            title="Choose portal browser executable",
            filetypes=[("Browser executable", "*.exe"), ("All files", "*.*")],
            parent=self.root,
            initialdir=self._dialog_directory(self.config.custom_portal_browser_path),
        )
        if not selected:
            self._detect_portal_browsers()
            self.portal_browser_box.configure(values=self._portal_browser_choices())
            self._update_custom_engine_control()
            return
        self.config.custom_portal_browser_path = selected
        self.custom_portal_engine_var.set(self._infer_browser_engine(Path(selected)))
        self.config.custom_portal_browser_engine = self.custom_portal_engine_var.get()
        self._detect_portal_browsers()
        custom_browser = self._custom_portal_browser()
        if custom_browser is not None:
            selected_name = next(
                (
                    name
                    for name, browser in self.portal_browsers.items()
                    if browser.executable == custom_browser[1].executable
                ),
                custom_browser[0],
            )
            self.portal_browser_var.set(selected_name)
        self._save_non_secret_settings()
        self.portal_browser_box.configure(values=self._portal_browser_choices())
        self.portal_browser_note.configure(text=self._portal_browser_note())
        self._update_custom_engine_control()
        self._set_run_buttons()

    def _custom_portal_engine_changed(self, _event: object) -> None:
        self.config.custom_portal_browser_engine = self.custom_portal_engine_var.get()
        self._detect_portal_browsers()
        custom_browser = self._custom_portal_browser()
        if custom_browser is not None:
            selected_name = next(
                (
                    name
                    for name, browser in self.portal_browsers.items()
                    if browser.executable == custom_browser[1].executable
                ),
                custom_browser[0],
            )
            self.portal_browser_var.set(selected_name)
        self._save_non_secret_settings()
        self.portal_browser_box.configure(values=self._portal_browser_choices())

    def _update_custom_engine_control(self) -> None:
        selected = self.portal_browsers.get(self.portal_browser_var.get())
        is_custom = selected is not None and selected.name.startswith("Custom browser (")
        if is_custom:
            self.custom_engine_frame.grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 0))
        else:
            self.custom_engine_frame.grid_remove()

    def _save_non_secret_settings(self, _event: object | None = None) -> None:
        self.config.last_download_path = self.download_var.get().strip()
        self.config.last_mode = self.mode_var.get()
        self.config.sms_user_id = self.sms_user_id_var.get().strip()
        self.config.sms_server_url = self.sms_server_url_var.get().strip() or DEFAULT_SMS_SERVER_URL
        self.config.last_article = self.article_var.get().strip()
        self.config.last_csv_path = self.csv_var.get().strip()
        browser = self.portal_browsers.get(self.portal_browser_var.get())
        if browser is not None:
            self.config.last_portal_browser_path = str(browser.executable)
        self.config_store.save(self.config)

    def _browse_chrome(self) -> None:
        self._record_ui_action("browse_chrome_clicked")
        selected = filedialog.askopenfilename(
            title="Choose Google Chrome",
            filetypes=[("Chrome executable", "chrome.exe"), ("Executables", "*.exe")],
            parent=self.root,
            initialdir=str(Path(self.chrome_var.get()).parent),
        )
        if selected:
            self.chrome_var.set(selected)
            self.gemini_ready = False
            self._save_browser_settings(reconfigure=True)
            self._set_run_buttons()

    def _on_csv_entry_changed(self, _event: object | None = None) -> None:
        if self.running or self.starting:
            return
        path_str = self.csv_var.get().strip()
        self._save_non_secret_settings()
        if path_str:
            path = Path(path_str)
            if path.is_file():
                self._load_preview(path, quiet=True)
            else:
                self.csv_valid = False
                self._render_rows([])
        else:
            self.csv_valid = False
            self._render_rows([])
        self._set_run_buttons()

    def _browse_csv(self) -> None:
        if self.running or self.starting:
            messagebox.showinfo(
                "Automation active",
                "Stop the current batch before selecting a different CSV.",
                parent=self.root,
            )
            return
        self._record_ui_action("select_csv_clicked")
        selected = filedialog.askopenfilename(
            title="Choose batch CSV",
            filetypes=[("CSV files", "*.csv")],
            parent=self.root,
            initialdir=self._dialog_directory(self.csv_var.get()),
        )
        if selected:
            self.csv_valid = False
            self.csv_var.set(selected)
            self._save_non_secret_settings()
            self._load_preview(Path(selected))
            self._set_run_buttons()

    def _browse_download(self) -> None:
        self._record_ui_action("choose_download_folder_clicked")
        selected = filedialog.askdirectory(
            title="Choose eStamp download folder",
            parent=self.root,
            initialdir=self._dialog_directory(self.download_var.get()),
            mustexist=True,
        )
        if selected:
            self.download_var.set(selected)
            self._save_non_secret_settings()

    @staticmethod
    def _dialog_directory(value: str) -> str:
        """Return an existing directory for a native Windows file dialog."""
        candidate = Path(value).expanduser() if value.strip() else Path.home()
        if candidate.is_file():
            candidate = candidate.parent
        while not candidate.is_dir() and candidate != candidate.parent:
            candidate = candidate.parent
        return str(candidate if candidate.is_dir() else Path.home())

    def _download_template(self) -> None:
        self._record_ui_action("download_csv_format_clicked")
        selected = filedialog.asksaveasfilename(
            title="Save CSV format",
            defaultextension=".csv",
            initialfile="estamp_batch_template.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self.root,
            initialdir=self._dialog_directory(self.csv_var.get()),
        )
        if not selected:
            return
        try:
            CsvBatchStore.write_template(Path(selected))
            self._append_session_log(
                "CSV template downloaded. Fill it, save it, then choose it with Select CSV… before starting."
            )
            messagebox.showinfo(
                "CSV format downloaded",
                "Fill the downloaded CSV and save it. Then click Select CSV… to load it into the batch.",
                parent=self.root,
            )
        except Exception as error:
            messagebox.showerror("Template error", str(error), parent=self.root)

    def _verify_gemini(self) -> None:
        self._record_ui_action("start_ocr_browser_clicked")
        if not self._save_browser_settings(reconfigure=False):
            return
        # self.gemini_login_required = False
        self.gemini_status_var.set("Gemini OCR: opening browser and checking...")
        # self.gemini_status_var.set("Gemini OCR: checking headlessly...")  # Headless mode
        self._set_run_buttons()
        self.controller.verify_gemini()

    # Login setup browser helper commented out for non-headless mode:
    # def _open_ocr_login_browser(self) -> None:
    #     self._record_ui_action("open_ocr_login_browser_clicked")
    #     if not self._save_browser_settings(reconfigure=False):
    #         return
    #     self.gemini_login_browser_open = True
    #     self.gemini_status_var.set("Gemini OCR: opening Chrome profile for sign-in...")
    #     self._set_run_buttons()
    #     self.controller.open_gemini_login_browser()

    def _close_ocr_browser(self) -> None:
        self._record_ui_action("close_ocr_browser_clicked")
        self.gemini_ready = False
        # self.gemini_login_browser_open = False
        self.config.gemini_verified = False
        self.config_store.save(self.config)
        self.captcha_warning_var.set(
            "CAPTCHA warning: Gemini OCR is inactive. CAPTCHAs must be entered manually."
        )
        self.gemini_status_var.set("Gemini OCR: stopping...")
        self._set_run_buttons()
        self.controller.close_gemini_ocr()

    def _save_browser_settings(self, reconfigure: bool) -> bool:
        chrome = Path(self.chrome_var.get().strip())
        if not chrome.is_file():
            messagebox.showerror("Chrome required", "Choose a valid chrome.exe file.", parent=self.root)
            return False
        changed = str(chrome) != self.config.chrome_executable
        profile_changed = self.profile_var.get().strip() != self.config.chrome_profile_path
        self.config.chrome_executable = str(chrome)
        self.config.chrome_profile_path = self.profile_var.get().strip()
        self.config_store.save(self.config)
        if reconfigure or changed or profile_changed:
            self.controller.reconfigure_browser()
        return True

    def _show_ocr_profile_settings(self) -> None:
        self._record_ui_action("ocr_profile_settings_clicked")
        dialog = tk.Toplevel(self.root)
        dialog.title("OCR Browser Profile Settings")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="OCR Browser Profile Settings", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            frame,
            text=(
                "This dedicated Chrome profile stores the Google sign-in used by headless Gemini OCR. "
                "It is separate from your normal Chrome profile."
            ),
            wraplength=620,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 12))
        ttk.Label(frame, text="Profile path").grid(row=2, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(frame, textvariable=self.profile_var, width=64).grid(
            row=2, column=1, columnspan=2, sticky="ew"
        )

        def save_profile_path() -> None:
            if not self.profile_var.get().strip():
                messagebox.showwarning(
                    "OCR Browser Profile Settings", "Enter a profile folder path.", parent=dialog
                )
                return
            if self._save_browser_settings(reconfigure=True):
                self._append_session_log("OCR browser profile changed.")

        def use_default_profile() -> None:
            self.profile_var.set(str(app_data_directory() / "chrome-profile"))
            if self._save_browser_settings(reconfigure=True):
                self._append_session_log("OCR browser profile reset to the default location.")

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Button(buttons, text="Save path", command=save_profile_path).pack(side="left")
        ttk.Button(buttons, text="Use default", command=use_default_profile).pack(side="left", padx=8)
        ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side="left")

    def _delete_ocr_profile(self) -> None:
        self._record_ui_action("delete_ocr_profile_clicked")
        configured_path = self.profile_var.get().strip()
        if not configured_path:
            messagebox.showinfo("Delete OCR profile", "No OCR browser profile is selected.", parent=self.root)
            return

        try:
            profile_path = Path(configured_path).expanduser().resolve()
        except OSError as error:
            messagebox.showerror("Delete OCR profile", str(error), parent=self.root)
            return
        if not profile_path.is_dir():
            messagebox.showinfo(
                "Delete OCR profile",
                "The selected OCR browser profile folder does not exist.",
                parent=self.root,
            )
            return

        protected_paths = (
            Path(profile_path.anchor),
            Path.home().resolve(),
            Path.cwd().resolve(),
            app_data_directory().resolve(),
        )
        if any(
            protected == profile_path or protected.is_relative_to(profile_path)
            for protected in protected_paths
        ):
            messagebox.showerror(
                "Delete OCR profile",
                "This selected path is too broad to delete as an OCR profile.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Delete OCR profile",
            "This permanently removes the selected OCR Chrome profile, including its Google sign-in and "
            f"browser data:\n\n{profile_path}\n\nContinue?",
            parent=self.root,
            default=messagebox.NO,
        ):
            return

        self.controller.close_gemini_ocr()
        self.root.after(800, lambda: self._delete_ocr_profile_directory(profile_path))

    def _delete_ocr_profile_directory(self, profile_path: Path) -> None:
        try:
            shutil.rmtree(profile_path)
        except OSError as error:
            messagebox.showerror(
                "Delete OCR profile",
                "Could not remove the profile. Ensure any Chrome window using it is closed, then try again."
                f"\n\n{error}",
                parent=self.root,
            )
            return
        self.gemini_ready = False
        self.config.gemini_verified = False
        self.config_store.save(self.config)
        self.gemini_status_var.set("Gemini OCR: profile deleted")
        self.captcha_warning_var.set(
            "CAPTCHA warning: Gemini OCR is inactive. CAPTCHAs must be entered manually."
        )
        self._append_session_log("OCR browser profile deleted.")
        self._set_run_buttons()

    def _entered_credentials(self) -> Credentials:
        return Credentials(
            citizen_username=self.citizen_user_var.get().strip(),
            citizen_password=self.citizen_password_var.get(),
            egras_username=self.egras_user_var.get().strip(),
            egras_password=self.egras_password_var.get(),
        )

    def _save_credentials(self) -> None:
        self._record_ui_action("save_credentials_clicked")
        credentials = self._entered_credentials()
        pairs = (
            ("Citizen", credentials.citizen_username, credentials.citizen_password),
            ("eGRAS", credentials.egras_username, credentials.egras_password),
        )
        incomplete = [name for name, username, password in pairs if bool(username) != bool(password)]
        if incomplete:
            messagebox.showwarning(
                "Incomplete credentials",
                f"Enter both username and password for: {', '.join(incomplete)}.",
                parent=self.root,
            )
            return
        if not any(username and password for _name, username, password in pairs):
            messagebox.showwarning(
                "No credentials",
                "Enter at least one username and password pair before saving.",
                parent=self.root,
            )
            return
        try:
            self.credential_store.save(credentials)
        except (OSError, RuntimeError, ValueError) as error:
            self.credentials_status_var.set("Credentials could not be saved.")
            messagebox.showerror("Save credentials", str(error), parent=self.root)
            return
        self.credentials_saved = True
        self.credentials_status_var.set("Saved securely in Windows Credential Manager.")
        if not self.clear_credentials_button.winfo_manager():
            self.clear_credentials_button.pack(
                side="left", padx=(8, 0), before=self.credentials_status_label
            )
        self.controller.record_activity("credentials_saved", "Portal credentials saved securely.")

    def _clear_saved_credentials(self) -> None:
        self._record_ui_action("clear_saved_credentials_clicked")
        if not messagebox.askyesno(
            "Clear saved credentials",
            "Remove the saved Citizen and eGRAS credentials from Windows Credential Manager?",
            parent=self.root,
        ):
            return
        try:
            self.credential_store.clear()
        except (OSError, RuntimeError) as error:
            messagebox.showerror("Clear saved credentials", str(error), parent=self.root)
            return
        self.credentials_saved = False
        self.citizen_user_var.set("")
        self.citizen_password_var.set("")
        self.egras_user_var.set("")
        self.egras_password_var.set("")
        self.credentials_status_var.set("Saved credentials cleared.")
        self.clear_credentials_button.pack_forget()
        self.controller.record_activity("credentials_cleared", "Saved portal credentials removed.")

    def _start(self) -> None:
        self._record_ui_action("start_clicked")
        csv_path = Path(self.csv_var.get().strip())
        if not csv_path.is_file():
            messagebox.showerror("CSV required", "Choose an existing CSV batch file.", parent=self.root)
            return
        article = self.article_var.get().strip()
        if not article:
            messagebox.showwarning(
                "Article required",
                "Choose or type the Article for this batch.",
                parent=self.root,
            )
            return
        portal_browser = self.portal_browsers.get(self.portal_browser_var.get())
        if portal_browser is None:
            messagebox.showerror(
                "Portal browser required",
                "Select an installed portal browser. Use Refresh if you installed one after opening the app.",
                parent=self.root,
            )
            return
        download_text = self.download_var.get().strip()
        self._save_non_secret_settings()
        options = RunOptions(
            csv_path=csv_path,
            download_root=Path(download_text) if download_text else None,
            article=article,
            portal_browser=portal_browser,
            mode=RunMode(self.mode_var.get()),
            credentials=self._entered_credentials(),
            ocr_enabled=self.gemini_ready,
            sms_user_id=self.sms_user_id_var.get().strip(),
            sms_server_url=self.sms_server_url_var.get().strip() or DEFAULT_SMS_SERVER_URL,
        )
        self.starting = True
        self.run_status_var.set(f"Opening {portal_browser.name}...")
        self._set_run_buttons()
        self.controller.start(options)

    def _pause(self) -> None:
        self._record_ui_action("pause_clicked")
        self.paused = True
        self.auto_waiting = False
        self._set_run_buttons()
        self.controller.pause()

    def _resume(self) -> None:
        self._record_ui_action("resume_clicked")
        self.paused = False
        self.auto_waiting = False
        self._set_run_buttons()
        self.controller.resume()

    def _stop(self) -> None:
        self._record_ui_action("stop_clicked")
        self.controller.stop()

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.controller.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_events)

    def _handle_event(self, event: UiEvent) -> None:
        if event.message:
            self._append_session_log(event.message)
        # Login required setup event commented out for non-headless mode:
        # if event.kind == "gemini_login_required":
        #     self.gemini_checking = False
        #     self.gemini_ready = False
        #     self.gemini_login_required = True
        #     self.gemini_login_browser_open = False
        #     self.config.gemini_verified = False
        #     self.config_store.save(self.config)
        #     self.gemini_status_var.set("Gemini OCR: Google sign-in required")
        #     self.captcha_warning_var.set(
        #         "CAPTCHA warning: Gemini OCR is inactive. CAPTCHAs must be entered manually."
        #     )
        if event.kind == "gemini_verified":
            self.gemini_checking = False
            self.gemini_ready = True
            # self.gemini_login_required = False
            # self.gemini_login_browser_open = False
            self.config.gemini_verified = True
            self.config_store.save(self.config)
            self.gemini_status_var.set("Gemini OCR: active")
            # self.gemini_status_var.set("Gemini OCR: active (headless)")  # Headless mode
            self.captcha_warning_var.set("")
        elif event.kind == "gemini_stopped":
            self.gemini_checking = False
            self.gemini_ready = False
            # self.gemini_login_required = False
            # self.gemini_login_browser_open = False
            self.gemini_status_var.set("Gemini OCR: stopped")
            self.captcha_warning_var.set(
                "CAPTCHA warning: Gemini OCR is inactive. CAPTCHAs must be entered manually."
            )
        elif event.kind in {"gemini_not_ready", "fatal_error"}:
            if event.kind == "gemini_not_ready":
                self.gemini_checking = False
                self.gemini_ready = False
                # self.gemini_login_required = False
                # self.gemini_login_browser_open = False
                self.config.gemini_verified = False
                self.config_store.save(self.config)
                self.gemini_status_var.set("Gemini OCR: sign-in required / not ready")
                self.captcha_warning_var.set(
                    "CAPTCHA warning: Gemini OCR is inactive. CAPTCHAs must be entered manually."
                )
            else:
                self.gemini_checking = False
                self.gemini_ready = False
                # self.gemini_login_browser_open = False
                self.starting = False
                self.running = False
                self.run_status_var.set("Stopped with an error")
                show_windows_notification(
                    "eStamp Automation error", "An error occurred. Please look into the application."
                )
                messagebox.showerror("Automation error", event.message, parent=self.root)
        elif event.kind == "run_started":
            self.starting = False
            self.running = True
            self.portal_session_open = True
            self.paused = False
            self.auto_waiting = False
            self.run_status_var.set("Running")
        elif event.kind == "stage":
            self.run_status_var.set(f"Running: {event.message.replace('_', ' ').title()}")
        elif event.kind == "status":
            self.run_status_var.set(event.message)
        elif event.kind in {"manual_checkpoint", "paused"}:
            self.paused = True
            self.auto_waiting = bool(event.data.get("auto_continue"))
            self.run_status_var.set("Waiting for user")
        elif event.kind == "persistence_blocked":
            self.paused = False
            self.auto_waiting = False
            self.run_status_var.set("Waiting for CSV file access")
            messagebox.showwarning("CSV is locked", event.message, parent=self.root)
        elif event.kind == "resumed":
            self.paused = False
            self.auto_waiting = False
            self.run_status_var.set("Running")
        elif event.kind in {"run_completed", "run_stopped", "browser_closed", "portal_closed"}:
            self.starting = False
            self.running = False
            self.paused = False
            self.auto_waiting = False
            if event.kind == "run_completed":
                self.portal_session_open = True
                self.run_status_var.set("Completed — portal ready for another CSV")
            else:
                self.portal_session_open = False
                self.run_status_var.set("Stopped")
            if event.kind == "browser_closed":
                if event.data.get("profile_browser"):
                    self.gemini_ready = False
                    # self.gemini_login_required = False
                    # self.gemini_login_browser_open = False
                    self.config.gemini_verified = False
                    self.config_store.save(self.config)
                    self.gemini_status_var.set(
                        "Gemini OCR: browser closed. Click Start OCR browser to reopen."
                    )
                    self.captcha_warning_var.set(
                        "CAPTCHA warning: Gemini OCR is inactive. CAPTCHAs must be entered manually."
                    )
                if not event.data.get("profile_browser"):
                    messagebox.showwarning("Browser closed", event.message, parent=self.root)
        # Login browser page opened event commented out:
        # elif event.kind == "gemini_login_browser_opened":
        #     self.gemini_checking = False
        #     self.gemini_ready = False
        #     self.gemini_login_required = True
        #     self.gemini_login_browser_open = True
        #     self.gemini_status_var.set("Gemini OCR: Chrome profile open for sign-in")
        elif event.kind == "batch_update":
            self._render_rows(event.data.get("rows", []), event.data.get("current_row"))
        elif event.kind == "error_prompt":
            self._show_error_dialog(event)
        elif event.kind == "notification":
            show_windows_notification(event.data.get("title", "eStamp Automation"), event.message)
        self._set_run_buttons()

    def _show_error_dialog(self, event: UiEvent) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Row error")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=f"Row {event.data.get('row')} failed at {event.data.get('stage', 'unknown')}.",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")
        ttk.Label(frame, text=event.message, wraplength=560).pack(anchor="w", pady=(8, 0))
        if event.data.get("post_payment_warning"):
            ttk.Label(
                frame,
                text="Warning: retrying after payment began can cause a duplicate charge.",
                foreground="#b00020",
                wraplength=560,
            ).pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(16, 0))

        def choose(action: str) -> None:
            self._record_ui_action(f"error_dialog_{action}_clicked")
            dialog.destroy()
            self.controller.decide_error(action)

        next_button = ttk.Button(buttons, text="Move to Next Row", command=lambda: choose("next"))
        next_button.pack(side="right")
        ttk.Button(buttons, text="Retry Current Row", command=lambda: choose("retry")).pack(
            side="right", padx=(0, 8)
        )
        dialog.protocol("WM_DELETE_WINDOW", lambda: choose("next"))
        next_button.focus_set()
        dialog.bind("<Return>", lambda _event: choose("next"))

    def _load_preview(self, path: Path, quiet: bool = False) -> None:
        try:
            store = CsvBatchStore(path)
            store.load()
            summaries = store.summaries()
            issues: list[str] = []
            for index, row in enumerate(store.rows):
                errors = store.validate_row(row)
                if errors:
                    message = "; ".join(errors)
                    summaries[index]["error"] = message
                    issues.append(f"Row {index + 1}: {message}")
            self.csv_valid = not issues
            self._render_rows(summaries)
            if issues and not quiet:
                preview = "\n".join(issues[:8])
                remaining = len(issues) - 8
                suffix = f"\n...and {remaining} more row(s)." if remaining else ""
                messagebox.showwarning(
                    "CSV needs correction",
                    "Fix these row(s) and select the CSV again before starting:\n\n" + preview + suffix,
                    parent=self.root,
                )
        except Exception as error:
            self.csv_valid = False
            self._render_rows([])
            if not quiet:
                messagebox.showerror("CSV error", str(error), parent=self.root)

    def _render_rows(self, rows: list[dict[str, str]], current_row: int | None = None) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
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
                    row.get("completed", "0"),
                    row.get("error", ""),
                ),
            )

    def _append_session_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.session_log_lines.append(f"{timestamp}  {message.strip()}")

    def _record_ui_action(self, action: str) -> None:
        self.controller.record_activity(action)
        self._append_session_log(action.replace("_", " ").title())

    def _show_session_log(self) -> None:
        self._record_ui_action("view_current_session_log_clicked")
        dialog = tk.Toplevel(self.root)
        dialog.title("Current Session Activity")
        dialog.transient(self.root)
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

    def _set_run_buttons(self) -> None:
        if self.gemini_ready:
            self.ocr_browser_button.configure(text="Close OCR browser", command=self._close_ocr_browser)
        # elif self.gemini_login_browser_open:
        #     self.ocr_browser_button.configure(text="Close OCR browser", command=self._close_ocr_browser)
        # elif self.gemini_login_required:
        #     self.ocr_browser_button.configure(
        #         text="Start OCR browser", command=self._open_ocr_login_browser
        #     )
        else:
            self.ocr_browser_button.configure(text="Start OCR browser", command=self._verify_gemini)
        has_csv = self.csv_valid and Path(self.csv_var.get().strip()).is_file()
        has_portal_browser = self.portal_browser_var.get() in self.portal_browsers
        self.start_button.configure(
            state=(
                "normal"
                if has_csv
                and has_portal_browser
                and not self.running
                and not self.starting
                else "disabled"
            )
        )
        self.pause_button.pack_forget()
        self.resume_button.pack_forget()
        if self.running and not self.auto_waiting:
            active_button = self.resume_button if self.paused else self.pause_button
            active_button.configure(state="normal")
            active_button.pack(side="left")
        self.stop_button.configure(
            state="normal" if self.running or self.portal_session_open else "disabled"
        )

    def _on_close(self) -> None:
        if (self.running or self.starting) and not messagebox.askyesno(
            "Exit application",
            "Automation is active. Stop it, close both automation browsers, and exit?",
            parent=self.root,
            default=messagebox.NO,
        ):
            return
        self.controller.record_activity("application_closed", "Desktop application closed.")
        self.controller.shutdown()
        self.citizen_user_var.set("")
        self.citizen_password_var.set("")
        self.egras_user_var.set("")
        self.egras_password_var.set("")
        self.root.destroy()
