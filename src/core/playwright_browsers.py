"""Persistent storage and installation support for Playwright browsers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.config import app_data_directory


def browser_install_directory() -> Path:
    """Return the per-user location that survives moving or updating the EXE."""
    return app_data_directory() / "playwright-browsers"


def configure_browser_install_directory() -> Path:
    """Make every Playwright process use the application-owned browser location."""
    directory = browser_install_directory()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(directory)
    return directory


def managed_firefox_is_installed() -> bool:
    """Return whether the Playwright Firefox executable exists in the app folder."""
    return any(browser_install_directory().glob("firefox-*/firefox/firefox.exe"))


def install_managed_firefox() -> None:
    """Download the Playwright Firefox build used by portal automation."""
    directory = configure_browser_install_directory()
    directory.mkdir(parents=True, exist_ok=True)

    # The Playwright driver is bundled by PyInstaller, including its Node runtime.
    # Calling its CLI works in both a source checkout and the one-file executable.
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    node, cli = compute_driver_executable()
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        [node, cli, "install", "firefox"],
        capture_output=True,
        text=True,
        env=get_driver_env(),
        creationflags=creation_flags,
        check=False,
    )
    if result.returncode:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(details or "Playwright could not download managed Firefox.")
