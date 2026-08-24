from __future__ import annotations

import asyncio
import atexit
import ctypes
import os
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from core.config import app_data_directory
from core.controls import RunControls
from core.models import BrowserEngine, PortalBrowser
from core.playwright_browsers import configure_browser_install_directory

# Must be set before Playwright creates its driver.  The folder is independent
# of the EXE's location, so a copy placed on the Desktop still finds Firefox.
configure_browser_install_directory()

from playwright.async_api import (  # noqa: E402
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

_PROCESS_LOCK = threading.Lock()
_TRACKED_PROCESSES: set[subprocess.Popen[bytes]] = set()
_PORTAL_LAUNCH_LOCK = asyncio.Lock()
_SW_RESTORE = 9


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
            with suppress(OSError):
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


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
            with suppress(Exception):
                await browser.close()

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
        # Headless mode commented out for non-headless only operation
        # if self.headless:
        #     arguments.append("--headless=new")
        # else:
        #     arguments.append("--start-maximized")
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
        # process = self.process
        # owns_chrome = self.owns_chrome
        # is_headless = self.headless
        self.browser = None
        self.context = None
        self.process = None
        self.owns_chrome = False

        # Headless process cleanup commented out for non-headless mode
        # if is_headless and owns_chrome and process is not None:
        #     if process.poll() is None:
        #         terminate_process_tree_sync(process)
        #     unregister_process(process)

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
    """A visible, disk-backed portal session in the browser selected by the user."""

    def __init__(
        self,
        choice: PortalBrowser,
        on_disconnect: Callable[[PortalBrowserSession], None],
        profile_directory: Path | None = None,
    ) -> None:
        self.choice = choice
        self.on_disconnect = on_disconnect
        self.profile_directory = profile_directory or (
            app_data_directory() / "portal-browser-profiles" / uuid.uuid4().hex
        )
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self._launch_page: Page | None = None
        self.window_handle: int | None = None
        self.closing = False
        self.closed_by_owner = False

    @property
    def is_active(self) -> bool:
        return self.context is not None and not self.closed_by_owner

    async def new_portal_page(
        self, initial_url: str = "about:blank", *, timeout_ms: int = 120_000
    ) -> Page:
        context = await self.start()
        page, self._launch_page = self._launch_page, None
        if page is None or page.is_closed():
            page = await context.new_page()
        await page.goto(initial_url, wait_until="domcontentloaded", timeout=timeout_ms)
        return page

    async def start(self) -> BrowserContext:
        if self.context is not None and not self.closed_by_owner:
            return self.context
        if self.choice.engine == BrowserEngine.CHROMIUM and not self.choice.executable.is_file():
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
                        "Choose 'Download managed Firefox' from the main menu, then try again."
                    )
            launch_args = ["--start-maximized"] if self.choice.engine == BrowserEngine.CHROMIUM else []
            launch_options: dict[str, Any] = {
                "headless": False,
                "args": launch_args,
                "accept_downloads": True,
                "no_viewport": True,
            }
            if self.choice.engine == BrowserEngine.CHROMIUM:
                launch_options["executable_path"] = str(self.choice.executable)
            self.profile_directory.mkdir(parents=True, exist_ok=True)
            self.closed_by_owner = False
            async with _PORTAL_LAUNCH_LOCK:
                self.context = await browser_type.launch_persistent_context(
                    str(self.profile_directory), **launch_options
                )
                self.browser = self.context.browser
                if self.browser is not None:
                    self.browser.on("disconnected", self._disconnected)
                self.context.on("close", self._context_closed)
                await self._capture_window_handle()
            return self.context
        except Exception:
            if self.context is not None:
                with suppress(Exception):
                    await self.context.close()
            self.browser = None
            self.context = None
            self.window_handle = None
            await self._stop_playwright()
            raise

    async def close(self) -> None:
        self.closing = True
        self.closed_by_owner = True
        context, self.context = self.context, None
        self.browser = None
        self.window_handle = None
        self._launch_page = None
        try:
            if context is not None:
                await context.close()
        except Exception:
            pass
        await self._stop_playwright()
        self.closing = False

    async def focus(self, page: Page, controls: RunControls) -> None:
        """Restore and activate this window, retrying while the session remains alive."""
        while True:
            await controls.ensure_not_stopped()
            if page.is_closed() or not self.is_active:
                raise RuntimeError("The portal browser was closed while requesting payment focus.")
            await page.bring_to_front()
            handle = self.window_handle
            if handle is None or os.name != "nt":
                return
            if await asyncio.to_thread(_restore_and_activate_window, handle):
                return
            await asyncio.sleep(0.5)

    def _disconnected(self, _browser: Browser) -> None:
        self._mark_disconnected()

    def _context_closed(self, _context: BrowserContext) -> None:
        self._mark_disconnected()

    def _mark_disconnected(self) -> None:
        self.browser = None
        self.context = None
        self.window_handle = None
        if not self.closed_by_owner:
            self.on_disconnect(self)

    async def _capture_window_handle(self) -> None:
        if os.name != "nt":
            return
        if self.context is None:
            raise RuntimeError("The portal browser context closed during launch.")
        marker = f"eStamp portal {uuid.uuid4().hex}"
        page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        await page.bring_to_front()
        await page.evaluate("title => { document.title = title; }", marker)
        handle = await asyncio.to_thread(_wait_for_window_handle, marker, 10.0)
        if handle is None:
            raise RuntimeError("Could not identify the portal browser window during launch.")
        self.window_handle = handle
        self._launch_page = page

    async def _stop_playwright(self) -> None:
        playwright, self.playwright = self.playwright, None
        if playwright is not None:
            await playwright.stop()


def _wait_for_window_handle(marker: str, timeout_seconds: float) -> int | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        handle = _find_window_with_title(marker)
        if handle is not None:
            return handle
        time.sleep(0.1)
    return None


def _find_window_with_title(marker: str) -> int | None:
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    match: list[int] = []
    enum_windows = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def inspect(hwnd: int, _param: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if marker in title.value:
            match.append(hwnd)
            return False
        return True

    user32.EnumWindows(enum_windows(inspect), 0)
    return match[0] if match else None


def _restore_and_activate_window(handle: int) -> bool:
    if os.name != "nt":
        return True
    user32 = ctypes.windll.user32
    if not user32.IsWindow(handle):
        return False
    if user32.IsIconic(handle):
        user32.ShowWindow(handle, _SW_RESTORE)
    user32.BringWindowToTop(handle)
    user32.SetForegroundWindow(handle)
    if int(user32.GetForegroundWindow()) == handle:
        return True

    # Windows sometimes denies SetForegroundWindow to a background worker.
    # Temporarily join the foreground input queue, perform the activation, and
    # detach immediately. The browser is never left always-on-top.
    kernel32 = ctypes.windll.kernel32
    foreground = int(user32.GetForegroundWindow())
    foreground_thread = int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
    current_thread = int(kernel32.GetCurrentThreadId())
    attached = bool(
        foreground_thread
        and foreground_thread != current_thread
        and user32.AttachThreadInput(current_thread, foreground_thread, True)
    )
    try:
        user32.ShowWindow(handle, _SW_RESTORE)
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
    return int(user32.GetForegroundWindow()) == handle
