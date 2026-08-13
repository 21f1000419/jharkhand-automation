from __future__ import annotations

import asyncio
import unittest

from core.controls import RunControls
from core.models import WorkflowStopped
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

    def test_run_controls_stop_interrupts_checkpoint(self) -> None:
        controls = RunControls(lambda _event: None)
        controls.stop()
        with self.assertRaises(WorkflowStopped):
            asyncio.run(controls.checkpoint())

    def test_error_decision_queue(self) -> None:
        controls = RunControls(lambda _event: None)
        controls.decide("next")
        self.assertEqual(asyncio.run(controls.wait_for_decision()), "next")


if __name__ == "__main__":
    unittest.main()

