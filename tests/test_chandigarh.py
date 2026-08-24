from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from chandigarh.app import (
    AutomationCancelled,
    EStampAutomation,
    find_in_any_frame,
)


class ChandigarhAutomationTests(unittest.TestCase):
    def test_estamp_automation_pause_and_resume(self) -> None:
        stop_event = threading.Event()
        pause_event = threading.Event()
        logs: list[str] = []

        automation = EStampAutomation(
            user_id="user1",
            password="pwd",
            state_code="CH",
            article_value="CH-RG-5",
            rows=[{"description": "Test", "consideration_price": "0", "party1": "A", "party2": "B", "paid_by": "A", "stamp_amount": "100"}],
            browser_executable="dummy_chrome.exe",
            browser_time=None,
            collection_mode="SELF",
            sro_location="",
            courier_address={"line1": "", "line2": "", "landmark": "", "city": "", "pin": ""},
            log_fn=lambda msg, _rec: logs.append(msg),
            status_fn=lambda _idx, _st, _det: None,
            stop_event=stop_event,
            pause_event=pause_event,
        )

        pause_event.set()

        def unpause_soon() -> None:
            time.sleep(0.1)
            pause_event.clear()

        unpause_thread = threading.Thread(target=unpause_soon)
        unpause_thread.start()

        automation._check_stop()
        unpause_thread.join()

        self.assertIn("Automation paused. Click Resume to continue.", logs)
        self.assertIn("Automation resumed.", logs)

    def test_estamp_automation_stop_raises_cancelled(self) -> None:
        stop_event = threading.Event()
        pause_event = threading.Event()
        stop_event.set()

        automation = EStampAutomation(
            user_id="user1",
            password="pwd",
            state_code="CH",
            article_value="CH-RG-5",
            rows=[],
            browser_executable="dummy_chrome.exe",
            browser_time=None,
            collection_mode="SELF",
            sro_location="",
            courier_address={"line1": "", "line2": "", "landmark": "", "city": "", "pin": ""},
            log_fn=lambda _msg, _rec: None,
            status_fn=lambda _idx, _st, _det: None,
            stop_event=stop_event,
            pause_event=pause_event,
        )

        with self.assertRaises(AutomationCancelled):
            automation._check_stop()

    def test_find_in_any_frame_pauses_and_resumes(self) -> None:
        stop_event = threading.Event()
        pause_event = threading.Event()
        logs: list[str] = []

        page = MagicMock()
        frame = MagicMock()
        target_locator = MagicMock()
        target_locator.is_visible.return_value = True

        matches = MagicMock()
        matches.count.return_value = 1
        matches.nth.return_value = target_locator

        frame.locator.return_value = matches
        page.frames = [frame]
        page.wait_for_timeout = lambda ms: time.sleep(ms / 1000.0)

        pause_event.set()

        def unpause_soon() -> None:
            time.sleep(0.1)
            pause_event.clear()

        unpause_thread = threading.Thread(target=unpause_soon)
        unpause_thread.start()

        found = find_in_any_frame(
            page,
            "#test-element",
            stop_event=stop_event,
            pause_event=pause_event,
            log_fn=logs.append,
        )
        unpause_thread.join()

        self.assertEqual(found, target_locator)
        self.assertIn("Automation paused. Click Resume to continue.", logs)
        self.assertIn("Automation resumed.", logs)

    def test_process_record_overrides_self_print_limit(self) -> None:
        stop_event = threading.Event()
        pause_event = threading.Event()
        logs: list[str] = []

        automation = EStampAutomation(
            user_id="user1",
            password="pwd",
            state_code="CH",
            article_value="CH-RG-5",
            rows=[],
            browser_executable="dummy_chrome.exe",
            browser_time=None,
            collection_mode="SELF",
            sro_location="",
            courier_address={"line1": "", "line2": "", "landmark": "", "city": "", "pin": ""},
            log_fn=lambda msg, _rec: logs.append(msg),
            status_fn=lambda _idx, _st, _det: None,
            stop_event=stop_event,
            pause_event=pause_event,
        )

        mock_page = MagicMock()
        limit_locator = MagicMock()
        state_locator = MagicMock()

        evaluated_scripts: list[str] = []

        def mock_evaluate(script: str) -> None:
            evaluated_scripts.append(script)

        limit_locator.evaluate.side_effect = mock_evaluate

        def mock_find(page, selector, *, state="visible", timeout=30_000):
            if selector == "#iSttCd":
                return state_locator
            if selector == "#iEsiSelfPrintLimit":
                return limit_locator
            return MagicMock()

        automation._find_in_any_frame = mock_find  # type: ignore[assignment]
        automation._wait_for_pay_stamp_duty_link = MagicMock()  # type: ignore[assignment]
        automation._click_pay_stamp_duty = MagicMock()  # type: ignore[assignment]
        automation._select_collection_mode = MagicMock()  # type: ignore[assignment]

        # Stop before proceeding further
        with patch.object(automation, "_check_stop", side_effect=[None, None, AutomationCancelled()]):
            with self.assertRaises(AutomationCancelled):
                automation._process_record(mock_page, {"description": "test"}, pay_stamp_open=True)

        state_locator.select_option.assert_called_once_with(value="CH")
        self.assertTrue(any("999999" in script for script in evaluated_scripts))


if __name__ == "__main__":
    unittest.main()
