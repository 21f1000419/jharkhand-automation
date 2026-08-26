from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from core.browser_detection import candidate_paths
from core.config import detect_chrome


class BrowserDetectionTests(unittest.TestCase):
    def test_macos_chrome_bundle_paths_are_checked(self) -> None:
        with patch("core.browser_detection.sys.platform", "darwin"):
            paths = candidate_paths("chrome.exe", ("Google", "Chrome", "Application"))

        self.assertIn(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"), paths)

    def test_detect_chrome_finds_macos_application_bundle(self) -> None:
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        with (
            patch("core.config.sys.platform", "darwin"),
            patch("core.config.Path.is_file", return_value=True),
        ):
            self.assertEqual(detect_chrome(), Path(chrome))


if __name__ == "__main__":
    unittest.main()
