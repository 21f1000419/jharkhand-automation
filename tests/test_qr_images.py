from __future__ import annotations

import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from services.qr_images import clear_qr_image_directory, remove_qr_file, save_data_uri


class QrImageTests(unittest.TestCase):
    @staticmethod
    def image_data_uri() -> str:
        buffer = BytesIO()
        Image.new("RGB", (220, 220), "white").save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def test_base64_qr_is_saved_and_removed_in_temporary_app_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target_directory = Path(directory) / "qr"
            with patch("services.qr_images.qr_image_directory", return_value=target_directory):
                qr_id, path = save_data_uri(self.image_data_uri())
                self.assertEqual(path, target_directory / f"{qr_id}.png")
                self.assertTrue(path.is_file())

                remove_qr_file(path)
                self.assertFalse(path.exists())

    def test_invalid_data_uri_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "base64"):
            save_data_uri("data:image/png;base64,not-valid-base64")

    def test_startup_cleanup_removes_stale_qr_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target_directory = Path(directory) / "qr"
            target_directory.mkdir()
            (target_directory / "stale.png").write_bytes(b"stale")
            with patch("services.qr_images.qr_image_directory", return_value=target_directory):
                clear_qr_image_directory()
            self.assertFalse(target_directory.exists())


if __name__ == "__main__":
    unittest.main()
