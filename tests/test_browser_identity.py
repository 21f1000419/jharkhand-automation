from __future__ import annotations

import unittest

from automation.browser import (
    _is_hex_color,
    _window_identity_script,
)


class BrowserIdentityTests(unittest.TestCase):
    def test_hex_color_validation(self) -> None:
        self.assertTrue(_is_hex_color("#2563eb"))
        self.assertTrue(_is_hex_color("ffffff"))
        self.assertFalse(_is_hex_color("#12345"))
        self.assertFalse(_is_hex_color("not-a-color"))

    def test_page_badge_script_contains_the_id_and_accent(self) -> None:
        script = _window_identity_script("#2563eb", "ID 1 | citizen-user")

        self.assertIn("ID 1", script)
        self.assertIn("ID 1 | citizen-user", script)
        self.assertIn("#2563eb", script)
        self.assertIn('font: "700 18px/1.2 Segoe UI, sans-serif"', script)
        self.assertIn('minWidth: "110px"', script)
        self.assertIn('pointerEvents: "none"', script)


if __name__ == "__main__":
    unittest.main()
