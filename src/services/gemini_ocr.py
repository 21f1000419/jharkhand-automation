from __future__ import annotations

import asyncio
import io
from collections import Counter

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from services.captcha_ocr import join_ocr_fragments, normalize_captcha

try:
    import easyocr
    _easyocr_import_error = ""
except ImportError as error:  # pragma: no cover - environment-dependent dependency
    easyocr = None  # type: ignore[assignment]
    _easyocr_import_error = f"{type(error).__name__}: {error}"


class EasyOcrCaptchaSolver:
    """Local CAPTCHA OCR using multiple preprocessing passes and EasyOCR voting."""

    _reader: easyocr.Reader | None = None
    _reader_lock = asyncio.Lock()

    def __init__(self, browser_session: object | None = None) -> None:
        self.browser_session = browser_session

    async def open_setup(self) -> None:
        return None

    async def sign_in_required(self) -> bool:
        return False

    async def verify_ready(self) -> bool:
        if easyocr is None:
            detail = f" ({_easyocr_import_error})" if _easyocr_import_error else ""
            raise RuntimeError(f"EasyOCR could not be imported in this application build{detail}.")
        await self._ensure_reader()
        return True

    async def solve(self, expected_length: int | None = None) -> str:
        raise RuntimeError("Clipboard-based OCR is not available with EasyOCR. Use direct copy mode.")

    async def solve_image(
        self, image_bytes: bytes, expected_length: int | None = None
    ) -> str:
        reader = await self._ensure_reader()
        return await asyncio.to_thread(
            self._solve_image_sync,
            reader,
            image_bytes,
            expected_length,
        )

    async def solve_pasted_clipboard_image(self, expected_length: int | None = None) -> str:
        raise RuntimeError("Clipboard paste OCR test is not available with EasyOCR.")

    async def cancel_active_response(self) -> None:
        return None

    async def _ensure_reader(self) -> easyocr.Reader:
        if easyocr is None:
            raise RuntimeError("EasyOCR is not installed.")
        async with self._reader_lock:
            if self.__class__._reader is None:
                self.__class__._reader = await asyncio.to_thread(
                    easyocr.Reader,
                    ["en"],
                    gpu=False,
                    detect_network="craft",
                    recog_network="standard",
                    verbose=False,
                )
            return self.__class__._reader

    @staticmethod
    def _solve_image_sync(
        reader: easyocr.Reader, image_bytes: bytes, expected_length: int | None
    ) -> str:
        image = Image.open(io.BytesIO(image_bytes)).convert("L")
        variants = build_ocr_variants(image)
        votes: list[str] = []

        for variant in variants:
            results = reader.readtext(
                np.asarray(variant),
                detail=1,
                paragraph=False,
                allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                decoder="beamsearch",
                text_threshold=0.5,
                low_text=0.2,
                link_threshold=0.2,
                width_ths=0.3,
                height_ths=0.3,
            )
            for _, raw, _ in results:
                normalized = normalize_captcha(str(raw), expected_length)
                if normalized:
                    votes.append(normalized)

            joined = join_ocr_fragments(results, expected_length)
            if joined:
                votes.append(joined)

        if expected_length:
            exact_votes = [vote for vote in votes if len(vote) == expected_length]
            if exact_votes:
                return Counter(exact_votes).most_common(1)[0][0]

        if votes:
            return Counter(votes).most_common(1)[0][0]

        raise RuntimeError("EasyOCR could not read a usable CAPTCHA value.")


def build_ocr_variants(image: Image.Image) -> list[Image.Image]:
    base = image.resize((image.width * 4, image.height * 4), Image.Resampling.LANCZOS)
    autocontrast = ImageOps.autocontrast(base)
    sharpened = autocontrast.filter(ImageFilter.SHARPEN)
    strong_sharpen = sharpened.filter(ImageFilter.SHARPEN)
    high_contrast = ImageEnhance.Contrast(sharpened).enhance(2.5)
    thresholded = high_contrast.point(lambda pixel: 255 if pixel > 160 else 0, mode="1").convert("L")
    inverted_threshold = ImageOps.invert(high_contrast).point(
        lambda pixel: 255 if pixel > 140 else 0, mode="1"
    ).convert("L")
    median = high_contrast.filter(ImageFilter.MedianFilter(size=3))
    return [
        base,
        autocontrast,
        sharpened,
        strong_sharpen,
        high_contrast,
        thresholded,
        inverted_threshold,
        median,
    ]


# Kept temporarily for integrations importing the former class name.
GeminiCaptchaSolver = EasyOcrCaptchaSolver
