from __future__ import annotations

from typing import Protocol


class CaptchaSolver(Protocol):
    async def verify_ready(self) -> bool: ...
    async def solve(self, expected_length: int | None = None) -> str: ...
    async def solve_image(self, image_bytes: bytes, expected_length: int | None = None) -> str: ...
    async def cancel_active_response(self) -> None: ...


class FallbackCaptchaSolver:
    """Use local OCR first and Gemini when local OCR cannot return a usable value."""

    def __init__(self, primary: CaptchaSolver, fallback: CaptchaSolver) -> None:
        self.primary = primary
        self.fallback = fallback
        self._use_fallback_next = False

    async def verify_ready(self) -> bool:
        return await self.primary.verify_ready() and await self.fallback.verify_ready()

    async def solve(self, expected_length: int | None = None) -> str:
        if self._use_fallback_next:
            self._use_fallback_next = False
            return await self.fallback.solve(expected_length)
        try:
            return await self.primary.solve(expected_length)
        except Exception:
            return await self.fallback.solve(expected_length)

    async def solve_image(self, image_bytes: bytes, expected_length: int | None = None) -> str:
        if self._use_fallback_next:
            self._use_fallback_next = False
            return await self.fallback.solve_image(image_bytes, expected_length)
        try:
            return await self.primary.solve_image(image_bytes, expected_length)
        except Exception:
            return await self.fallback.solve_image(image_bytes, expected_length)

    async def cancel_active_response(self) -> None:
        await self.primary.cancel_active_response()
        await self.fallback.cancel_active_response()

    def mark_rejected(self) -> None:
        """Use Gemini for the next fresh CAPTCHA after the portal rejects EasyOCR."""
        self._use_fallback_next = True
