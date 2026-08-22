from __future__ import annotations

import time


def copy_image_from_screen_position(x: int, y: int) -> None:
    """Right-click a browser image and invoke its native Copy image command."""
    # pywinauto initializes COM as multithreaded when it is imported.  Import it
    # here, on the automation worker thread, so Tk's GUI thread remains in the
    # apartment mode expected by Windows' modern file and folder dialogs.
    from pywinauto import mouse  # type: ignore[import-untyped]
    from pywinauto.controls.uiawrapper import UIAWrapper  # type: ignore[import-untyped]
    from pywinauto.findwindows import find_elements  # type: ignore[import-untyped]

    mouse.click(button="right", coords=(x, y))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        elements = find_elements(
            title_re=r"(?i)^Copy image$",
            control_type="MenuItem",
            backend="uia",
            top_level_only=False,
            visible_only=True,
            enabled_only=True,
        )
        if elements:
            UIAWrapper(elements[-1]).click_input()
            return
        time.sleep(0.1)
    mouse.click(button="left", coords=(x, y))
    raise RuntimeError("Chrome's 'Copy image' menu command was not found. The browser UI must use English.")
