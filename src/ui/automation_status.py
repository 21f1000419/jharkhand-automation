"""Compact multi-ID status dock for active portal automation sessions."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Literal


class _RunStatusCard:
    def __init__(
        self,
        parent: tk.Misc,
        *,
        run_id: str,
        title: str,
        accent: str,
        on_pause: Callable[[str], None],
        on_resume: Callable[[str], None],
        on_stop: Callable[[str], None],
        on_error_decision: Callable[[str, str], None],
        on_browser_recovery: Callable[[str, str], None],
        on_layout_changed: Callable[[], None],
    ) -> None:
        self.run_id = run_id
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_stop = on_stop
        self.on_error_decision = on_error_decision
        self.on_browser_recovery = on_browser_recovery
        self.on_layout_changed = on_layout_changed
        self.paused = False
        self.browser_recovery = False
        self.browser_recovery_ready = False

        self.frame = tk.Frame(
            parent,
            background="#ffffff",
            highlightbackground="#d1d5db",
            highlightthickness=1,
        )
        tk.Frame(self.frame, width=6, background=accent).pack(side="left", fill="y")
        body = tk.Frame(self.frame, background="#ffffff", padx=10, pady=8)
        body.pack(side="left", fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        self.title_var = tk.StringVar(value=title)
        self.status_var = tk.StringVar(value="Starting")
        self.progress_var = tk.StringVar(value="Waiting for row")
        self.detail_var = tk.StringVar(value="Preparing this automation session...")
        self.error_var = tk.StringVar()
        self.warning_var = tk.StringVar()
        self.checkpoint_var = tk.StringVar()

        tk.Label(
            body,
            textvariable=self.title_var,
            background="#ffffff",
            foreground="#111827",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.status_label = tk.Label(
            body,
            textvariable=self.status_var,
            background="#ffffff",
            foreground="#2563eb",
            font=("Segoe UI", 9, "bold"),
        )
        self.status_label.grid(row=0, column=1, sticky="e", padx=(10, 0))
        tk.Label(
            body,
            textvariable=self.progress_var,
            background="#ffffff",
            foreground="#6b7280",
            font=("Segoe UI", 8),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        tk.Label(
            body,
            textvariable=self.detail_var,
            background="#ffffff",
            foreground="#374151",
            wraplength=390,
            justify="left",
            font=("Segoe UI", 9),
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        controls = tk.Frame(body, background="#ffffff")
        controls.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.pause_resume_button = tk.Button(
            controls,
            text="Pause",
            width=8,
            command=self._pause_or_resume,
        )
        self.pause_resume_button.pack(side="left")
        self.next_row_button = tk.Button(
            controls,
            text="Next row",
            width=9,
            command=lambda: self._choose_browser_recovery("next"),
        )
        self.stop_button = tk.Button(
            controls,
            text="Stop",
            width=7,
            command=lambda: self.on_stop(self.run_id),
        )
        self.stop_button.pack(side="left", padx=(6, 0))

        self.error_frame = tk.Frame(body, background="#fff7ed", padx=8, pady=7)
        tk.Label(
            self.error_frame,
            textvariable=self.error_var,
            background="#fff7ed",
            foreground="#b91c1c",
            wraplength=382,
            justify="left",
            font=("Segoe UI", 9),
        ).pack(anchor="w")
        self.checkpoint_label = tk.Label(
            self.error_frame,
            textvariable=self.checkpoint_var,
            background="#fff7ed",
            foreground="#1d4ed8",
            wraplength=382,
            justify="left",
            font=("Segoe UI", 8, "bold"),
        )
        self.warning_label = tk.Label(
            self.error_frame,
            textvariable=self.warning_var,
            background="#fff7ed",
            foreground="#92400e",
            wraplength=382,
            justify="left",
            font=("Segoe UI", 8),
        )
        self.error_buttons = tk.Frame(self.error_frame, background="#fff7ed")

    def begin(self, title: str, detail: str) -> None:
        self.title_var.set(title)
        self.progress_var.set("Waiting for row")
        self.clear_browser_recovery()
        self.clear_error()
        self.set_status("Starting", detail)

    def set_status(self, status: str, detail: str) -> None:
        self.status_var.set(status)
        if detail:
            self.detail_var.set(detail)
        lowered = status.casefold()
        color = (
            "#b91c1c"
            if "error" in lowered or "attention" in lowered or "closed" in lowered
            else "#2563eb"
        )
        if "pause" in lowered or "wait" in lowered or "queue" in lowered:
            color = "#b45309"
        elif "complete" in lowered:
            color = "#047857"
        elif "stop" in lowered:
            color = "#6b7280"
        self.status_label.configure(foreground=color)

    def set_progress(self, row: int | None, quantity: int | None = None) -> None:
        if row is None:
            return
        text = f"Row {row}"
        if quantity is not None:
            text += f"  |  Quantity {quantity}"
        self.progress_var.set(text)

    def set_controls(
        self,
        *,
        running: bool,
        starting: bool,
        paused: bool,
        auto_waiting: bool,
        portal_open: bool,
    ) -> None:
        if self.browser_recovery:
            recovery_state: Literal["normal", "disabled"] = (
                "normal" if self.browser_recovery_ready else "disabled"
            )
            self.pause_resume_button.configure(text="Retry row", state=recovery_state)
            self.next_row_button.configure(state=recovery_state)
            self.stop_button.configure(state="normal")
            return
        self.paused = paused
        self.pause_resume_button.configure(
            text="Resume" if paused else "Pause",
            state=(
                "normal"
                if running and ((paused and not auto_waiting) or not paused)
                else "disabled"
            ),
        )
        self.stop_button.configure(
            state="normal" if running or starting or portal_open else "disabled"
        )

    def show_browser_recovery(self, message: str, *, ready: bool) -> None:
        self.browser_recovery = True
        self.browser_recovery_ready = ready
        self.clear_error()
        self.set_status("Browser closed", message)
        self.pause_resume_button.configure(
            text="Retry row", state="normal" if ready else "disabled"
        )
        if not self.next_row_button.winfo_manager():
            self.next_row_button.pack(side="left", padx=(6, 0))
        self.next_row_button.configure(state="normal" if ready else "disabled")
        self.stop_button.configure(state="normal")
        self.on_layout_changed()

    def clear_browser_recovery(self) -> None:
        self.browser_recovery = False
        self.browser_recovery_ready = False
        self.next_row_button.pack_forget()

    def show_error(
        self,
        *,
        message: str,
        row: int | None,
        quantity: int | None,
        quantity_action: bool,
        post_payment_warning: bool,
        can_continue: bool,
        next_checkpoint_title: str,
        next_checkpoint_instruction: str,
    ) -> None:
        self.set_status("Action needed", "Choose how this ID should continue.")
        self.set_progress(row, quantity if quantity_action else None)
        self.error_var.set(message)
        self.warning_var.set(
            "Payment may already have started. Retrying can create a duplicate charge."
            if post_payment_warning
            else ""
        )
        checkpoint = ""
        if can_continue and next_checkpoint_title:
            checkpoint = f"Next checkpoint: {next_checkpoint_title}"
            if next_checkpoint_instruction:
                checkpoint += f"\n{next_checkpoint_instruction}"
        self.checkpoint_var.set(checkpoint)

        for child in self.error_buttons.winfo_children():
            child.destroy()
        if can_continue:
            tk.Button(
                self.error_buttons,
                text="Continue",
                command=lambda: self._choose_error_action("continue"),
            ).pack(side="left")
        tk.Button(
            self.error_buttons,
            text="Retry",
            command=lambda: self._choose_error_action("retry"),
        ).pack(side="left", padx=(6 if can_continue else 0, 0))
        tk.Button(
            self.error_buttons,
            text="Move next",
            command=lambda: self._choose_error_action("next"),
        ).pack(side="left", padx=(6, 0))

        if checkpoint:
            self.checkpoint_label.pack(anchor="w", pady=(5, 0))
        else:
            self.checkpoint_label.pack_forget()
        if self.warning_var.get():
            self.warning_label.pack(anchor="w", pady=(5, 0))
        else:
            self.warning_label.pack_forget()
        self.error_buttons.pack(anchor="w", pady=(7, 0))
        self.error_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.on_layout_changed()

    def clear_error(self) -> None:
        self.error_frame.grid_remove()
        self.error_var.set("")
        self.warning_var.set("")
        self.checkpoint_var.set("")
        self.on_layout_changed()

    def _pause_or_resume(self) -> None:
        if self.browser_recovery:
            self._choose_browser_recovery("retry")
            return
        if self.paused:
            self.on_resume(self.run_id)
        else:
            self.on_pause(self.run_id)

    def _choose_error_action(self, action: str) -> None:
        for child in self.error_buttons.winfo_children():
            if isinstance(child, tk.Button):
                child.configure(state="disabled")
        self.clear_error()
        self.set_status("Continuing", "Applying your choice...")
        self.on_error_decision(self.run_id, action)

    def _choose_browser_recovery(self, action: str) -> None:
        if not self.browser_recovery_ready:
            return
        self.browser_recovery_ready = False
        self.pause_resume_button.configure(state="disabled")
        self.next_row_button.configure(state="disabled")
        self.set_status("Restarting", "Preparing a new browser for this ID...")
        self.on_browser_recovery(self.run_id, action)


class AutomationStatusWindow:
    """A single dock containing vertically stacked status cards for active IDs."""

    def __init__(
        self,
        parent: tk.Tk,
        *,
        on_pause: Callable[[str], None],
        on_resume: Callable[[str], None],
        on_stop: Callable[[str], None],
        on_error_decision: Callable[[str, str], None],
        on_browser_recovery: Callable[[str, str], None],
        on_stop_all: Callable[[], None],
    ) -> None:
        self.parent = parent
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_stop = on_stop
        self.on_error_decision = on_error_decision
        self.on_browser_recovery = on_browser_recovery
        self.cards: dict[str, _RunStatusCard] = {}
        self.payment_ids: set[str] = set()
        self._manually_hidden = False
        self._user_positioned = False
        self._drag_x = 0
        self._drag_y = 0

        self.window = tk.Toplevel(parent)
        self.window.title("Automation status")
        self.window.attributes("-topmost", True)
        self.window.overrideredirect(True)
        self.window.resizable(False, False)
        self.window.withdraw()

        shell = tk.Frame(
            self.window,
            background="#e5e7eb",
            highlightbackground="#9ca3af",
            highlightthickness=1,
        )
        shell.pack(fill="both", expand=True)
        self.header = tk.Frame(shell, background="#111827", padx=10, pady=7)
        self.header.pack(fill="x")
        self.header.columnconfigure(0, weight=1)
        title_label = tk.Label(
            self.header,
            text="Automation status",
            background="#111827",
            foreground="#ffffff",
            font=("Segoe UI", 10, "bold"),
        )
        title_label.grid(row=0, column=0, sticky="w")
        tk.Button(
            self.header,
            text="Stop all",
            command=on_stop_all,
            background="#374151",
            foreground="#ffffff",
            activebackground="#4b5563",
            activeforeground="#ffffff",
            relief="flat",
            padx=8,
        ).grid(row=0, column=1, padx=(8, 5))
        tk.Button(
            self.header,
            text="X",
            command=self.hide,
            background="#374151",
            foreground="#ffffff",
            activebackground="#4b5563",
            activeforeground="#ffffff",
            relief="flat",
            width=3,
        ).grid(row=0, column=2)
        self.header.bind("<ButtonPress-1>", self._start_drag)
        self.header.bind("<B1-Motion>", self._drag)
        title_label.bind("<ButtonPress-1>", self._start_drag)
        title_label.bind("<B1-Motion>", self._drag)

        content = tk.Frame(shell, background="#f3f4f6")
        content.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            content,
            width=470,
            height=1,
            background="#f3f4f6",
            borderwidth=0,
            highlightthickness=0,
        )
        self.scrollbar = tk.Scrollbar(content, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.cards_frame = tk.Frame(self.canvas, background="#f3f4f6", padx=7, pady=7)
        self.canvas_item = self.canvas.create_window(
            (0, 0), window=self.cards_frame, anchor="nw", width=470
        )
        self.cards_frame.bind("<Configure>", lambda _event: self._refresh_layout())
        self.canvas.bind("<Configure>", self._canvas_resized)

    @property
    def exists(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def has_run(self, run_id: str) -> bool:
        return run_id in self.cards

    @property
    def has_cards(self) -> bool:
        return bool(self.cards)

    def begin_run(self, run_id: str, title: str, accent: str, detail: str) -> None:
        card = self._ensure_card(run_id, title, accent)
        card.begin(title, detail)
        self._manually_hidden = False
        self._show()

    def set_status(self, run_id: str, status: str, detail: str) -> None:
        card = self.cards.get(run_id)
        if card is None:
            return
        card.set_status(status, detail)
        self._refresh_layout()

    def set_progress(self, run_id: str, row: int | None, quantity: int | None = None) -> None:
        card = self.cards.get(run_id)
        if card is not None:
            card.set_progress(row, quantity)

    def set_controls(
        self,
        run_id: str,
        *,
        running: bool,
        starting: bool,
        paused: bool,
        auto_waiting: bool,
        portal_open: bool,
    ) -> None:
        card = self.cards.get(run_id)
        if card is not None:
            card.set_controls(
                running=running,
                starting=starting,
                paused=paused,
                auto_waiting=auto_waiting,
                portal_open=portal_open,
            )

    def show_error(self, run_id: str, **details: object) -> None:
        card = self.cards.get(run_id)
        if card is None:
            return
        row = details.get("row")
        quantity = details.get("quantity")
        card.show_error(
            message=str(details.get("message", "")),
            row=row if isinstance(row, int) else None,
            quantity=quantity if isinstance(quantity, int) else None,
            quantity_action=bool(details.get("quantity_action")),
            post_payment_warning=bool(details.get("post_payment_warning")),
            can_continue=bool(details.get("can_continue")),
            next_checkpoint_title=str(details.get("next_checkpoint_title", "")),
            next_checkpoint_instruction=str(details.get("next_checkpoint_instruction", "")),
        )
        self._manually_hidden = False
        self._show()

    def clear_error(self, run_id: str) -> None:
        card = self.cards.get(run_id)
        if card is not None:
            card.clear_error()

    def show_browser_recovery(self, run_id: str, message: str, *, ready: bool) -> None:
        card = self.cards.get(run_id)
        if card is None:
            return
        card.show_browser_recovery(message, ready=ready)
        self._manually_hidden = False
        self._show()

    def remove_run(self, run_id: str) -> None:
        card = self.cards.pop(run_id, None)
        self.payment_ids.discard(run_id)
        if card is not None:
            card.frame.destroy()
        self._apply_topmost_state()
        if self.cards:
            self._refresh_layout()
        else:
            self.window.withdraw()
            self._manually_hidden = False

    def set_payment_active(self, run_id: str, active: bool) -> None:
        if active:
            self.payment_ids.add(run_id)
        else:
            self.payment_ids.discard(run_id)
        self._apply_topmost_state()

    def hide(self) -> None:
        self._manually_hidden = True
        self.window.withdraw()

    def show(self) -> None:
        self._manually_hidden = False
        if self.cards:
            self._show()

    def close(self) -> None:
        if self.exists:
            self.window.destroy()

    def _ensure_card(self, run_id: str, title: str, accent: str) -> _RunStatusCard:
        card = self.cards.get(run_id)
        if card is not None:
            return card
        card = _RunStatusCard(
            self.cards_frame,
            run_id=run_id,
            title=title,
            accent=accent,
            on_pause=self.on_pause,
            on_resume=self.on_resume,
            on_stop=self.on_stop,
            on_error_decision=self.on_error_decision,
            on_browser_recovery=self.on_browser_recovery,
            on_layout_changed=self._refresh_layout,
        )
        self.cards[run_id] = card
        self._repack_cards()
        return card

    def _repack_cards(self) -> None:
        for card in self.cards.values():
            card.frame.pack_forget()

        def order(item: str) -> tuple[int, int | str]:
            return (0, int(item)) if item.isdigit() else (1, item)

        for run_id in sorted(self.cards, key=order):
            self.cards[run_id].frame.pack(fill="x", pady=(0, 7))
        self._refresh_layout()

    def _show(self) -> None:
        if self._manually_hidden:
            return
        self._apply_topmost_state()
        self.window.deiconify()
        if not self.payment_ids:
            self.window.lift()
        self.window.after_idle(self._refresh_layout)

    def _apply_topmost_state(self) -> None:
        if self.exists:
            self.window.attributes("-topmost", not self.payment_ids)

    def _refresh_layout(self) -> None:
        if not self.exists:
            return
        self.cards_frame.update_idletasks()
        requested_height = self.cards_frame.winfo_reqheight()
        maximum_height = max(180, int(self.window.winfo_screenheight() * 0.62))
        self.canvas.configure(
            height=max(1, min(requested_height, maximum_height)),
            scrollregion=self.canvas.bbox("all"),
        )
        self.window.update_idletasks()
        if not self._user_positioned:
            self._place_window()

    def _canvas_resized(self, event: tk.Event[tk.Misc]) -> None:
        self.canvas.itemconfigure(self.canvas_item, width=max(1, event.width))

    def _place_window(self) -> None:
        horizontal_inset = 8
        taskbar_clearance = 48
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        x = max(0, screen_width - width - horizontal_inset)
        y = max(0, screen_height - height - taskbar_clearance)
        self.window.geometry(f"+{x}+{y}")

    def _start_drag(self, event: tk.Event[tk.Misc]) -> None:
        self._drag_x = event.x_root - self.window.winfo_x()
        self._drag_y = event.y_root - self.window.winfo_y()

    def _drag(self, event: tk.Event[tk.Misc]) -> None:
        self._user_positioned = True
        self.window.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")
