from __future__ import annotations

from io import BytesIO

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
