from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from core.config import ConfigStore
from core.controller import AutomationController
from ui.main_window import MainWindow


def main() -> None:
    root = tk.Tk()
    try:
        config_store = ConfigStore()
        config = config_store.load()
        controller = AutomationController(config)
        MainWindow(root, config, config_store, controller)
        root.mainloop()
    except Exception as error:
        messagebox.showerror("Application error", str(error), parent=root)
        root.destroy()


if __name__ == "__main__":
    main()
