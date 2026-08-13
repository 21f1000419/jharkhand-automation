from __future__ import annotations

import asyncio
import base64
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from automation.browser import BrowserSession

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
OCR_PROMPT = "OCR this."
RESPONSE_TIMEOUT_SECONDS = 15
MAX_RESPONSE_ATTEMPTS = 2
ATTACHMENT_SELECTORS = [
    'img[src^="blob:"]',
    '[data-test-id*="attachment" i]',
    '[aria-label*="Remove image" i]',
    '[aria-label*="Remove attachment" i]',
]


@dataclass(frozen=True)
class ImageFile:
    name: str
    mime_type: str
    data: bytes


class GeminiCaptchaSolver:
    def __init__(self, browser_session: BrowserSession) -> None:
        self.browser_session = browser_session
        self._lock = asyncio.Lock()

    async def open_setup(self) -> Page:
        page = await self.browser_session.page_for_host(
            "gemini.google.com", create_url="https://gemini.google.com/app"
        )
        await page.bring_to_front()
        return page

    async def verify_ready(self) -> bool:
        page = await self.open_setup()
        composer = await find_first_visible(page, COMPOSER_SELECTORS, 4_000)
        if composer is None:
            return False
        try:
            return await composer.is_editable()
        except PlaywrightError:
            return False

    async def solve(self, image: bytes, expected_length: int | None = None) -> str:
        async with self._lock:
            page = await self.browser_session.page_for_host(
                "gemini.google.com", create_url="https://gemini.google.com/app"
            )
            composer = await find_first_visible(page, COMPOSER_SELECTORS, 15_000)
            if composer is None:
                raise RuntimeError("Gemini is not signed in or its prompt box could not be found.")
            for attempt in range(MAX_RESPONSE_ATTEMPTS):
                baseline = await read_responses(page)
                await attach_image(page, composer, ImageFile("captcha.png", "image/png", image))
                await composer.fill(OCR_PROMPT)
                await send_prompt(page)
                try:
                    raw = await wait_for_response(page, baseline, RESPONSE_TIMEOUT_SECONDS)
                except GeminiResponseTimeout:
                    await stop_response(page)
                    if attempt + 1 < MAX_RESPONSE_ATTEMPTS:
                        continue
                    raise RuntimeError(
                        "Gemini did not return a CAPTCHA OCR result within 15 seconds."
                    ) from None
                code = normalize_captcha(raw, expected_length)
                if code:
                    return code
                if attempt + 1 < MAX_RESPONSE_ATTEMPTS:
                    continue
                raise RuntimeError(f"Gemini returned an unusable CAPTCHA value: {raw[:120]!r}")
        raise RuntimeError("Gemini CAPTCHA OCR could not be completed.")


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


async def attach_image(page: Page, composer: Locator, image: ImageFile) -> None:
    baseline = await attachment_counts(page)
    try:
        await page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"], origin=origin_for(page.url)
        )
        await page.evaluate(
            """async ({ base64Value, mimeType }) => {
                const binary = atob(base64Value);
                const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
                await navigator.clipboard.write([
                  new ClipboardItem({[mimeType]: new Blob([bytes], {type: mimeType})})
                ]);
            }""",
            {
                "base64Value": base64.b64encode(image.data).decode("ascii"),
                "mimeType": image.mime_type,
            },
        )
        await composer.click()
        await page.keyboard.press("Control+V")
        if await wait_for_attachment(page, baseline, 12_000):
            return
    except Exception:
        pass

    inputs = page.locator('input[type="file"]')
    for index in range(await safe_count(inputs)):
        try:
            await inputs.nth(index).set_input_files(
                {"name": image.name, "mimeType": image.mime_type, "buffer": image.data}
            )
            if await wait_for_attachment(page, baseline, 12_000):
                return
        except PlaywrightError:
            continue
    raise RuntimeError("Could not attach the CAPTCHA image to Gemini.")


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


def origin_for(value: str) -> str:
    parsed = urlparse(value)
    return f"{parsed.scheme}://{parsed.netloc}"
