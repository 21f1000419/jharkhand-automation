from __future__ import annotations

import base64
import binascii
import os
import shutil
import tempfile
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image

from core.config import app_data_directory


def qr_image_directory() -> Path:
    return app_data_directory() / "temporary-qr-codes"


def save_data_uri(data_uri: str) -> tuple[str, Path]:
    """Validate a base64 image data URI and save it as a temporary PNG."""
    header, separator, encoded = data_uri.partition(",")
    if not separator or not header.casefold().startswith("data:image/") or ";base64" not in header:
        raise ValueError("The payment page did not provide a base64 QR image.")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("The payment QR image contains invalid base64 data.") from error
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
    except Exception as error:
        raise ValueError("The payment QR image is not a valid image.") from error

    qr_id = uuid.uuid4().hex
    directory = qr_image_directory()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{qr_id}.png"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)
    return qr_id, target


def remove_qr_file(path: Path) -> None:
    """Delete a QR file only when it belongs to the application's temporary directory."""
    directory = qr_image_directory().resolve()
    candidate = path.resolve()
    try:
        candidate.relative_to(directory)
    except ValueError:
        return
    candidate.unlink(missing_ok=True)


def clear_qr_image_directory() -> None:
    directory = qr_image_directory()
    if directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)
