from __future__ import annotations

import asyncio
import re
from typing import Any

try:
    import ddddocr as _ddddocr

    ddddocr: Any | None = _ddddocr
    _ddddocr_import_error = ""
except ImportError as error:  # pragma: no cover - environment-dependent dependency
    ddddocr = None
    _ddddocr_import_error = f"{type(error).__name__}: {error}"


def normalize_ddddocr_captcha(value: str, expected_length: int | None = None) -> str:
    candidates = re.findall(r"[A-Za-z0-9]+", value.upper())
    if expected_length is not None:
        exact = [candidate for candidate in candidates if len(candidate) == expected_length]
        if exact:
            return str(exact[-1])
    return str(candidates[-1]) if candidates else ""


class DdddOcrCaptchaSolver:
    """Local CAPTCHA OCR using the bundled ddddocr ONNX model."""

    _ocr: Any | None = None
    _ocr_lock = asyncio.Lock()

    async def verify_ready(self) -> bool:
        await self._ensure_ocr()
        return True

    async def solve(self, expected_length: int | None = None) -> str:
        raise RuntimeError("Clipboard-based OCR is not available with ddddocr. Use direct copy mode.")

    async def solve_image(
        self, image_bytes: bytes, expected_length: int | None = None
    ) -> str:
        ocr = await self._ensure_ocr()
        raw = await asyncio.to_thread(ocr.classification, image_bytes)
        result = normalize_ddddocr_captcha(str(raw), expected_length)
        if not result:
            raise RuntimeError("ddddocr did not return an alphanumeric CAPTCHA value.")
        return result

    async def cancel_active_response(self) -> None:
        return None

    @classmethod
    async def _ensure_ocr(cls) -> Any:
        if ddddocr is None:
            detail = f" ({_ddddocr_import_error})" if _ddddocr_import_error else ""
            raise RuntimeError(f"ddddocr could not be imported in this application build{detail}.")
        async with cls._ocr_lock:
            if cls._ocr is None:
                cls._ocr = await asyncio.to_thread(ddddocr.DdddOcr, show_ad=False)
            return cls._ocr
