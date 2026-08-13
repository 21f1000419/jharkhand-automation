"""Local Gemini-backed OCR API.

Run with:
    .venv\\Scripts\\python.exe -m uvicorn src.gemini_ocr_server:app --host 127.0.0.1 --port 4318
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import os
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, Page, async_playwright

PORT = int(os.environ.get("PORT", "4318"))
DEBUG_PORT = int(os.environ.get("AUTOMATION_DEBUG_PORT", "9223"))
ORCHESTRATOR_ROOT = Path(os.environ.get("AGENT_ORCHESTRATOR_ROOT", r"D:\Projects\agent-orchestrator"))
PROFILE_PATH = Path(os.environ.get("CHROME_USER_DATA_DIR", ORCHESTRATOR_ROOT / ".playwright-chrome-profile"))
CHROME_PROFILE_DIRECTORY = os.environ.get("CHROME_PROFILE_DIRECTORY", "Default")
CHROME_EXECUTABLE_PATH = Path(
    os.environ.get(
        "CHROME_EXECUTABLE_PATH",
        r"C:\Users\vikas\AppData\Local\Google\Chrome\Application\chrome.exe",
    )
)
MAX_IMAGE_BYTES = 25 * 1024 * 1024

COMPOSER_SELECTORS = [
    'div[contenteditable="true"][aria-label="Enter a prompt for Gemini"]',
    'rich-textarea div[contenteditable="true"]',
    '.ql-editor[contenteditable="true"]',
    'div[contenteditable="true"][aria-label*="prompt"]',
    'textarea[aria-label*="prompt"]',
]
SEND_SELECTORS = [
    'button[aria-label="Send message"]',
    'button[aria-label*="Send message"]',
    'button[aria-label="Send"]',
    '[data-test-id="send-button"]',
]
RESPONSE_SELECTORS = [
    'model-response message-content',
    'model-response .model-response-text',
    '[data-test-id="model-response"]',
]
STOP_SELECTORS = [
    'button[aria-label*="Stop response"]',
    'button[aria-label*="Stop generating"]',
]
ATTACHMENT_SELECTORS = [
    'img[src^="blob:"]',
    '[data-test-id*="attachment" i]',
    '[aria-label*="Remove image" i]',
    '[aria-label*="Remove attachment" i]',
    '[aria-label*="Remove file" i]',
]


@dataclass(frozen=True)
class ImageFile:
    name: str
    mime_type: str
    data: bytes


class GeminiOcrBrowser:
    """Serialises Gemini requests through one Chrome/CDP browser context."""

    def __init__(self) -> None:
        self._playwright: Any | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.chrome_process: subprocess.Popen[bytes] | None = None
        self.owns_chrome = False
        self._lock = asyncio.Lock()

    async def ocr(self, image: ImageFile) -> dict[str, str]:
        async with self._lock:
            return await self._run_ocr(image)

    async def get_gemini_page(self) -> Page:
        context = await self.get_context()
        for page in context.pages:
            if not page.is_closed() and hostname_for(page.url) == "gemini.google.com":
                return page

        page = await context.new_page()
        await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=60_000)
        return page

    async def get_context(self) -> BrowserContext:
        if self.context is not None:
            return self.context

        endpoint = f"http://127.0.0.1:{DEBUG_PORT}"
        if not await debugger_ready(endpoint):
            self.chrome_process = launch_chrome()
            self.owns_chrome = True
            await wait_for_debugger(endpoint, self.chrome_process)

        self._playwright = await async_playwright().start()
        try:
            self.browser = await self._playwright.chromium.connect_over_cdp(endpoint)
            self.context = self.browser.contexts[0] if self.browser.contexts else None
            if self.context is None:
                raise RuntimeError("Chrome has no browser context.")
            self.browser.on("disconnected", self._on_browser_disconnected)
            return self.context
        except Exception:
            await self._stop_playwright()
            raise

    async def close(self) -> None:
        browser, chrome_process = self.browser, self.chrome_process
        self.browser = None
        self.context = None
        self.chrome_process = None
        if self.owns_chrome and browser is not None:
            await browser.close()
        if self.owns_chrome and chrome_process is not None and chrome_process.poll() is None:
            chrome_process.kill()
        self.owns_chrome = False
        await self._stop_playwright()

    async def detach(self) -> None:
        """Disconnect without closing Chrome; used by the interactive login tool."""
        self.browser = None
        self.context = None
        self.chrome_process = None
        self.owns_chrome = False
        await self._stop_playwright()

    async def _run_ocr(self, image: ImageFile) -> dict[str, str]:
        page = await self.get_gemini_page()
        composer = await find_first_visible(page, COMPOSER_SELECTORS, 20_000)
        if composer is None:
            raise RuntimeError("Could not find the Gemini composer. Run setup_gemini_browser.py, sign in to Gemini, then retry.")

        baseline = await read_responses(page)
        await paste_image(page, composer, image)
        await composer.fill("OCR this")
        await send_prompt(page)
        return {"text": await wait_for_response(page, baseline), "url": page.url}

    def _on_browser_disconnected(self) -> None:
        self.browser = None
        self.context = None
        self.chrome_process = None
        self.owns_chrome = False

    async def _stop_playwright(self) -> None:
        playwright, self._playwright = self._playwright, None
        if playwright is not None:
            await playwright.stop()


def launch_chrome() -> subprocess.Popen[bytes]:
    if not CHROME_EXECUTABLE_PATH.is_file():
        raise RuntimeError(f"Chrome was not found at {CHROME_EXECUTABLE_PATH}. Set CHROME_EXECUTABLE_PATH to its chrome.exe path.")

    PROFILE_PATH.mkdir(parents=True, exist_ok=True)
    arguments = [
        str(CHROME_EXECUTABLE_PATH),
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={PROFILE_PATH}",
        f"--profile-directory={CHROME_PROFILE_DIRECTORY}",
        "--no-first-run",
        "--hide-crash-restore-bubble",
        "--disable-session-crashed-bubble",
        "--start-maximized",
        "about:blank",
    ]
    return subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def debugger_ready(endpoint: str) -> bool:
    def check() -> bool:
        try:
            with urlopen(f"{endpoint}/json/version", timeout=0.5) as response:
                return response.status == 200
        except (OSError, URLError):
            return False

    return await asyncio.to_thread(check)


async def wait_for_debugger(endpoint: str, chrome_process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if chrome_process.poll() is not None:
            raise RuntimeError("Chrome exited before its debugging endpoint became ready.")
        if await debugger_ready(endpoint):
            return
        await asyncio.sleep(0.25)
    chrome_process.kill()
    raise RuntimeError("Chrome debugging endpoint did not become ready.")


async def paste_image(page: Page, composer: Any, image: ImageFile) -> None:
    attachment_baseline = await attachment_counts(page)
    try:
        await page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"], origin=origin_for(page.url)
        )
        await page.evaluate(
            """async ({ base64, mimeType }) => {
                if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") {
                  throw new Error("Image clipboard access is unavailable.");
                }
                const binary = atob(base64);
                const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
                await navigator.clipboard.write([
                  new ClipboardItem({ [mimeType]: new Blob([bytes], { type: mimeType }) }),
                ]);
            }""",
            {"base64": base64.b64encode(image.data).decode("ascii"), "mimeType": image.mime_type},
        )
        await composer.click()
        await page.keyboard.press("Control+V")
        if await wait_for_attachment(page, attachment_baseline, 15_000):
            return
    except PlaywrightError:
        pass

    inputs = page.locator('input[type="file"]')
    for index in range(await inputs.count()):
        try:
            await inputs.nth(index).set_input_files(
                {"name": image.name, "mimeType": image.mime_type, "buffer": image.data}
            )
            if await wait_for_attachment(page, attachment_baseline, 15_000):
                return
        except PlaywrightError:
            pass
    raise RuntimeError("Could not paste or attach the image in Gemini.")


async def send_prompt(page: Page) -> None:
    send = await wait_for_enabled_send_button(page)
    if send is None:
        raise RuntimeError("Gemini's Send button did not become available after attaching the image.")
    await send.click()


async def wait_for_enabled_send_button(page: Page) -> Any | None:
    deadline = time.monotonic() + int(os.environ.get("GEMINI_SEND_READY_TIMEOUT_MS", "60000")) / 1000
    while time.monotonic() < deadline:
        for selector in SEND_SELECTORS:
            matches = page.locator(selector)
            for index in range(await safe_count(matches)):
                button = matches.nth(index)
                if await is_visible(button) and await is_enabled(button):
                    return button
        await page.wait_for_timeout(250)
    return None


async def attachment_counts(page: Page) -> list[int]:
    counts: list[int] = []
    for selector in ATTACHMENT_SELECTORS:
        matches = page.locator(selector)
        counts.append(sum([await is_visible(matches.nth(index)) for index in range(await safe_count(matches))]))
    return counts


async def wait_for_attachment(page: Page, baseline: list[int], timeout_ms: int) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        current = await attachment_counts(page)
        if any(count > baseline[index] for index, count in enumerate(current)):
            return True
        await page.wait_for_timeout(200)
    return False


async def wait_for_response(page: Page, baseline: list[str]) -> str:
    deadline = time.monotonic() + int(os.environ.get("GEMINI_RESPONSE_TIMEOUT_MS", "180000")) / 1000
    latest = ""
    last_change = time.monotonic()
    while time.monotonic() < deadline:
        current = await read_responses(page)
        text = new_response(current, baseline)
        if text and text != latest:
            latest, last_change = text, time.monotonic()
        if latest and not await any_visible(page, STOP_SELECTORS) and time.monotonic() - last_change > 2:
            return latest
        await page.wait_for_timeout(250)
    raise RuntimeError("Gemini did not finish responding before the timeout.")


async def read_responses(page: Page) -> list[str]:
    for selector in RESPONSE_SELECTORS:
        try:
            texts = [text.strip() for text in await page.locator(selector).all_inner_texts() if text.strip()]
            if texts:
                return texts
        except PlaywrightError:
            continue
    return []


def new_response(current: list[str], baseline: list[str]) -> str:
    latest = current[-1] if current else ""
    previous = baseline[-1] if baseline else ""
    return latest if len(current) > len(baseline) or latest != previous else ""


async def find_first_visible(page: Page, selectors: list[str], timeout_ms: int) -> Any | None:
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


async def safe_count(locator: Any) -> int:
    try:
        return await locator.count()
    except PlaywrightError:
        return 0


async def is_visible(locator: Any) -> bool:
    try:
        return await locator.is_visible()
    except PlaywrightError:
        return False


async def is_enabled(locator: Any) -> bool:
    try:
        return await locator.is_enabled()
    except PlaywrightError:
        return False


def hostname_for(value: str) -> str:
    from urllib.parse import urlparse

    return urlparse(value).hostname or ""


def origin_for(value: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(value)
    return f"{parsed.scheme}://{parsed.netloc}"


gemini = GeminiOcrBrowser()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await gemini.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.post("/gemini/ocr")
async def gemini_ocr(request: Request, image: UploadFile = File(...)) -> dict[str, str]:
    content_length = int(request.headers.get("content-length", "0"))
    if content_length > MAX_IMAGE_BYTES + 1024 * 1024:
        raise HTTPException(413, "Image must be 25 MB or smaller.")
    if not (image.content_type or "").startswith("image/"):
        raise HTTPException(400, "The image field must contain an image file.")

    data = await image.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image must be 25 MB or smaller.")
    try:
        return await gemini.ocr(ImageFile(image.filename or "image", image.content_type, data))
    except Exception as error:
        raise HTTPException(500, str(error)) from error
    finally:
        await image.close()


def shutdown_browser() -> None:
    if gemini.chrome_process is not None and gemini.owns_chrome and gemini.chrome_process.poll() is None:
        gemini.chrome_process.kill()


atexit.register(shutdown_browser)
