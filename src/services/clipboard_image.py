from __future__ import annotations

import os
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import ImageGrab


def read_clipboard_image_png() -> bytes:
    """Return the current Windows clipboard image as PNG bytes.

    The portal keeps ownership of copying the CAPTCHA image. Headless Gemini
    cannot paste from the desktop clipboard, so its OCR service turns that
    copied image into an in-memory upload instead.
    """
    image = ImageGrab.grabclipboard()
    if image is None:
        raise RuntimeError("The Windows clipboard does not contain an image.")
    if isinstance(image, list):
        raise RuntimeError("The Windows clipboard contains files, not an image.")
    buffer = BytesIO()
    image.convert("RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


def write_png_to_clipboard(png: bytes) -> None:
    """Put a PNG on the Windows clipboard so Chrome can paste it as an image."""
    if os.name != "nt":
        raise RuntimeError("Clipboard image tests are supported only on Windows.")
    image_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporary_file:
            temporary_file.write(png)
            image_path = Path(temporary_file.name)
        environment = os.environ.copy()
        environment["OCR_TEST_CLIPBOARD_IMAGE"] = str(image_path)
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-Command",
                (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "Add-Type -AssemblyName System.Drawing; "
                    "$image = [System.Drawing.Image]::FromFile($env:OCR_TEST_CLIPBOARD_IMAGE); "
                    "try { [System.Windows.Forms.Clipboard]::SetImage($image) } "
                    "finally { $image.Dispose() }"
                ),
            ],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Unknown PowerShell error."
            raise RuntimeError(f"Could not copy the test CAPTCHA image to the Windows clipboard: {detail}")
    finally:
        if image_path is not None:
            image_path.unlink(missing_ok=True)
