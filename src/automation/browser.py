from __future__ import annotations

import asyncio
import atexit
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from core.models import BrowserEngine, PortalBrowser

_PROCESS_LOCK = threading.Lock()
_TRACKED_PROCESSES: set[subprocess.Popen[bytes]] = set()


def register_process(process: subprocess.Popen[bytes]) -> None:
    with _PROCESS_LOCK:
        _TRACKED_PROCESSES.add(process)


def unregister_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is not None:
        with _PROCESS_LOCK:
            _TRACKED_PROCESSES.discard(process)


def terminate_process_tree_sync(process: subprocess.Popen[bytes] | int | None) -> None:
    """Close a process and all its child processes synchronously."""
    if process is None:
        return
    pid = process.pid if isinstance(process, subprocess.Popen) else process
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        if isinstance(process, subprocess.Popen):
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def cleanup_all_spawned_processes() -> None:
    """Synchronous emergency cleanup for all tracked browser processes."""
    with _PROCESS_LOCK:
        processes = list(_TRACKED_PROCESSES)
        _TRACKED_PROCESSES.clear()
    for proc in processes:
        try:
            if proc.poll() is None:
                terminate_process_tree_sync(proc)
        except Exception:
            pass


atexit.register(cleanup_all_spawned_processes)


class BrowserSession:
    """Owns the dedicated Chrome profile and its CDP connection."""

    def __init__(
        self,
        chrome_executable: Path,
        profile_path: Path,
        debug_port: int,
        on_disconnect: Callable[[], None] | None = None,
        *,
        headless: bool = False,
    ) -> None:
        self.chrome_executable = chrome_executable
        self.profile_path = profile_path
        self.debug_port = debug_port
        self.on_disconnect = on_disconnect
        self.headless = headless
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.owns_chrome = False
        self.closing = False

    async def start(self) -> BrowserContext:
        if self.context is not None and self.browser is not None and self.browser.is_connected():
            return self.context
        if not self.chrome_executable.is_file():
            raise RuntimeError(
                f"Google Chrome was not found at {self.chrome_executable}. Choose chrome.exe in setup."
            )

        endpoint = f"http://127.0.0.1:{self.debug_port}"
        if not await debugger_ready(endpoint):
            self._launch_chrome()
            try:
                await wait_for_debugger(endpoint, self.process)
            except Exception:
                if self.process is not None:
                    if self.process.poll() is None:
                        await terminate_process_tree(self.process)
                    unregister_process(self.process)
                    self.process = None
                    self.owns_chrome = False
                raise

        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.connect_over_cdp(endpoint)
            if not self.browser.contexts:
                raise RuntimeError("Chrome did not expose a browser context.")
            self.context = self.browser.contexts[0]
            self.browser.on("disconnected", self._disconnected)
            return self.context
        except Exception:
            await self._stop_playwright()
            if self.owns_chrome and self.process is not None:
                if self.process.poll() is None:
                    await terminate_process_tree(self.process)
                unregister_process(self.process)
                self.process = None
                self.owns_chrome = False
            raise

    async def page_for_host(self, hostname: str, *, create_url: str) -> Page:
        context = await self.start()
        for page in context.pages:
            if not page.is_closed() and hostname in page.url:
                return page
        page = await context.new_page()
        await page.goto(create_url, wait_until="domcontentloaded", timeout=60_000)
        return page

    async def new_portal_page(
        self, initial_url: str = "about:blank", *, timeout_ms: int = 120_000
    ) -> Page:
        context = await self.start()
        page = await context.new_page()
        await page.goto(initial_url, wait_until="domcontentloaded", timeout=timeout_ms)
        return page

    async def close(self) -> None:
        self.closing = True
        browser, process = self.browser, self.process
        owns_chrome = self.owns_chrome
        self.browser = None
        self.context = None
        self.process = None
        self.owns_chrome = False

        if browser is not None and browser.is_connected():
            try:
                cdp = await browser.new_browser_cdp_session()
                await cdp.send("Browser.close")
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

        if owns_chrome and process is not None and process.poll() is None:
            await terminate_process_tree(process)
        unregister_process(process)

        await self._stop_playwright()
        self.closing = False

    async def detach(self) -> None:
        self.browser = None
        self.context = None
        self.process = None
        self.owns_chrome = False
        await self._stop_playwright()

    def _launch_chrome(self) -> None:
        self.profile_path.mkdir(parents=True, exist_ok=True)
        arguments = [
            str(self.chrome_executable),
            f"--remote-debugging-port={self.debug_port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={self.profile_path}",
            "--profile-directory=Default",
            "--no-first-run",
            "--hide-crash-restore-bubble",
            "--disable-session-crashed-bubble",
            "--window-size=1280,1000",
            "about:blank",
        ]
        if self.headless:
            arguments.append("--headless=new")
        else:
            arguments.append("--start-maximized")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            arguments,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        self.owns_chrome = True
        register_process(self.process)

    def _disconnected(self, _browser: Browser) -> None:
        process = self.process
        owns_chrome = self.owns_chrome
        is_headless = self.headless
        self.browser = None
        self.context = None
        self.process = None
        self.owns_chrome = False

        if is_headless and owns_chrome and process is not None:
            if process.poll() is None:
                terminate_process_tree_sync(process)
            unregister_process(process)

        if not self.closing and self.on_disconnect is not None:
            self.on_disconnect()

    async def _stop_playwright(self) -> None:
        playwright, self.playwright = self.playwright, None
        if playwright is not None:
            await playwright.stop()


async def debugger_ready(endpoint: str) -> bool:
    def check() -> bool:
        try:
            with urlopen(f"{endpoint}/json/version", timeout=0.6) as response:
                return int(response.status) == 200
        except (OSError, URLError):
            return False

    return await asyncio.to_thread(check)


async def wait_for_debugger(
    endpoint: str, process: subprocess.Popen[bytes] | None, timeout_seconds: float = 20
) -> None:
    if process is None:
        raise RuntimeError("Chrome did not start.")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Chrome exited before automation could connect.")
        if await debugger_ready(endpoint):
            return
        await asyncio.sleep(0.25)
    terminate_process_tree_sync(process)
    unregister_process(process)
    raise RuntimeError("Chrome's automation connection did not become ready.")


async def terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Close Chrome plus its child processes when this app launched it."""
    await asyncio.to_thread(terminate_process_tree_sync, process)


class PortalBrowserSession:
    """A visible fresh portal session in the browser selected by the user."""

    def __init__(self, choice: PortalBrowser, on_disconnect: Callable[[], None]) -> None:
        self.choice = choice
        self.on_disconnect = on_disconnect
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.closing = False

    @property
    def is_active(self) -> bool:
        return self.browser is not None and self.browser.is_connected()

    async def new_portal_page(
        self, initial_url: str = "about:blank", *, timeout_ms: int = 120_000
    ) -> Page:
        context = await self.start()
        page = await context.new_page()
        await page.goto(initial_url, wait_until="domcontentloaded", timeout=timeout_ms)
        return page

    async def start(self) -> BrowserContext:
        if self.context is not None and self.browser is not None and self.browser.is_connected():
            return self.context
        if not self.choice.executable.is_file():
            raise RuntimeError(f"{self.choice.name} was not found at {self.choice.executable}.")
        self.playwright = await async_playwright().start()
        try:
            browser_type = (
                self.playwright.firefox
                if self.choice.engine == BrowserEngine.FIREFOX
                else self.playwright.chromium
            )
            if self.choice.engine == BrowserEngine.FIREFOX:
                bundled_firefox = Path(browser_type.executable_path)
                if not bundled_firefox.is_file():
                    raise RuntimeError(
                        "Firefox-based portal automation requires Playwright Firefox. "
                        "Run '.venv\\Scripts\\playwright.exe install firefox' once, then try again."
                    )
            launch_args = ["--start-maximized"] if self.choice.engine == BrowserEngine.CHROMIUM else []
            launch_options: dict[str, object] = {"headless": False, "args": launch_args}
            if self.choice.engine == BrowserEngine.CHROMIUM:
                launch_options["executable_path"] = str(self.choice.executable)
            self.browser = await browser_type.launch(**launch_options)  # type: ignore[arg-type]
            self.browser.on("disconnected", self._disconnected)
            self.context = await self.browser.new_context(accept_downloads=True, no_viewport=True)
            return self.context
        except Exception:
            if self.browser is not None:
                try:
                    await self.browser.close()
                except Exception:
                    pass
                self.browser = None
            await self._stop_playwright()
            raise

    async def close(self) -> None:
        self.closing = True
        browser, self.browser = self.browser, None
        self.context = None
        try:
            if browser is not None and browser.is_connected():
                await browser.close()
        except Exception:
            pass
        await self._stop_playwright()
        self.closing = False

    def _disconnected(self, _browser: Browser) -> None:
        self.browser = None
        self.context = None
        if not self.closing:
            self.on_disconnect()

    async def _stop_playwright(self) -> None:
        playwright, self.playwright = self.playwright, None
        if playwright is not None:
            await playwright.stop()
