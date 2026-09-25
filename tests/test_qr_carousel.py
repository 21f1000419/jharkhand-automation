from __future__ import annotations

import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from core.models import UiEvent
from ui.main_window import MainWindow, QrItem


def qr_item(number: int, *, paid: bool = False) -> QrItem:
    return QrItem(
        qr_id=f"qr-{number}",
        file_path=Path(f"qr-{number}.png"),
        created_at=100.0,
        expires_at=400.0,
        run_id=str(number),
        dock_id=f"{number}.1",
        worker_label=f"B{number}.1",
        row=number,
        quantity=1,
        paid=paid,
    )


class QrCarouselTests(unittest.TestCase):
    def window(self) -> MainWindow:
        window = MainWindow.__new__(MainWindow)
        window.qr_items = []
        window.qr_index = -1
        window._render_qr = MagicMock()  # type: ignore[method-assign]
        window.append_session_log = MagicMock()  # type: ignore[method-assign]
        return window

    @staticmethod
    def event(number: int) -> UiEvent:
        return UiEvent(
            "qr_available",
            "QR ready",
            {
                "qr_id": f"qr-{number}",
                "file_path": f"qr-{number}.png",
                "created_at": 100.0,
                "expires_at": 400.0,
                "dock_id": f"{number}.1",
                "worker_label": f"B{number}.1",
                "row": number,
                "quantity": 1,
            },
            str(number),
        )

    def test_first_qr_is_selected(self) -> None:
        window = self.window()

        window._append_qr(self.event(1))

        self.assertEqual(window.qr_index, 0)

    def test_appending_qr_preserves_manual_selection(self) -> None:
        window = self.window()
        window.qr_items = [qr_item(1), qr_item(2)]
        window.qr_index = 0

        window._append_qr(self.event(3))

        self.assertEqual(window.qr_index, 0)
        self.assertEqual(window.qr_items[window.qr_index].qr_id, "qr-1")

    def test_mark_paid_only_updates_selected_qr(self) -> None:
        window = self.window()
        window.qr_items = [qr_item(1), qr_item(2)]
        window.qr_index = 1

        window._mark_current_qr_paid()

        self.assertFalse(window.qr_items[0].paid)
        self.assertTrue(window.qr_items[1].paid)
        cast(MagicMock, window._render_qr).assert_called_once_with()

    def test_qr_identity_uses_id_color_and_worker_label(self) -> None:
        window = self.window()
        item = qr_item(2)

        self.assertEqual(window._qr_accent_color(item), "#059669")
        self.assertEqual(window._qr_display_label(item), "ID 2 | B2.1")


if __name__ == "__main__":
    unittest.main()
