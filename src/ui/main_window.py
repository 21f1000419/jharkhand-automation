from __future__ import annotations

import queue
import subprocess
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.browser_detection import detect_supported_browsers
from core.config import AppConfig, ConfigStore
from core.controller import AutomationController
from core.form_options import ARTICLE_OPTIONS
from core.models import BrowserEngine, Credentials, PortalBrowser, RunMode, RunOptions, UiEvent
from services.credential_store import WindowsCredentialStore
from services.csv_store import CsvBatchStore
from services.windows_notifications import show_windows_notification


def walk_widgets(widget: tk.Misc) -> list[tk.Misc]:
    result: list[tk.Misc] = []
    for child in widget.winfo_children():
        result.append(child)
        result.extend(walk_widgets(child))
    return result


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
        self.running = False
        self.starting = False
        self.csv_valid = False
        self.credential_store = WindowsCredentialStore()
        try:
            saved_credentials = self.credential_store.load()
        except (OSError, RuntimeError, ValueError):
            saved_credentials = None

        self.chrome_var = tk.StringVar(value=config.chrome_executable)
        self.profile_var = tk.StringVar(value=str(config.profile_path))
        self.csv_var = tk.StringVar()
        self.download_var = tk.StringVar(value=config.last_download_path)
        self.article_var = tk.StringVar()
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
        self.otp_auto_fill_var = tk.BooleanVar(value=config.otp_auto_fill)
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
        self.gemini_dialog_status_var = tk.StringVar(value=self.gemini_status_var.get())
        self.setup_status_var = self.gemini_dialog_status_var
        self.run_status_var = tk.StringVar(value="Idle")
        self.session_log_lines: list[str] = []
        self.gemini_dialog: tk.Toplevel | None = None
        self.portal_browsers: dict[str, PortalBrowser] = {}
        self._detect_portal_browsers()

        self._build_menu()
        self._build()
        self._update_custom_engine_control()
        self._set_run_buttons()
        self.controller.record_activity("application_started", "Desktop application opened.")
        self.root.after(100, self._drain_events)
        self.root.after(200, self._initial_gemini_check)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        self.root.title("Compitcom eStamp Batch Automation")
        self.root.geometry("1180x820")
        self.root.minsize(980, 700)

        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Heading.TLabel", font=("Segoe UI", 16, "bold"))  # type: ignore[no-untyped-call]
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))  # type: ignore[no-untyped-call]

        container = ttk.Frame(self.root, padding=12)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="eStamp Batch Automation", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            container,
            text=(
                "Visible browser automation for Jharkhand NGDRS/eGRAS. "
                "Payment remains manual; eGRAS OTP can optionally auto-fill from a paired phone."
            ),
        ).pack(anchor="w", pady=(0, 10))

        gemini_status = ttk.Frame(container)
        gemini_status.pack(fill="x", pady=(0, 8))
        ttk.Label(gemini_status, textvariable=self.gemini_status_var).pack(side="left")
        ttk.Button(gemini_status, text="Open profile", command=self._open_gemini).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(gemini_status, text="Browser profile status...", command=self._show_gemini_setup).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(gemini_status, text="OTP phone...", command=self._show_otp_phone_setup).pack(
            side="left", padx=(8, 0)
        )

        setup = ttk.LabelFrame(container, text="1. Chrome and Gemini setup", padding=10)
        setup.pack(fill="x", pady=(0, 8))
        setup.columnconfigure(1, weight=1)
        ttk.Label(setup, text="Chrome executable").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(setup, textvariable=self.chrome_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(setup, text="Browse…", command=self._browse_chrome).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(setup, text="Dedicated profile").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
        ttk.Entry(setup, textvariable=self.profile_var, state="readonly").grid(
            row=1, column=1, sticky="ew", pady=(6, 0)
        )
        setup_buttons = ttk.Frame(setup)
        setup_buttons.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(setup_buttons, text="Open Gemini Setup", command=self._open_gemini).pack(side="left")
        ttk.Button(setup_buttons, text="Verify Gemini", command=self._verify_gemini).pack(side="left", padx=8)
        ttk.Label(setup_buttons, textvariable=self.setup_status_var).pack(side="left", padx=8)
        setup.pack_forget()

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
        ttk.Label(
            batch,
            text="Choose a listed article or type one. The portal validates it before every submission.",
            foreground="#555555",
            wraplength=500,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Label(batch, text="Filled batch CSV").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(batch, textvariable=self.csv_var).grid(row=5, column=1, sticky="ew", pady=(8, 0))
        ttk.Button(batch, text="Select CSV…", command=self._browse_csv).grid(
            row=5, column=2, padx=(8, 0), pady=(8, 0)
        )
        ttk.Button(batch, text="Download CSV Format", command=self._download_template).grid(
            row=6, column=1, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            batch,
            text="eStamp download folder",
        ).grid(row=7, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.download_entry = ttk.Entry(batch, textvariable=self.download_var)
        self.download_entry.grid(row=7, column=1, sticky="ew", pady=(8, 0))
        self.download_entry.bind("<FocusOut>", self._save_non_secret_settings)
        ttk.Button(batch, text="Browse…", command=self._browse_download).grid(
            row=7, column=2, padx=(8, 0), pady=(8, 0)
        )
        ttk.Label(
            batch,
            text=(
                "Defaults to Windows Downloads. The app verifies and saves each PDF here, "
                "then records it in the CSV."
            ),
            foreground="#555555",
            wraplength=500,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(5, 0))
        modes = ttk.Frame(batch)
        modes.grid(row=9, column=0, columnspan=3, sticky="w", pady=(8, 0))
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
        ttk.Checkbutton(
            modes,
            text="Auto-fill eGRAS OTP from paired phone",
            variable=self.otp_auto_fill_var,
            command=self._save_non_secret_settings,
        ).pack(side="left", padx=(12, 0))

        controls = ttk.Frame(container)
        controls.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(controls, text="Start", command=self._start)
        self.pause_button = ttk.Button(controls, text="Pause", command=self._pause)
        self.resume_button = ttk.Button(controls, text="Resume", command=self._resume)
        self.stop_button = ttk.Button(controls, text="Stop", command=self._stop)
        self.start_button.pack(side="left")
        self.pause_button.pack(side="left", padx=(8, 0))
        self.resume_button.pack(side="left", padx=(8, 0))
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
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Download CSV Format…", command=self._download_template)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menu.add_cascade(label="File", menu=file_menu)

        activity_menu = tk.Menu(menu, tearoff=False)
        activity_menu.add_command(label="Current Session…", command=self._show_session_log)
        activity_menu.add_command(label="Open Daily Log Folder", command=self._open_log_folder)
        menu.add_cascade(label="Activity", menu=activity_menu)

        self.root.configure(menu=menu)

    def _initial_gemini_check(self) -> None:
        self.gemini_checking = True
        self.gemini_status_var.set("Browser profile: checking Google/Gemini login...")
        self.gemini_dialog_status_var.set(
            "Checking the existing Chrome profile for a signed-in Google account and usable Gemini chat..."
        )
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
        if not self._require_gemini():
            return
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
        self.config.otp_auto_fill = self.otp_auto_fill_var.get()
        browser = self.portal_browsers.get(self.portal_browser_var.get())
        if browser is not None:
            self.config.last_portal_browser_path = str(browser.executable)
        self.config_store.save(self.config)

    def _require_gemini(self) -> bool:
        if self.gemini_ready:
            return True
        if self.gemini_checking:
            self.gemini_dialog_status_var.set("Gemini is still being checked. Please wait a moment.")
        else:
            self.gemini_dialog_status_var.set(
                "A signed-in browser profile is required before batch operations can be used."
            )
        self._show_gemini_setup()
        return False

    def _browse_chrome(self) -> None:
        self._record_ui_action("browse_chrome_clicked")
        selected = filedialog.askopenfilename(
            title="Choose Google Chrome",
            filetypes=[("Chrome executable", "chrome.exe"), ("Executables", "*.exe")],
        )
        if selected:
            self.chrome_var.set(selected)
            self.gemini_ready = False
            self._save_browser_settings(reconfigure=True)
            self._set_run_buttons()

    def _browse_csv(self) -> None:
        if not self._require_gemini():
            return
        self._record_ui_action("select_csv_clicked")
        selected = filedialog.askopenfilename(title="Choose batch CSV", filetypes=[("CSV files", "*.csv")])
        if selected:
            self.csv_valid = False
            self.csv_var.set(selected)
            self._load_preview(Path(selected))
            self._set_run_buttons()

    def _browse_download(self) -> None:
        if not self._require_gemini():
            return
        self._record_ui_action("choose_download_folder_clicked")
        selected = filedialog.askdirectory(title="Choose eStamp download folder")
        if selected:
            self.download_var.set(selected)
            self._save_non_secret_settings()

    def _download_template(self) -> None:
        if not self._require_gemini():
            return
        self._record_ui_action("download_csv_format_clicked")
        selected = filedialog.asksaveasfilename(
            title="Save CSV format",
            defaultextension=".csv",
            initialfile="estamp_batch_template.csv",
            filetypes=[("CSV files", "*.csv")],
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

    def _show_gemini_setup(self) -> None:
        self._record_ui_action("gemini_status_clicked")
        if self.gemini_dialog is not None and self.gemini_dialog.winfo_exists():
            self.gemini_dialog.deiconify()
            self.gemini_dialog.lift()
            self.gemini_dialog.focus_set()
            return

        dialog = tk.Toplevel(self.root)
        self.gemini_dialog = dialog
        dialog.title("Browser Profile Setup")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Browser Profile Setup", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            frame,
            text=(
                "A Google account must be signed in to this dedicated Chrome profile so Gemini can solve "
                "CAPTCHAs. This is normally a one-time setup. Every Start checks the profile and Gemini chat."
            ),
            wraplength=560,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 12))
        ttk.Label(frame, text="Chrome executable").grid(row=2, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(frame, textvariable=self.chrome_var, width=58).grid(row=2, column=1, sticky="ew")
        ttk.Button(frame, text="Browse...", command=self._browse_chrome).grid(row=2, column=2, padx=(8, 0))
        ttk.Label(frame, text="Chrome profile").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(frame, textvariable=self.profile_var, state="readonly", width=58).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=(8, 0)
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Button(buttons, text="Open Profile at Gemini", command=self._open_gemini).pack(side="left")
        ttk.Button(buttons, text="Check Profile", command=self._verify_gemini).pack(side="left", padx=8)
        ttk.Button(buttons, text="Close", command=lambda: close_dialog()).pack(side="left")
        ttk.Label(frame, textvariable=self.gemini_dialog_status_var, wraplength=560).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(12, 0)
        )

        def close_dialog() -> None:
            self.gemini_dialog = None
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

    def _show_otp_phone_setup(self) -> None:
        self._record_ui_action("otp_phone_setup_clicked")
        dialog = tk.Toplevel(self.root)
        dialog.title("Pair Android OTP Reader")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Pair Android OTP Reader", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "Keep the phone and this PC on the same Wi-Fi. In the Android app, enter the "
                "server address and pairing token below, then enable SMS forwarding."
            ),
            wraplength=560,
        ).pack(anchor="w", pady=(8, 12))
        endpoint = self.controller.otp_receiver.server_address
        token = self.controller.otp_receiver.token
        for label, value in (("Server address", endpoint), ("Pairing token", token)):
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=15).pack(side="left")
            entry = ttk.Entry(row, width=58)
            entry.insert(0, value)
            entry.configure(state="readonly")
            entry.pack(side="left", fill="x", expand=True)

        def copy_details() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(f"Server address: {endpoint}\nPairing token: {token}")
            self._append_session_log("OTP phone pairing details copied to clipboard.")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Copy details", command=copy_details).pack(side="left")
        ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side="left", padx=8)
        ttk.Label(
            frame,
            text="The token changes when the desktop app restarts. OTPs are not saved or written to logs.",
            foreground="#555555",
            wraplength=560,
        ).pack(anchor="w", pady=(12, 0))

    def _open_gemini(self) -> None:
        self._record_ui_action("open_gemini_setup_clicked")
        if not self._save_browser_settings(reconfigure=False):
            return
        self.setup_status_var.set("Opening Chrome…")
        self.gemini_status_var.set("Browser profile: opening...")
        self.gemini_dialog_status_var.set("Opening the configured Chrome profile at Gemini...")
        self.controller.open_gemini_setup()

    def _verify_gemini(self) -> None:
        self._record_ui_action("verify_gemini_clicked")
        if not self._save_browser_settings(reconfigure=False):
            return
        self.setup_status_var.set("Checking Gemini…")
        self.gemini_status_var.set("Browser profile: checking...")
        self.gemini_dialog_status_var.set("Checking Google sign-in and whether Gemini chat accepts input...")
        self.controller.verify_gemini()

    def _save_browser_settings(self, reconfigure: bool) -> bool:
        chrome = Path(self.chrome_var.get().strip())
        if not chrome.is_file():
            messagebox.showerror("Chrome required", "Choose a valid chrome.exe file.", parent=self.root)
            return False
        changed = str(chrome) != self.config.chrome_executable
        self.config.chrome_executable = str(chrome)
        self.config.chrome_profile_path = self.profile_var.get().strip()
        self.config_store.save(self.config)
        if reconfigure or changed:
            self.controller.reconfigure_browser()
        return True

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
        if not self._require_gemini():
            return
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
            otp_auto_fill=self.otp_auto_fill_var.get(),
        )
        self.starting = True
        self.run_status_var.set(f"Checking profile; opening {portal_browser.name}...")
        self._set_run_buttons()
        self.controller.start(options)

    def _pause(self) -> None:
        self._record_ui_action("pause_clicked")
        self.controller.pause()

    def _resume(self) -> None:
        self._record_ui_action("resume_clicked")
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
        if event.kind == "gemini_setup_opened":
            self.gemini_checking = False
            self.gemini_dialog_status_var.set(event.message)
            self.gemini_status_var.set("Browser profile: setup in progress")
        elif event.kind == "gemini_verified":
            self.gemini_checking = False
            self.gemini_ready = True
            self.config.gemini_verified = True
            self.config_store.save(self.config)
            self.gemini_status_var.set("Browser profile: ready")
            self.gemini_dialog_status_var.set(event.message)
        elif event.kind in {"gemini_not_ready", "gemini_setup_required", "fatal_error"}:
            if event.kind == "gemini_not_ready":
                self.gemini_checking = False
                self.gemini_ready = False
                self.config.gemini_verified = False
                self.config_store.save(self.config)
                self.gemini_status_var.set("Browser profile: setup required")
                self.gemini_dialog_status_var.set(event.message)
                self._show_gemini_setup()
            elif event.kind == "gemini_setup_required":
                self.gemini_checking = False
                self.starting = False
                self.gemini_ready = False
                self.config.gemini_verified = False
                self.config_store.save(self.config)
                self.gemini_status_var.set("Browser profile: setup required")
                self.gemini_dialog_status_var.set(event.message)
                self.run_status_var.set("Browser profile setup required")
                self._show_gemini_setup()
            else:
                self.gemini_checking = False
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
            self.run_status_var.set("Running")
        elif event.kind == "stage":
            self.run_status_var.set(f"Running: {event.message.replace('_', ' ').title()}")
        elif event.kind in {"manual_checkpoint", "paused", "persistence_blocked"}:
            self.run_status_var.set("Waiting for user")
            if event.kind == "persistence_blocked":
                messagebox.showwarning("CSV is locked", event.message, parent=self.root)
        elif event.kind == "resumed":
            self.run_status_var.set("Running")
        elif event.kind in {"run_completed", "run_stopped", "browser_closed"}:
            self.starting = False
            self.running = False
            self.run_status_var.set("Completed" if event.kind == "run_completed" else "Stopped")
            if event.kind == "browser_closed":
                if event.data.get("profile_browser"):
                    self.gemini_ready = False
                    self.config.gemini_verified = False
                    self.config_store.save(self.config)
                    self.gemini_status_var.set("Browser profile: setup required")
                messagebox.showwarning("Browser closed", event.message, parent=self.root)
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

    def _load_preview(self, path: Path) -> None:
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
            if issues:
                preview = "\n".join(issues[:8])
                remaining = len(issues) - 8
                suffix = f"\n...and {remaining} more row(s)." if remaining else ""
                messagebox.showwarning(
                    "CSV needs correction",
                    "Fix these row(s) and select the CSV again before starting:\n\n" + preview + suffix,
                    parent=self.root,
                )
        except Exception as error:
            self._render_rows([])
            messagebox.showerror("CSV error", str(error), parent=self.root)
            self.csv_valid = False
            self._render_rows([])
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
        self._set_gemini_gated_controls()
        has_csv = self.csv_valid and Path(self.csv_var.get().strip()).is_file()
        has_portal_browser = self.portal_browser_var.get() in self.portal_browsers
        self.start_button.configure(
            state=(
                "normal"
                if self.gemini_ready
                and has_csv
                and has_portal_browser
                and not self.running
                and not self.starting
                else "disabled"
            )
        )
        self.pause_button.configure(state="normal" if self.running else "disabled")
        self.resume_button.configure(state="normal" if self.running else "disabled")
        self.stop_button.configure(state="normal" if self.running else "disabled")

    def _set_gemini_gated_controls(self) -> None:
        state = "normal" if self.gemini_ready else "disabled"
        for panel in (self.credentials_panel, self.batch_panel):
            for widget in walk_widgets(panel):
                try:
                    widget.configure(state=state)  # type: ignore[call-arg]
                except tk.TclError:
                    continue
        self.portal_browser_box.configure(state="readonly" if self.gemini_ready else "disabled")
        self.custom_portal_engine_box.configure(state="readonly" if self.gemini_ready else "disabled")

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
