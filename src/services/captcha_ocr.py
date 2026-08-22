from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol


class CaptchaSolver(Protocol):
    async def verify_ready(self) -> bool: ...
    async def solve(self, expected_length: int | None = None) -> str: ...
    async def solve_image(self, image_bytes: bytes, expected_length: int | None = None) -> str: ...
    async def cancel_active_response(self) -> None: ...


def normalize_captcha(value: str, expected_length: int | None = None) -> str:
    candidates: list[str] = re.findall(r"[A-Za-z0-9]+", value.upper())
    common_words = {
        "ANSWER",
        "CANNOT",
        "CAPTCHA",
        "CHARACTERS",
        "CODE",
        "DIGITS",
        "IMAGE",
        "LETTERS",
        "ONLY",
        "READ",
        "RETURN",
        "UNABLE",
    }
    candidates = [candidate for candidate in candidates if candidate not in common_words]
    if expected_length:
        exact = [candidate for candidate in candidates if len(candidate) == expected_length]
        if exact:
            return exact[-1]
    useful = [candidate for candidate in candidates if 4 <= len(candidate) <= 10]
    return useful[-1] if useful else ""


def join_ocr_fragments(results: Sequence[Sequence[object]], expected_length: int | None) -> str:
    """Join left-to-right OCR fragments only when they form the expected CAPTCHA length."""
    if expected_length is None:
        return ""

    fragments: list[tuple[float, str]] = []
    for result in results:
        if len(result) < 2:
            continue
        bounding_box, raw = result[0], str(result[1])
        if not isinstance(bounding_box, Sequence) or isinstance(bounding_box, str | bytes):
            continue
        x_positions = [
            float(point[0])
            for point in bounding_box
            if isinstance(point, Sequence)
            and not isinstance(point, str | bytes)
            and point
            and isinstance(point[0], int | float)
        ]
        if not x_positions:
            continue
        fragment = "".join(re.findall(r"[A-Za-z0-9]+", raw.upper()))
        if fragment:
            fragments.append((min(x_positions), fragment))

    combined = "".join(fragment for _, fragment in sorted(fragments))
    return combined if len(combined) == expected_length else ""
