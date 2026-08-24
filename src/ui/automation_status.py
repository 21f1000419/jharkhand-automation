"""Compact, always-visible controls for an active portal automation session."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk


class AutomationStatusWindow:
    """A small topmost window that keeps essential run information and actions visible."""

    def __init__(
        self,
        parent: tk.Tk,
        *,
        on_pause: Callable[[], None],
        on_resume: Callable[[], None],
        on_stop: Callable[[], None],
        on_error_decision: Callable[[str], None],
    ) -> None:
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("Automation status")
        self.window.attributes("-topmost", True)
        self.window.overrideredirect(True)
        self.window.resizable(False, False)
        self.window.configure(borderwidth=1, relief="solid")

        self.status_var = tk.StringVar(value="Starting")
        self.detail_var = tk.StringVar(value="Preparing the portal session…")
        self.progress_var = tk.StringVar(value="Row —")
        self.error_var = tk.StringVar()
        self.warning_var = tk.StringVar()
        self.checkpoint_title_var = tk.StringVar()
        self.checkpoint_instruction_var = tk.StringVar()
        self._on_error_decision = on_error_decision

        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(frame, textvariable=self.progress_var, foreground="#555555").grid(
            row=1, column=0, sticky="w", pady=(2, 0)
        )
        ttk.Label(frame, textvariable=self.detail_var, wraplength=430, justify="left").grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )

        self.error_frame = ttk.Frame(frame)
        ttk.Separator(self.error_frame).pack(fill="x", pady=(2, 8))
        ttk.Label(self.error_frame, text="Action needed", foreground="#b00020").pack(anchor="w")
        ttk.Label(
            self.error_frame,
            textvariable=self.error_var,
            foreground="#b00020",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(3, 0))

        self.checkpoint_frame = ttk.Frame(self.error_frame)
        ttk.Label(
            self.checkpoint_frame,
            textvariable=self.checkpoint_title_var,
            font=("Segoe UI", 9, "bold"),
            foreground="#0b57d0",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            self.checkpoint_frame,
            textvariable=self.checkpoint_instruction_var,
            foreground="#333333",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        ttk.Label(
            self.error_frame,
            textvariable=self.warning_var,
            foreground="#8a5700",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(3, 0))
        self.error_buttons = ttk.Frame(self.error_frame)
        self.continue_button = ttk.Button(
            self.error_buttons,
            text="Fixed error and continue",
            command=lambda: self._choose_error_action("continue"),
        )
        self.retry_button = ttk.Button(
            self.error_buttons, text="Retry", command=lambda: self._choose_error_action("retry")
        )
        self.next_button = ttk.Button(
            self.error_buttons, text="Move to next", command=lambda: self._choose_error_action("next")
        )
        self.continue_button.pack(side="left")
        self.retry_button.pack(side="left", padx=(8, 0))
        self.next_button.pack(side="left", padx=(8, 0))
        self.error_buttons.pack(anchor="w", pady=(8, 0))

        controls = ttk.Frame(frame)
        controls.grid(row=4, column=0, sticky="w", pady=(12, 0))
        self.pause_button = ttk.Button(controls, text="Pause", command=on_pause)
        self.resume_button = ttk.Button(controls, text="Resume", command=on_resume)
        self.stop_button = ttk.Button(controls, text="Stop", command=on_stop)
        self.pause_button.pack(side="left")
        self.resume_button.pack(side="left", padx=(8, 0))
        self.stop_button.pack(side="left", padx=(8, 0))

        self.set_controls(running=True, paused=False)
        self.window.deiconify()
        self.window.lift()
        self.window.after_idle(self._place_window)

    @property
    def exists(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def close(self) -> None:
        if self.exists:
            self.window.destroy()

    def set_status(self, status: str, detail: str) -> None:
        self.status_var.set(status)
        self.detail_var.set(detail)

    def set_progress(self, row: int | None, quantity: int | None = None) -> None:
        if row is None:
            return
        text = f"Row {row}"
        if quantity is not None:
            text += f" · Quantity {quantity}"
        self.progress_var.set(text)

    def set_controls(self, *, running: bool, paused: bool, auto_waiting: bool = False) -> None:
        self.pause_button.configure(state="normal" if running and not paused else "disabled")
        self.resume_button.configure(
            state="normal" if running and paused and not auto_waiting else "disabled"
        )
        self.stop_button.configure(state="normal" if running else "disabled")

    def show_error(
        self,
        *,
        message: str,
        row: int | None,
        quantity: int | None,
        stage: str,
        quantity_action: bool,
        post_payment_warning: bool,
        can_continue: bool = False,
        next_checkpoint_title: str = "",
        next_checkpoint_instruction: str = "",
    ) -> None:
        self.set_status("Action needed", f"{stage.replace('_', ' ').title()} could not finish.")
        self.set_progress(row, quantity if quantity_action else None)
        self.error_var.set(message)
        self.warning_var.set(
            "Retrying after payment began can create a duplicate charge." if post_payment_warning else ""
        )
        if can_continue and next_checkpoint_title:
            self.checkpoint_title_var.set(f"Next checkpoint: {next_checkpoint_title}")
            self.checkpoint_instruction_var.set(
                f"{next_checkpoint_instruction}\nOnce at the checkpoint page, click "
                "'Fixed error and continue'."
            )
            self.checkpoint_frame.pack(fill="x", pady=(4, 0))
            self.continue_button.pack(side="left", before=self.retry_button)
            self.continue_button.configure(state="normal")
        else:
            self.checkpoint_title_var.set("")
            self.checkpoint_instruction_var.set("")
            self.checkpoint_frame.pack_forget()
            self.continue_button.pack_forget()

        self.retry_button.configure(
            text="Retry quantity" if quantity_action else "Retry row", state="normal"
        )
        self.next_button.configure(
            text="Move to next quantity" if quantity_action else "Move to next row", state="normal"
        )
        self.error_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.set_controls(running=True, paused=True, auto_waiting=True)
        self.window.deiconify()
        self.window.lift()
        self.window.after_idle(self._place_window)

    def clear_error(self) -> None:
        self.error_frame.grid_remove()
        self.error_var.set("")
        self.warning_var.set("")
        self.checkpoint_title_var.set("")
        self.checkpoint_instruction_var.set("")
        self.window.after_idle(self._place_window)

    def _choose_error_action(self, action: str) -> None:
        self.continue_button.configure(state="disabled")
        self.retry_button.configure(state="disabled")
        self.next_button.configure(state="disabled")
        self._on_error_decision(action)


    def _place_window(self) -> None:
        self.parent.update_idletasks()
        self.window.update_idletasks()
        horizontal_inset = 8
        taskbar_clearance = 48
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        x = max(0, screen_width - width - horizontal_inset)
        y = max(0, screen_height - height - taskbar_clearance)
        self.window.geometry(f"+{x}+{y}")
