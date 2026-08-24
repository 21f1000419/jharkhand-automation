from __future__ import annotations

import asyncio
import re
import time
from contextlib import suppress

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from automation.browser import BrowserSession
from services.clipboard_image import read_clipboard_image_png

COMPOSER_SELECTORS = [
    'div[contenteditable="true"][aria-label="Enter a prompt for Gemini"]',
    'rich-textarea div[contenteditable="true"]',
    '.ql-editor[contenteditable="true"]',
    'div[contenteditable="true"][aria-label*="prompt" i]',
]
SEND_SELECTORS = [
    'button[aria-label="Send message"]',
    'button[aria-label*="Send message" i]',
    '[data-test-id="send-button"]',
]
UPLOAD_BUTTON_SELECTORS = [
    # Gemini currently uses "Upload & tools". Older variants used "and",
    # so retain both labels rather than tying OCR to one UI wording.
    'button[aria-label*="Upload & tools" i]',
    'button[aria-label*="Upload and tools" i]',
    'button[aria-label*="Upload files" i]',
    'button[aria-label*="Upload" i]',
]
RESPONSE_SELECTORS = [
    'model-response message-content',
    'model-response .model-response-text',
    '[data-test-id="model-response"]',
]
STOP_SELECTORS = [
    'button[aria-label*="Stop response" i]',
    'button[aria-label*="Stop generating" i]',
]
SIGN_IN_SELECTORS = ['a:has-text("Sign in")', 'button:has-text("Sign in")', '#identifierId']


class GeminiWebCaptchaSolver:
    """CAPTCHA OCR through the signed-in Gemini website in a dedicated browser profile."""

    def __init__(self, browser_session: BrowserSession) -> None:
        self.browser_session = browser_session
        self._lock = asyncio.Lock()

    async def open_setup(self) -> Page:
        page = await self.browser_session.page_for_host(
            "gemini.google.com", create_url="https://gemini.google.com/app"
        )
        if not self.browser_session.headless:
            await page.bring_to_front()
        return page

    async def sign_in_required(self) -> bool:
        page = await self.open_setup()
        return "accounts.google.com" in page.url or await _first_visible(
            page, SIGN_IN_SELECTORS, 2_000
        ) is not None

    async def verify_ready(self) -> bool:
        page = await self.open_setup()
        composer = await _first_visible(page, COMPOSER_SELECTORS, 5_000)
        try:
            return composer is not None and await composer.is_editable()
        except PlaywrightError:
            return False

    async def solve(self, expected_length: int | None = None) -> str:
        return await self.solve_image(read_clipboard_image_png(), expected_length)

    async def solve_image(self, image_bytes: bytes, expected_length: int | None = None) -> str:
        async with self._lock:
            page = await self.open_setup()
            composer = await _first_visible(page, COMPOSER_SELECTORS, 15_000)
            if composer is None:
                raise RuntimeError("Gemini is not signed in or its prompt box was not found.")
            await _clear_composer(page, composer)
            baseline = await _responses(page)
            upload_button = await _first_visible(page, UPLOAD_BUTTON_SELECTORS, 10_000)
            if upload_button is None:
                raise RuntimeError("Gemini's Upload & tools button was not found.")
            await upload_button.click()
            upload = page.locator('input[type="file"]').first
            await upload.wait_for(state="attached", timeout=10_000)
            await upload.set_input_files(
                {"name": "captcha.png", "mimeType": "image/png", "buffer": image_bytes}
            )
            await composer.fill(
                "Read this CAPTCHA. Reply with only the letters and numbers, no explanation."
            )
            await _send(page)
            raw = await _wait_for_response(page, baseline)
            code = _normalize(raw, expected_length)
            if not code:
                raise RuntimeError(f"Gemini returned an unusable CAPTCHA value: {raw[:120]!r}")
            return code

    async def solve_pasted_clipboard_image(self, expected_length: int | None = None) -> str:
        return await self.solve(expected_length)

    async def cancel_active_response(self) -> None:
        with suppress(PlaywrightError, RuntimeError):
            page = await self.open_setup()
            stop = await _first_visible(page, STOP_SELECTORS, 500)
            if stop is not None:
                await stop.click()


def _normalize(value: str, expected_length: int | None) -> str:
    values: list[str] = re.findall(r"[A-Za-z0-9]+", value.upper())
    if expected_length:
        exact = [item for item in values if len(item) == expected_length]
        if exact:
            return exact[-1]
    useful = [item for item in values if 4 <= len(item) <= 10]
    return useful[-1] if useful else ""


async def _first_visible(page: Page, selectors: list[str], timeout_ms: int) -> Locator | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for selector in selectors:
            matches = page.locator(selector)
            for index in range(await matches.count()):
                candidate = matches.nth(index)
                if await candidate.is_visible():
                    return candidate
        await page.wait_for_timeout(100)
    return None


async def _clear_composer(page: Page, composer: Locator) -> None:
    with suppress(PlaywrightError):
        await composer.fill("")
    for selector in ('button[aria-label*="Remove image" i]', '[aria-label*="Remove attachment" i]'):
        matches = page.locator(selector)
        for index in range(await matches.count()):
            button = matches.nth(index)
            if await button.is_visible():
                with suppress(PlaywrightError):
                    await button.click()


async def _send(page: Page) -> None:
    button = await _first_visible(page, SEND_SELECTORS, 30_000)
    if button is None:
        raise RuntimeError("Gemini's Send button did not become available.")
    await button.click()


async def _responses(page: Page) -> list[str]:
    for selector in RESPONSE_SELECTORS:
        texts = [text.strip() for text in await page.locator(selector).all_inner_texts()]
        if any(texts):
            return [text for text in texts if text]
    return []


async def _wait_for_response(page: Page, baseline: list[str]) -> str:
    deadline = time.monotonic() + 60
    latest = ""
    changed_at = time.monotonic()
    while time.monotonic() < deadline:
        current = await _responses(page)
        candidate = current[-1] if current else ""
        previous = baseline[-1] if baseline else ""
        if (
            candidate
            and (len(current) > len(baseline) or candidate != previous)
            and candidate != latest
        ):
            latest = candidate
            changed_at = time.monotonic()
        generating = await _first_visible(page, STOP_SELECTORS, 100) is not None
        if latest and not generating and time.monotonic() - changed_at > 1:
            return latest
        await page.wait_for_timeout(250)
    raise RuntimeError("Gemini did not return a CAPTCHA result within 60 seconds.")
