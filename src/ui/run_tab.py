from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from core.config import DEFAULT_SMS_SERVER_URL, TabConfig
from core.models import Credentials, OcrEngine, PortalBrowser, RunMode, RunOptions, Stage, UiEvent
from services.config_package import TabExportData
from services.credential_store import credential_store_label
from services.csv_store import CsvBatchStore
from services.windows_notifications import show_windows_notification

if TYPE_CHECKING:
    from ui.main_window import MainWindow


CUSTOM_BROWSER_OPTION = "Choose custom browser..."


class AutomationTab:
    """Runtime state for one ID. The main window owns all visible batch controls."""

    def __init__(self, owner: MainWindow, parent: tk.Misc, config: TabConfig) -> None:
        self.owner = owner
        self.config = config
        self.run_id = str(config.tab_id)
        self.frame = ttk.Frame(parent)
        self.running = False
        self.starting = False
        self.paused = False
        self.auto_waiting = False
        self.portal_session_open = False
        self.browser_recovery_pending = False
        self.browser_recovery_ready = False
        self.current_row_number: int | None = None
        self.current_unit_number: int | None = None
        self.error_window: tk.Toplevel | None = None

        try:
            saved = owner.credential_store.load(config.tab_id)
        except (OSError, RuntimeError, ValueError):
            saved = None
        self.citizen_user_var = tk.StringVar(value=saved.citizen_username if saved else "")
        self.citizen_password_var = tk.StringVar(value=saved.citizen_password if saved else "")
        self.egras_user_var = tk.StringVar(value=saved.egras_username if saved else "")
        self.egras_password_var = tk.StringVar(value=saved.egras_password if saved else "")
        self.credentials_saved = saved is not None
        self.credentials_status_var = tk.StringVar(
            value=f"saved in {credential_store_label()}" if saved else ""
        )
        self.sms_user_id_var = tk.StringVar(value=config.sms_user_id)
        self.browser_count_var = tk.IntVar(value=min(20, max(1, config.browser_count)))
        self.profile_var = tk.StringVar(value=config.portal_profile_path)
        self.run_status_var = tk.StringVar(value="Idle" if config.enabled else "Disabled")

        # Compatibility aliases used by dialogs and services that previously read a tab.
        self.csv_var = owner.csv_var
        self.download_var = owner.download_var
        self.article_var = owner.article_var
        self.sms_server_url_var = owner.sms_server_url_var
        self.payment_trigger_url_var = owner.payment_trigger_url_var
        self.payment_trigger_method_var = owner.payment_trigger_method_var
        self.mode_var = owner.mode_var
        self.ocr_enabled_var = owner.ocr_enabled_var
        self.ocr_engine_var = owner.ocr_engine_var
        self.captcha_copy_mode_var = owner.captcha_copy_mode_var
        self.save_captcha_images_var = owner.save_captcha_images_var
        self.fresh_browser_var = owner.fresh_browser_var
        self.retry_egras_otp_once_var = owner.retry_egras_otp_once_var
        self.portal_browser_var = owner.portal_browser_var

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

    def entered_credentials(self) -> Credentials:
        return Credentials(
            self.citizen_user_var.get().strip(),
            self.citizen_password_var.get(),
            self.egras_user_var.get().strip(),
            self.egras_password_var.get(),
        )

    def _selected_browser(self) -> PortalBrowser | None:
        return self.owner.selected_portal_browser()

    def save_id_settings(self) -> None:
        self.config.sms_user_id = self.sms_user_id_var.get().strip()
        try:
            self.config.browser_count = min(20, max(1, int(self.browser_count_var.get())))
        except (tk.TclError, ValueError):
            self.config.browser_count = 1
        self.owner.save_config()

    def save_credentials(self) -> str:
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
            return f"Enter both fields for: {', '.join(incomplete)}."
        if not any(
            (
                credentials.citizen_username,
                credentials.citizen_password,
                credentials.egras_username,
                credentials.egras_password,
            )
        ):
            self.owner.credential_store.clear(self.tab_id)
            self.credentials_saved = False
            self.credentials_status_var.set("")
            return ""
        self.owner.credential_store.save(credentials, self.tab_id)
        self.credentials_saved = True
        self.credentials_status_var.set(f"saved in {credential_store_label()}")
        return ""

    def clear_credentials(self) -> None:
        self.owner.credential_store.clear(self.tab_id)
        for variable in (
            self.citizen_user_var,
            self.citizen_password_var,
            self.egras_user_var,
            self.egras_password_var,
        ):
            variable.set("")
        self.credentials_saved = False
        self.credentials_status_var.set("")

    def validation_error(self) -> str:
        error = self.owner.global_validation_error()
        if error:
            return error
        try:
            count = int(self.browser_count_var.get())
        except (tk.TclError, ValueError):
            return "Browser count must be a whole number from 1 to 20."
        if not 1 <= count <= 20:
            return "Browser count must be from 1 to 20."
        return ""

    def start(self, *, show_errors: bool = True) -> bool:
        if not self.is_enabled:
            self.set_state("Disabled", "Skipped by user")
            return False
        if self.is_active or self.portal_session_open:
            return False
        if self.browser_recovery_pending and not self.browser_recovery_ready:
            return False
        self.current_row_number = None
        self.current_unit_number = None
        self.owner.load_batch_preview(quiet=True)
        error = self.validation_error()
        if error:
            self.set_state("Needs setup", error)
            if show_errors:
                messagebox.showwarning(f"{self.display_name} needs setup", error, parent=self.owner.root)
            return False
        browser = self.owner.selected_portal_browser()
        if browser is None:
            return False
        self.save_id_settings()
        run_config = self.owner.config.run_config
        profile_slug = re.sub(r"[^a-z0-9]+", "-", browser.name.casefold()).strip("-") or "browser"
        profile_path = Path(self.config.portal_profile_path) / browser.engine.value / profile_slug
        credentials = self.entered_credentials()
        citizen_label = credentials.citizen_username or "not set"
        options = RunOptions(
            csv_path=Path(run_config.last_csv_path),
            download_root=Path(run_config.last_download_path) if run_config.last_download_path else None,
            article=run_config.last_article,
            portal_browser=browser,
            mode=RunMode(run_config.last_mode),
            credentials=credentials,
            ocr_enabled=run_config.ocr_enabled,
            ocr_engine=OcrEngine(run_config.ocr_engine),
            sms_user_id=self.config.sms_user_id,
            sms_server_url=run_config.sms_server_url or DEFAULT_SMS_SERVER_URL,
            payment_trigger_url=run_config.payment_trigger_url,
            payment_trigger_method=run_config.payment_trigger_method,
            captcha_copy_mode=self.owner.selected_captcha_copy_mode(),
            save_captcha_images=run_config.save_captcha_images,
            fresh_browser_per_unit=run_config.fresh_browser_per_unit,
            retry_egras_otp_once=run_config.retry_egras_otp_once,
            run_id=self.run_id,
            portal_profile_path=profile_path,
            portal_window_accent=self.owner.tab_accent_color(self.tab_id),
            portal_window_label=f"{self.display_name} | {citizen_label}",
            browser_count=self.config.browser_count,
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
        count = self.config.browser_count
        label = "browser" if count == 1 else "browsers"
        self.set_state("Starting", f"Opening {count} {browser.name} {label}")
        return True

    def pause(self) -> None:
        self.owner.controller.pause(self.run_id)

    def resume(self) -> None:
        self.owner.controller.resume(self.run_id)

    def stop(self) -> None:
        self.owner.controller.stop(self.run_id)

    def toggle_enabled(self) -> None:
        if self.is_active or self.portal_session_open:
            return
        self.config.enabled = not self.config.enabled
        self.owner.save_config()
        self.set_state("Idle" if self.is_enabled else "Disabled")

    def handle_event(self, event: UiEvent) -> None:
        try:
            browser_count = int(event.data.get("browser_count", 1))
        except (TypeError, ValueError):
            browser_count = 1
        worker_label = str(event.data.get("worker_label", "") or "").strip()
        dock_id = str(event.data.get("dock_id", "") or "").strip() or None
        log_prefix = (
            f"{self.display_name} {worker_label}" if browser_count > 1 and worker_label else self.display_name
        )
        if event.message:
            self.owner.append_session_log(f"{log_prefix}: {event.message}")
        kind = event.kind
        group_event = kind in {
            "run_started",
            "run_completed",
            "run_stopped",
            "portal_closed",
            "fatal_error",
            "session_finished",
        }
        target = None if group_event else dock_id
        if kind == "run_started":
            self.browser_recovery_pending = False
            self.browser_recovery_ready = False
            self.starting = False
            self.running = True
            self.portal_session_open = True
            self.paused = False
            self.set_state("Running", event.message, dock_id=target)
        elif kind in {"stage", "status"}:
            detail = event.message.replace("_", " ").title() if kind == "stage" else event.message
            self.set_state("Running", detail, dock_id=target)
        elif kind in {"manual_checkpoint", "paused"}:
            self.paused = True
            self.auto_waiting = bool(event.data.get("auto_continue"))
            self.set_state("Paused", event.message, dock_id=target)
        elif kind == "resumed":
            self.paused = False
            self.auto_waiting = False
            self.set_state("Running", event.message, dock_id=target)
        elif kind == "payment_state":
            state = str(event.data.get("state", ""))
            self.set_state(
                "Payment" if state in {"pay_now_ready", "qr_ready"} else "Running",
                event.message,
                dock_id=target,
            )
        elif kind == "batch_update":
            row = event.data.get("current_row")
            unit = event.data.get("current_unit")
            if isinstance(row, int):
                self.current_row_number = row
            if isinstance(unit, int):
                self.current_unit_number = unit
            self.owner.render_batch_rows(event.data.get("rows", []))
        elif kind == "error_prompt":
            self.paused = True
            self.auto_waiting = True
            if not self.owner.show_tab_error_in_dock(self, event):
                self._show_error(event)
            self.set_state("Error", event.message, dock_id=target)
        elif kind == "notification":
            title = (
                f"{self.display_name} {worker_label}"
                if browser_count > 1 and worker_label
                else event.data.get("title", self.display_name)
            )
            show_windows_notification(str(title), event.message)
        elif kind in {"worker_stopped", "worker_finished"}:
            return
        elif kind == "browser_closed":
            if browser_count > 1 and dock_id:
                self.set_state("Browser closed", event.message, dock_id=dock_id)
                return
            offer = self.owner.should_offer_browser_recovery(self)
            self.starting = self.running = self.paused = self.auto_waiting = self.portal_session_open = False
            self.browser_recovery_pending = offer
            self.browser_recovery_ready = False
            if offer:
                message = "This browser was closed. Finishing cleanup before it can restart..."
                self.set_state("Browser closed", message, dock_id=None)
                self.owner.show_browser_recovery(self, message, ready=False, dock_id=None)
            else:
                self.set_state("Stopped", event.message, dock_id=None)
        elif kind == "session_finished":
            if self.browser_recovery_pending:
                self.browser_recovery_ready = True
                message = "Retry this row, skip it and open the next row, or stop this ID."
                self.set_state("Browser closed", message, dock_id=None)
                self.owner.show_browser_recovery(self, message, ready=True, dock_id=None)
        elif kind in {"run_completed", "run_stopped", "portal_closed", "fatal_error"}:
            browser_closed = kind == "run_stopped" and bool(event.data.get("browser_closed"))
            if browser_closed and not self.browser_recovery_pending:
                self.browser_recovery_pending = self.owner.should_offer_browser_recovery(self)
                self.browser_recovery_ready = False
            self.starting = self.running = self.paused = self.auto_waiting = self.portal_session_open = False
            if kind == "run_stopped" and self.browser_recovery_pending:
                message = "This browser was closed. Finishing cleanup before it can restart..."
                self.set_state("Browser closed", message, dock_id=None)
                self.owner.show_browser_recovery(self, message, ready=False, dock_id=None)
            else:
                self.browser_recovery_pending = False
                self.browser_recovery_ready = False
                state = (
                    "Complete" if kind == "run_completed" else "Error" if kind == "fatal_error" else "Stopped"
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
        self.paused = self.auto_waiting = False
        self.owner.controller.decide_error(self.run_id, action)
        self.set_state("Running", "Applying error decision")

    def decide_error(self, action: str) -> None:
        self._error_choice(action)

    def recover_browser(self, action: str) -> None:
        if not self.browser_recovery_pending or not self.browser_recovery_ready:
            return
        if action == "next" and not self._skip_browser_closed_row():
            return
        self.browser_recovery_pending = self.browser_recovery_ready = False
        if not self.start():
            self.browser_recovery_pending = self.browser_recovery_ready = True

    def dismiss_browser_recovery(self) -> None:
        self.browser_recovery_pending = self.browser_recovery_ready = False
        self.set_state("Stopped", "Browser recovery dismissed for this ID.")

    def _skip_browser_closed_row(self) -> bool:
        try:
            store = CsvBatchStore(Path(self.owner.csv_var.get().strip()))
            store.load()
            pending = list(store.pending_rows())
            if not pending:
                return True
            _index, row = pending[0]
            if self.current_row_number is not None and 0 < self.current_row_number <= len(store.rows):
                candidate = store.rows[self.current_row_number - 1]
                if store.pending_quantity_numbers(candidate):
                    row = candidate
            try:
                stage = Stage(row.get("last_stage", ""))
            except ValueError:
                stage = Stage.IDLE
            store.mark_skipped_row(row, stage, "Browser was closed; row skipped from the status dock.")
            store.persist()
            self.owner.load_batch_preview(quiet=True)
            return True
        except Exception as error:
            messagebox.showerror("Skip row", str(error), parent=self.owner.root)
            return False

    def set_state(self, state: str, detail: str = "", dock_id: str | None = None) -> None:
        self.run_status_var.set(f"{state}: {detail}" if detail else state)
        self.owner.update_tab_state(self, state, detail, dock_id=dock_id)

    def _set_buttons(self) -> None:
        owner = getattr(self, "owner", None)
        if owner is not None:
            owner.refresh_id_strip()
            owner.update_configuration_lock()
        # Preserve compatibility with tests and optional external callers.
        if hasattr(self, "start_button"):
            blocked = (
                self.is_active
                or self.portal_session_open
                or not self.is_enabled
                or (self.browser_recovery_pending and not self.browser_recovery_ready)
            )
            self.start_button.configure(state="disabled" if blocked else "normal")
        if hasattr(self, "pause_button"):
            self.pause_button.configure(state="normal" if self.running and not self.paused else "disabled")
        if hasattr(self, "resume_button"):
            self.resume_button.configure(state="normal" if self.running and self.paused else "disabled")
        if hasattr(self, "stop_button"):
            active = self.running or self.starting or self.portal_session_open
            self.stop_button.configure(state="normal" if active else "disabled")
        if hasattr(self, "toggle_enabled_button"):
            self.toggle_enabled_button.configure(
                text="Disable ID" if self.is_enabled else "Enable ID",
                state="disabled" if self.is_active or self.portal_session_open else "normal",
            )
        if hasattr(self, "delete_profile_button"):
            self.delete_profile_button.configure(
                state="disabled" if self.is_active or self.portal_session_open else "normal"
            )

    def delete_portal_profile(self) -> str:
        profile_path, result = self.config.reset_portal_profile()
        self.profile_var.set(str(profile_path))
        self.owner.save_config()
        if result == "deleted":
            return "Portal browser profile deleted. It will be recreated when this ID starts."
        if result == "path_reset":
            return "Portal profile path reset to this ID's local profile path."
        return "No saved portal browser profile folder was found."

    def export_data(self) -> TabExportData:
        self.save_id_settings()
        credentials = self.entered_credentials()
        return TabExportData(
            tab_config=self.config.to_dict(),
            credentials={
                "citizen_username": credentials.citizen_username,
                "citizen_password": credentials.citizen_password,
                "egras_username": credentials.egras_username,
                "egras_password": credentials.egras_password,
            },
            credentials_saved=self.credentials_saved,
        )

    def refresh_from_config(self) -> None:
        try:
            saved = self.owner.credential_store.load(self.tab_id)
        except Exception:
            saved = None
        self.citizen_user_var.set(saved.citizen_username if saved else "")
        self.citizen_password_var.set(saved.citizen_password if saved else "")
        self.egras_user_var.set(saved.egras_username if saved else "")
        self.egras_password_var.set(saved.egras_password if saved else "")
        self.credentials_saved = saved is not None
        self.sms_user_id_var.set(self.config.sms_user_id)
        self.browser_count_var.set(self.config.browser_count)
        self.profile_var.set(self.config.portal_profile_path)
        self._set_buttons()

    def destroy(self) -> None:
        if self.error_window is not None and self.error_window.winfo_exists():
            self.error_window.destroy()
        self.frame.destroy()
