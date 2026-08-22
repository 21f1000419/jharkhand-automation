from __future__ import annotations

import asyncio
import io
import os
from typing import Any

import numpy as np
from PIL import Image

from core.resources import bundled_path
from services.captcha_ocr import normalize_captcha

MODEL_NAME = "PP-OCRv6_medium_rec"
MODEL_DIRECTORY = "assets/paddleocr/PP-OCRv6_medium_rec"


class PaddleOcrCaptchaSolver:
    """Offline CAPTCHA OCR using the bundled PP-OCRv6 medium recognition model."""

    _model: Any | None = None
    _model_lock = asyncio.Lock()

    async def verify_ready(self) -> bool:
        await self._ensure_model()
        return True

    async def solve(self, expected_length: int | None = None) -> str:
        raise RuntimeError("Clipboard-based OCR is not available with PaddleOCR. Use direct copy mode.")

    async def solve_image(
        self, image_bytes: bytes, expected_length: int | None = None
    ) -> str:
        model = await self._ensure_model()
        return await asyncio.to_thread(self._solve_image_sync, model, image_bytes, expected_length)

    async def cancel_active_response(self) -> None:
        return None

    @classmethod
    async def _ensure_model(cls) -> Any:
        async with cls._model_lock:
            if cls._model is None:
                cls._model = await asyncio.to_thread(cls._create_model)
            return cls._model

    @staticmethod
    def _create_model() -> Any:
        model_path = bundled_path(MODEL_DIRECTORY)
        if not model_path.is_dir():
            raise RuntimeError(f"Bundled PaddleOCR model is missing: {model_path}")

        # Do not allow PaddleX to start host availability checks or downloads.
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("MKL_NUM_THREADS", "1")
        try:
            from paddleocr import TextRecognition
        except ImportError as error:  # pragma: no cover - environment-dependent dependency
            raise RuntimeError(f"PaddleOCR could not be imported: {error}") from error

        return TextRecognition(
            model_name=MODEL_NAME,
            model_dir=str(model_path),
            device="cpu",
            engine="paddle_static",
        )

    @staticmethod
    def _solve_image_sync(model: Any, image_bytes: bytes, expected_length: int | None) -> str:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image_data = np.asarray(image.convert("RGB"))
        output = model.predict(input=image_data, batch_size=1)
        try:
            item = next(iter(output))
            json_value = item.json
            payload = json_value() if callable(json_value) else json_value
            raw = str(payload["res"]["rec_text"])
        except (KeyError, StopIteration, TypeError, ValueError) as error:
            raise RuntimeError("PaddleOCR did not return a usable CAPTCHA value.") from error

        result = normalize_captcha(raw, expected_length)
        if not result:
            raise RuntimeError("PaddleOCR did not return an alphanumeric CAPTCHA value.")
        return result
