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
    'textarea[aria-label*="prompt" i]',
]
SEND_SELECTORS = [
    'button[aria-label="Send message"]',
    'button[aria-label*="Send message" i]',
    'button[aria-label="Send"]',
    '[data-test-id="send-button"]',
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
SIGN_IN_SELECTORS = [
    'a:has-text("Sign in")',
    'button:has-text("Sign in")',
    '[aria-label*="Sign in" i]',
    'input[type="submit"][value*="Sign in" i]',
    "#identifierId",
]
OCR_PROMPT = "OCR this."
RESPONSE_TIMEOUT_SECONDS = 60
ATTACHMENT_SELECTORS = [
    'img[src^="blob:"]',
    '[data-test-id*="attachment" i]',
    '[aria-label*="Remove image" i]',
    '[aria-label*="Remove attachment" i]',
]
REMOVE_ATTACHMENT_SELECTORS = [
    'button[aria-label*="Remove image" i]',
    'button[aria-label*="Remove attachment" i]',
    '[aria-label*="Remove image" i]',
    '[aria-label*="Remove attachment" i]',
]
UPLOAD_BUTTON_SELECTORS = [
    'button[aria-label="Upload and tools"]',
    'button[aria-label*="Upload and tools" i]',
]


class GeminiCaptchaSolver:
    def __init__(self, browser_session: BrowserSession) -> None:
        self.browser_session = browser_session
        self._lock = asyncio.Lock()
        self._greeting_verified = False

    async def open_setup(self) -> Page:
        page = await self.browser_session.page_for_host(
            "gemini.google.com", create_url="https://gemini.google.com/app"
        )
        if not self.browser_session.headless:
            await page.bring_to_front()
        return page

    async def sign_in_required(self) -> bool:
        page = await self.open_setup()
        if "accounts.google.com" in page.url:
            return True
        return await find_first_visible(page, SIGN_IN_SELECTORS, 2_000) is not None

    async def verify_ready(self) -> bool:
        page = await self.open_setup()
        composer = await find_first_visible(page, COMPOSER_SELECTORS, 4_000)
        if composer is None:
            return False
        try:
            if not await composer.is_editable():
                return False
        except PlaywrightError:
            return False
        if self._greeting_verified:
            return True
        try:
            baseline = await read_responses(page)
            await composer.fill("Hi")
            await send_prompt(page)
            await wait_for_response(page, baseline, RESPONSE_TIMEOUT_SECONDS)
        except (GeminiResponseTimeout, PlaywrightError, RuntimeError):
            return False
        self._greeting_verified = True
        return True

    async def solve(self, expected_length: int | None = None) -> str:
        """Read the CAPTCHA image that the portal has copied to the clipboard."""
        return await self.solve_image(read_clipboard_image_png(), expected_length)

    async def solve_image(
        self, image_bytes: bytes, expected_length: int | None = None
    ) -> str:
        """Upload the supplied CAPTCHA bytes directly and return Gemini's OCR result."""
        async with self._lock:
            page = await self.browser_session.page_for_host(
                "gemini.google.com", create_url="https://gemini.google.com/app"
            )
            composer = await find_first_visible(page, COMPOSER_SELECTORS, 15_000)
            if composer is None:
                raise RuntimeError("Gemini is not signed in or its prompt box could not be found.")
            try:
                await clear_composer(page, composer)
                # Let the dynamically-created upload control finish binding before opening it.
                await page.wait_for_timeout(1_000)
                baseline = await read_responses(page)
                await attach_image(page, image_bytes)
                await composer.fill(OCR_PROMPT)
                await send_prompt(page)
                raw = await wait_for_response(page, baseline, RESPONSE_TIMEOUT_SECONDS)
            except GeminiResponseTimeout as error:
                await stop_response(page)
                raise RuntimeError(
                    "Gemini did not return a CAPTCHA OCR result within "
                    f"{RESPONSE_TIMEOUT_SECONDS} seconds."
                ) from error
            code = normalize_captcha(raw, expected_length)
            if code:
                return code
            raise RuntimeError(f"Gemini returned an unusable CAPTCHA value: {raw[:120]!r}")

    async def solve_pasted_clipboard_image(self, expected_length: int | None = None) -> str:
        """Paste the Windows clipboard image into Gemini and return its OCR result."""
        async with self._lock:
            page = await self.browser_session.page_for_host(
                "gemini.google.com", create_url="https://gemini.google.com/app"
            )
            composer = await find_first_visible(page, COMPOSER_SELECTORS, 15_000)
            if composer is None:
                raise RuntimeError("Gemini is not signed in or its prompt box could not be found.")
            try:
                await clear_composer(page, composer)
                await page.wait_for_timeout(1_000)
                baseline = await read_responses(page)
                attachment_baseline = await attachment_counts(page)
                await paste_clipboard_image(page, composer, attachment_baseline)
                await composer.fill(OCR_PROMPT)
                await send_prompt(page)
                raw = await wait_for_response(page, baseline, RESPONSE_TIMEOUT_SECONDS)
            except GeminiResponseTimeout as error:
                await stop_response(page)
                raise RuntimeError(
                    "Gemini did not return a clipboard-paste CAPTCHA OCR result within "
                    f"{RESPONSE_TIMEOUT_SECONDS} seconds."
                ) from error
            code = normalize_captcha(raw, expected_length)
            if code:
                return code
            raise RuntimeError(f"Gemini returned an unusable CAPTCHA value: {raw[:120]!r}")

    async def cancel_active_response(self) -> None:
        """Stop Gemini and clear an abandoned OCR draft after manual input takes over."""
        try:
            page = await self.browser_session.page_for_host(
                "gemini.google.com", create_url="https://gemini.google.com/app"
            )
            await stop_response(page)
            composer = await find_first_visible(page, COMPOSER_SELECTORS, 1_000)
            if composer is not None:
                await clear_composer(page, composer)
        except (PlaywrightError, RuntimeError):
            return


class GeminiResponseTimeout(RuntimeError):
    """Raised when a Gemini reply does not settle within the CAPTCHA response deadline."""


def normalize_captcha(value: str, expected_length: int | None = None) -> str:
    candidates: list[str] = re.findall(r"[A-Za-z0-9]+", value)
    common_words = {
        "answer",
        "cannot",
        "captcha",
        "characters",
        "code",
        "digits",
        "image",
        "letters",
        "only",
        "read",
        "return",
        "unable",
    }
    candidates = [candidate for candidate in candidates if candidate.lower() not in common_words]
    if expected_length:
        exact = [candidate for candidate in candidates if len(candidate) == expected_length]
        if exact:
            return exact[-1]
    useful = [candidate for candidate in candidates if 4 <= len(candidate) <= 10]
    return useful[-1] if useful else ""


async def attach_image(page: Page, image_bytes: bytes) -> None:
    """Upload an in-memory CAPTCHA image without using the desktop clipboard."""
    baseline = await attachment_counts(page)
    try:
        upload_button = await find_first_visible(page, UPLOAD_BUTTON_SELECTORS, 10_000)
        if upload_button is None:
            raise RuntimeError("Gemini's Upload and tools button was not found.")
        await upload_button.click()
        upload = page.locator('input[type="file"]').first
        await upload.wait_for(state="attached", timeout=10_000)
        await upload.set_input_files(
            {
                "name": "captcha.png",
                "mimeType": "image/png",
                "buffer": image_bytes,
            }
        )
        if await wait_for_attachment(page, baseline, 12_000):
            return
    except Exception as error:
        raise RuntimeError(f"Could not upload the CAPTCHA image to Gemini: {error}") from error
    raise RuntimeError("Gemini did not show the uploaded CAPTCHA image.")


async def paste_clipboard_image(page: Page, composer: Locator, baseline: list[int]) -> None:
    """Use Chrome's normal Ctrl+V image handling and wait for Gemini's attachment preview."""
    await composer.click()
    await page.keyboard.press("Control+V")
    if not await wait_for_attachment(page, baseline, 12_000):
        raise RuntimeError("Gemini did not show an image after pasting from the Windows clipboard.")


async def clear_composer(page: Page, composer: Locator) -> None:
    """Remove stale text/attachments left by a cancelled or interrupted OCR attempt."""
    await stop_response(page)
    with suppress(PlaywrightError):
        await composer.fill("")
    for selector in REMOVE_ATTACHMENT_SELECTORS:
        matches = page.locator(selector)
        for index in range(await safe_count(matches) - 1, -1, -1):
            button = matches.nth(index)
            if not await is_visible(button):
                continue
            try:
                await button.click()
            except PlaywrightError:
                continue


async def send_prompt(page: Page) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        for selector in SEND_SELECTORS:
            matches = page.locator(selector)
            for index in range(await safe_count(matches)):
                button = matches.nth(index)
                if await is_visible(button) and await is_enabled(button):
                    await button.click()
                    return
        await page.wait_for_timeout(250)
    raise RuntimeError("Gemini's Send button did not become available.")


async def wait_for_response(page: Page, baseline: list[str], timeout_seconds: int) -> str:
    deadline = time.monotonic() + timeout_seconds
    latest = ""
    last_change = time.monotonic()
    while time.monotonic() < deadline:
        current = await read_responses(page)
        candidate = current[-1] if current else ""
        previous = baseline[-1] if baseline else ""
        if (
            candidate
            and (len(current) > len(baseline) or candidate != previous)
            and candidate != latest
        ):
            latest = candidate
            last_change = time.monotonic()
        if latest and not await any_visible(page, STOP_SELECTORS) and time.monotonic() - last_change > 1.5:
            return latest
        await page.wait_for_timeout(250)
    raise GeminiResponseTimeout


async def stop_response(page: Page) -> None:
    stop = await find_first_visible(page, STOP_SELECTORS, 1_000)
    if stop is None:
        return
    try:
        await stop.click()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and await any_visible(page, STOP_SELECTORS):
            await page.wait_for_timeout(100)
    except PlaywrightError:
        return


async def read_responses(page: Page) -> list[str]:
    for selector in RESPONSE_SELECTORS:
        try:
            texts = [item.strip() for item in await page.locator(selector).all_inner_texts()]
            if any(texts):
                return [item for item in texts if item]
        except PlaywrightError:
            continue
    return []


async def attachment_counts(page: Page) -> list[int]:
    values: list[int] = []
    for selector in ATTACHMENT_SELECTORS:
        locator = page.locator(selector)
        values.append(
            sum([await is_visible(locator.nth(index)) for index in range(await safe_count(locator))])
        )
    return values


async def wait_for_attachment(page: Page, baseline: list[int], timeout_ms: int) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        current = await attachment_counts(page)
        if any(count > baseline[index] for index, count in enumerate(current)):
            return True
        await page.wait_for_timeout(200)
    return False


async def find_first_visible(page: Page, selectors: list[str], timeout_ms: int) -> Locator | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for selector in selectors:
            matches = page.locator(selector)
            for index in range(await safe_count(matches)):
                candidate = matches.nth(index)
                if await is_visible(candidate):
                    return candidate
        await page.wait_for_timeout(100)
    return None


async def any_visible(page: Page, selectors: list[str]) -> bool:
    return await find_first_visible(page, selectors, 100) is not None


async def safe_count(locator: Locator) -> int:
    try:
        return int(await locator.count())
    except PlaywrightError:
        return 0


async def is_visible(locator: Locator) -> bool:
    try:
        return bool(await locator.is_visible())
    except PlaywrightError:
        return False


async def is_enabled(locator: Locator) -> bool:
    try:
        return bool(await locator.is_enabled())
    except PlaywrightError:
        return False
