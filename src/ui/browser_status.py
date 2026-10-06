"""Low-overhead browser-status table embedded in the main window."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from functools import partial
from tkinter import ttk
from typing import Any


class BrowserStatusPage:
    """Display selected IDs as columns and browsers as rows without redraw flicker."""

    def __init__(
        self,
        parent: ttk.Frame,
        *,
        on_refresh: Callable[[str], None],
        on_reset: Callable[[str], None],
    ) -> None:
        self.parent = parent
        self.on_refresh = on_refresh
        self.on_reset = on_reset
        self.tabs: Mapping[int, Any] = {}
        self.id_visibility: dict[int, tk.BooleanVar] = {}
        self.states: dict[str, str] = {}
        self.details: dict[str, str] = {}
        self.progress: dict[str, tuple[int | None, int | None]] = {}
        self.table_visible = False
        self.simple_mode = False
        self.cell_state_vars: dict[str, tk.StringVar] = {}
        self.cell_detail_vars: dict[str, tk.StringVar] = {}
        self.cell_state_labels: dict[str, tk.Label] = {}
        self._layout_signature: tuple[tuple[int, int], ...] = ()

        toolbar = ttk.Frame(parent, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")
        self.table_toggle_button = ttk.Button(
            toolbar, text="Enable browser status", command=self.toggle_table
        )
        self.table_toggle_button.pack(side="left")
        self.simple_mode_button = ttk.Button(
            toolbar,
            text="Use simple mode",
            command=self.toggle_simple_mode,
            state="disabled",
        )
        self.simple_mode_button.pack(side="left", padx=(7, 0))

        self.id_menu = tk.Menu(toolbar, tearoff=False)
        self.id_menu_button = ttk.Menubutton(
            toolbar, text="Select IDs", menu=self.id_menu, state="disabled"
        )
        self.id_menu_button.pack(side="left", padx=(7, 0))
        self.show_all_button = ttk.Button(
            toolbar, text="Show all IDs", command=self._show_all_ids, state="disabled"
        )
        self.show_all_button.pack(side="left", padx=(7, 0))
        self.selection_summary = tk.StringVar(value="Status table off — saves resources")
        ttk.Label(
            toolbar, textvariable=self.selection_summary, foreground="#6b7280"
        ).pack(side="left", padx=(12, 0))

        self.table_host = ttk.Frame(parent, padding=(8, 4, 8, 8))
        self.table_host.rowconfigure(0, weight=1)
        self.table_host.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            self.table_host,
            background="#f3f4f6",
            borderwidth=0,
            highlightthickness=0,
        )
        horizontal = ttk.Scrollbar(
            self.table_host, orient="horizontal", command=self.canvas.xview
        )
        vertical = ttk.Scrollbar(self.table_host, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

        self.table = tk.Frame(self.canvas, background="#d1d5db", padx=1, pady=1)
        self.canvas_item = self.canvas.create_window((0, 0), window=self.table, anchor="nw")
        self.table.bind("<Configure>", self._table_resized)
        self.canvas.bind("<Configure>", self._canvas_resized)

    def sync_tabs(self, tabs: Mapping[int, Any], *, select_id: int | None = None) -> None:
        """Synchronize IDs while preserving the user's visible-ID choices."""
        del select_id  # Compatibility with the earlier single-ID table.
        previous_ids = set(self.id_visibility)
        self.tabs = tabs
        for removed_id in previous_ids - set(tabs):
            self.id_visibility.pop(removed_id, None)
        for tab_id in sorted(tabs):
            if tab_id not in self.id_visibility:
                self.id_visibility[tab_id] = tk.BooleanVar(value=True)
        self._rebuild_id_menu()
        signature = tuple(
            (tab_id, self._browser_count(tabs[tab_id])) for tab_id in sorted(tabs)
        )
        structure_changed = signature != self._layout_signature
        self._layout_signature = signature
        if self.table_visible and structure_changed:
            self._render()

    def set_step(self, run_id: str, stage: str) -> None:
        """Compatibility hook; the compact table intentionally shows status only."""
        del run_id, stage

    def set_status(self, run_id: str, state: str, detail: str = "") -> None:
        self.states[run_id] = state
        self.details[run_id] = detail
        self._update_cell(run_id)

    def set_progress(self, run_id: str, row: int | None, quantity: int | None) -> None:
        self.progress[run_id] = (row, quantity)
        self._update_cell(run_id)

    def toggle_table(self) -> None:
        self.table_visible = not self.table_visible
        if self.table_visible:
            self.table_host.pack(fill="both", expand=True)
            self.table_toggle_button.configure(text="Disable browser status")
            self.simple_mode_button.configure(state="normal")
            self.id_menu_button.configure(state="normal")
            self.show_all_button.configure(state="normal")
            self._render()
            return
        self.table_host.pack_forget()
        self.table_toggle_button.configure(text="Enable browser status")
        self.simple_mode_button.configure(state="disabled")
        self.id_menu_button.configure(state="disabled")
        self.show_all_button.configure(state="disabled")
        self.selection_summary.set("Status table off — saves resources")
        self._clear_cell_bindings()

    def toggle_simple_mode(self) -> None:
        """Switch between status-only cells and detailed cells with controls."""
        self.simple_mode = not self.simple_mode
        self.simple_mode_button.configure(
            text="Use detailed mode" if self.simple_mode else "Use simple mode"
        )
        if self.table_visible:
            self._render()

    def _show_all_ids(self) -> None:
        for visible in self.id_visibility.values():
            visible.set(True)
        if self.table_visible:
            self._render()

    def _rebuild_id_menu(self) -> None:
        self.id_menu.delete(0, "end")
        if not self.tabs:
            self.id_menu.add_command(label="No IDs configured", state="disabled")
            return
        for tab_id in sorted(self.tabs):
            self.id_menu.add_checkbutton(
                label=f"ID {tab_id}",
                variable=self.id_visibility[tab_id],
                command=self._render,
            )

    def _selected_ids(self) -> list[int]:
        return [
            tab_id
            for tab_id in sorted(self.tabs)
            if self.id_visibility.get(tab_id) is not None
            and self.id_visibility[tab_id].get()
        ]

    @staticmethod
    def _browser_count(tab: Any) -> int:
        try:
            return min(20, max(1, int(getattr(tab.config, "browser_count", 1))))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _run_id(tab_id: int, browser_number: int, browser_count: int) -> str:
        return str(tab_id) if browser_count == 1 else f"{tab_id}.{browser_number}"

    @staticmethod
    def _status_color(state: str) -> str:
        lowered = state.casefold()
        if "error" in lowered or "attention" in lowered or "closed" in lowered:
            return "#b91c1c"
        if "pause" in lowered or "wait" in lowered or "queue" in lowered:
            return "#b45309"
        if "complete" in lowered:
            return "#047857"
        if "stop" in lowered:
            return "#6b7280"
        return "#2563eb"

    def _detail_text(self, run_id: str) -> str:
        status_parts: list[str] = []
        progress_row, quantity = self.progress.get(run_id, (None, None))
        if progress_row is not None:
            progress_text = f"Row {progress_row}"
            if quantity is not None:
                progress_text += f"  |  Quantity {quantity}"
            status_parts.append(progress_text)
        detail = self.details.get(run_id, "")
        if detail:
            status_parts.append(detail)
        return "\n".join(status_parts) or "Waiting for browser activity"

    def _update_cell(self, run_id: str) -> None:
        """Update only one existing cell; never rebuild the table for state changes."""
        if not self.table_visible:
            return
        state_var = self.cell_state_vars.get(run_id)
        state_label = self.cell_state_labels.get(run_id)
        if state_var is None or state_label is None:
            return
        state = self.states.get(run_id, "Idle")
        state_var.set(state)
        state_label.configure(foreground=self._status_color(state))
        detail_var = self.cell_detail_vars.get(run_id)
        if detail_var is not None:
            detail_var.set(self._detail_text(run_id))

    def _label_cell(
        self,
        row: int,
        column: int,
        text: str,
        *,
        background: str,
        foreground: str = "#374151",
        font: Any = ("Segoe UI", 9),
        anchor: str = "center",
    ) -> tk.Label:
        label = tk.Label(
            self.table,
            text=text,
            background=background,
            foreground=foreground,
            font=font,
            padx=9,
            pady=7,
            anchor=anchor,
        )
        label.grid(row=row, column=column, sticky="nsew", padx=(0, 1), pady=(0, 1))
        return label

    def _clear_cell_bindings(self) -> None:
        self.cell_state_vars.clear()
        self.cell_detail_vars.clear()
        self.cell_state_labels.clear()

    def _render(self) -> None:
        """Rebuild structure only after a layout or selection change."""
        if not self.table_visible:
            return
        for child in self.table.winfo_children():
            child.destroy()
        self._clear_cell_bindings()
        selected_ids = self._selected_ids()
        total = len(self.tabs)
        mode = "simple" if self.simple_mode else "detailed"
        self.selection_summary.set(
            f"Showing {len(selected_ids)} of {total} ID(s) — {mode} mode"
        )

        if not self.tabs:
            self._label_cell(
                0, 0, "Add an ID to see browser status.", background="#ffffff", anchor="w"
            )
            return
        if not selected_ids:
            self._label_cell(
                0,
                0,
                "Select at least one ID from Select IDs.",
                background="#ffffff",
                anchor="w",
            )
            return

        self._label_cell(
            0,
            0,
            "BROWSER",
            background="#1f2937",
            foreground="#ffffff",
            font=("Segoe UI", 9, "bold"),
        )
        for column, tab_id in enumerate(selected_ids, start=1):
            tab = self.tabs[tab_id]
            accent = "#2563eb"
            owner = getattr(tab, "owner", None)
            if owner is not None and hasattr(owner, "tab_accent_color"):
                accent = str(owner.tab_accent_color(tab_id))
            self._label_cell(
                0,
                column,
                f"ID {tab_id}",
                background=accent,
                foreground="#ffffff",
                font=("Segoe UI", 10, "bold"),
            )
            minimum = 145 if self.simple_mode else 230
            self.table.columnconfigure(column, minsize=minimum, weight=1, uniform="status")

        max_browsers = max(self._browser_count(self.tabs[tab_id]) for tab_id in selected_ids)
        for browser_number in range(1, max_browsers + 1):
            self._label_cell(
                browser_number,
                0,
                f"Browser {browser_number}",
                background="#f3f4f6",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            )
            for column, tab_id in enumerate(selected_ids, start=1):
                tab = self.tabs[tab_id]
                browser_count = self._browser_count(tab)
                if browser_number > browser_count:
                    self._label_cell(
                        browser_number,
                        column,
                        "—",
                        background="#f9fafb",
                        foreground="#9ca3af",
                    )
                    continue
                run_id = self._run_id(tab_id, browser_number, browser_count)
                self._status_cell(browser_number, column, run_id)
        self.table.columnconfigure(0, minsize=125)

    def _status_cell(self, row: int, column: int, run_id: str) -> None:
        state = self.states.get(run_id, "Idle")
        cell = tk.Frame(self.table, background="#ffffff", padx=9, pady=7)
        cell.grid(row=row, column=column, sticky="nsew", padx=(0, 1), pady=(0, 1))
        cell.columnconfigure(0, weight=1)

        state_var = tk.StringVar(value=state)
        state_label = tk.Label(
            cell,
            textvariable=state_var,
            background="#ffffff",
            foreground=self._status_color(state),
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        state_label.grid(row=0, column=0, sticky="ew")
        self.cell_state_vars[run_id] = state_var
        self.cell_state_labels[run_id] = state_label

        if self.simple_mode:
            return

        detail_var = tk.StringVar(value=self._detail_text(run_id))
        tk.Label(
            cell,
            textvariable=detail_var,
            background="#ffffff",
            foreground="#4b5563",
            font=("Segoe UI", 8),
            justify="left",
            anchor="w",
            wraplength=250,
        ).grid(row=1, column=0, sticky="ew", pady=(3, 6))
        self.cell_detail_vars[run_id] = detail_var

        actions = tk.Frame(cell, background="#ffffff")
        actions.grid(row=2, column=0, sticky="w")
        ttk.Button(
            actions,
            text="Refresh",
            command=partial(self.on_refresh, run_id),
            width=9,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="Reset",
            command=partial(self.on_reset, run_id),
            width=9,
        ).pack(side="left", padx=(6, 0))

    def _table_resized(self, _event: object = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_resized(self, event: tk.Event[tk.Misc]) -> None:
        requested = self.table.winfo_reqwidth()
        self.canvas.itemconfigure(self.canvas_item, width=max(event.width, requested))
