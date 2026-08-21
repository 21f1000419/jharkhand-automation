"""Open a visual preview of the automation status dock without running automation."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ui.automation_status import AutomationStatusWindow


def main() -> None:
    root = tk.Tk()
    root.title("Automation status dock preview")
    root.geometry("980x700")
    root.minsize(980, 700)

    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Automation Status Dock Preview", font=("Segoe UI", 15, "bold")).pack(
        anchor="w"
    )
    ttk.Label(
        frame,
        text="The borderless dock is anchored at this window's lower-right corner.\n"
        "Right-click the dock or close this window when you are done.",
        foreground="#555555",
        justify="left",
    ).pack(anchor="w", pady=(8, 0))

    def open_dock() -> None:
        dock = AutomationStatusWindow(
            root,
            on_pause=lambda: None,
            on_resume=lambda: None,
            on_stop=lambda: None,
            on_error_decision=lambda _action: None,
        )
        dock.set_status("Working", "Filling the eStamp form…")
        dock.set_progress(row=4, quantity=2)
        dock.window.bind("<Button-3>", lambda _event: root.destroy())

    root.after(100, open_dock)
    root.mainloop()


if __name__ == "__main__":
    main()
