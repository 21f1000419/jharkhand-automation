from __future__ import annotations

import asyncio
import unittest

from core.controls import RunControls
from core.models import CaptchaCopyMode, Credentials, WorkflowStopped
from services.credential_store import decode_credentials, encode_credentials
from services.downloads import extract_reference
from services.gemini_ocr import normalize_captcha


class ServiceTests(unittest.TestCase):
    def test_normalize_captcha_prefers_expected_length(self) -> None:
        self.assertEqual(normalize_captcha("The code is `UL1HVY`.", 6), "UL1HVY")
        self.assertEqual(normalize_captcha("answer: AB12"), "AB12")
        self.assertEqual(normalize_captcha("I cannot read it", 6), "")

    def test_extract_download_reference(self) -> None:
        self.assertEqual(
            extract_reference("https://example.test/gras_estamp_download/7489afcddfd00e8d892a"),
            "7489afcddfd00e8d892a",
        )

    def test_saved_credentials_round_trip(self) -> None:
        credentials = Credentials("citizen-user", "citizen-pass", "egras-user", "egras-pass")
        self.assertEqual(decode_credentials(encode_credentials(credentials)), credentials)

    def test_run_controls_stop_interrupts_checkpoint(self) -> None:
        controls = RunControls(lambda _event: None)
        controls.stop()
        with self.assertRaises(WorkflowStopped):
            asyncio.run(controls.checkpoint())

    def test_error_decision_queue(self) -> None:
        controls = RunControls(lambda _event: None)
        controls.decide("next")
        self.assertEqual(asyncio.run(controls.wait_for_decision()), "next")


    def test_config_store_persists_article_and_csv(self) -> None:
        import tempfile
        from pathlib import Path
        from core.config import AppConfig, ConfigStore

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            store = ConfigStore(settings_path)
            config = AppConfig(
                last_article="AFFIDAVIT (Art. 4)",
                last_csv_path=r"C:\batches\sample.csv",
                last_mode="continuous",
                captcha_copy_mode=CaptchaCopyMode.MOUSE_CURSOR,
            )
            store.save(config)

            reloaded = store.load()
            self.assertEqual(reloaded.last_article, "AFFIDAVIT (Art. 4)")
            self.assertEqual(reloaded.last_csv_path, r"C:\batches\sample.csv")
            self.assertEqual(reloaded.last_mode, "continuous")
            self.assertEqual(reloaded.captcha_copy_mode, CaptchaCopyMode.MOUSE_CURSOR)

    def test_config_store_handles_missing_keys(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from core.config import ConfigStore

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(
                json.dumps({"last_mode": "assisted", "chrome_executable": "chrome.exe"}),
                encoding="utf-8",
            )
            store = ConfigStore(settings_path)
            loaded = store.load()
            self.assertEqual(loaded.last_article, "")
            self.assertEqual(loaded.last_csv_path, "")
            self.assertEqual(loaded.last_mode, "assisted")
            self.assertEqual(loaded.last_chandigarh_time, "14:00")

    def test_parse_browser_time(self) -> None:
        from chandigarh.app import parse_browser_time

        self.assertIsNone(parse_browser_time(""))
        self.assertIsNone(parse_browser_time("   "))
        self.assertIsNone(parse_browser_time(None))

        parsed_24h = parse_browser_time("14:30")
        self.assertIsNotNone(parsed_24h)
        self.assertEqual(parsed_24h.hour, 14)
        self.assertEqual(parsed_24h.minute, 30)

        parsed_12h = parse_browser_time("02:45 PM")
        self.assertIsNotNone(parsed_12h)
        self.assertEqual(parsed_12h.hour, 14)
        self.assertEqual(parsed_12h.minute, 45)

        parsed_full = parse_browser_time("2026-08-21 14:00")
        self.assertIsNotNone(parsed_full)
        self.assertEqual(parsed_full.year, 2026)
        self.assertEqual(parsed_full.month, 8)
        self.assertEqual(parsed_full.day, 21)
        self.assertEqual(parsed_full.hour, 14)

        with self.assertRaises(ValueError):
            parse_browser_time("invalid-time-string")


if __name__ == "__main__":
    unittest.main()

