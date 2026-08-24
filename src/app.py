from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from automation.browser import cleanup_all_spawned_processes
from core.config import ConfigStore
from core.controller import AutomationController
from ui.main_window import MainWindow


def packaging_smoke_test() -> int:
    """Exercise imports and bundled runtime files from a frozen build."""
    report_name = os.environ.get("COMPITCOM_SMOKE_TEST_REPORT", "packaging-smoke-test.txt")
    report_path = Path(report_name)
    try:
        from core.playwright_browsers import configure_browser_install_directory
        from core.resources import bundled_path
        from services.paddleocr_ocr import PaddleOcrCaptchaSolver

        configure_browser_install_directory()
        model = PaddleOcrCaptchaSolver._create_model()
        if model is None:
            raise RuntimeError("PaddleOCR returned no model.")

        __import__("easyocr")
        from playwright._impl._driver import compute_driver_executable

        node, cli = compute_driver_executable()
        required_files = (
            bundled_path("assets/paddleocr/PP-OCRv6_medium_rec/inference.json"),
            bundled_path("assets/paddleocr/PP-OCRv6_medium_rec/inference.pdiparams"),
            bundled_path("assets/paddleocr/PP-OCRv6_medium_rec/inference.yml"),
            Path(node),
            Path(cli),
        )
        missing = [str(path) for path in required_files if not path.is_file()]
        if missing:
            raise RuntimeError(f"Frozen build is missing: {', '.join(missing)}")
        report_path.write_text("packaging smoke test passed\n", encoding="utf-8")
        return 0
    except Exception as error:  # pragma: no cover - exercised by frozen builds
        report_path.write_text(f"packaging smoke test failed: {error}\n", encoding="utf-8")
        return 1


def main() -> None:
    root = tk.Tk()
    controller: AutomationController | None = None
    try:
        config_store = ConfigStore()
        config = config_store.load()
        controller = AutomationController(config)
        MainWindow(root, config, config_store, controller)
        root.mainloop()
    except Exception as error:
        messagebox.showerror("Application error", str(error), parent=root)
        root.destroy()
    finally:
        if controller is not None:
            controller.shutdown()
        cleanup_all_spawned_processes()


if __name__ == "__main__":
    if "--packaging-smoke-test" in sys.argv:
        raise SystemExit(packaging_smoke_test())
    main()
