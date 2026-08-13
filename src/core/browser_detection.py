from __future__ import annotations

import os
from pathlib import Path

from core.models import BrowserEngine, PortalBrowser

SUPPORTED_BROWSERS = (
    ("Google Chrome", "chrome.exe", BrowserEngine.CHROMIUM, ("Google", "Chrome", "Application")),
    ("Microsoft Edge", "msedge.exe", BrowserEngine.CHROMIUM, ("Microsoft", "Edge", "Application")),
    ("Brave", "brave.exe", BrowserEngine.CHROMIUM, ("BraveSoftware", "Brave-Browser", "Application")),
    ("Opera", "opera.exe", BrowserEngine.CHROMIUM, ("Programs", "Opera")),
    ("Opera GX", "opera.exe", BrowserEngine.CHROMIUM, ("Programs", "Opera GX")),
    ("Vivaldi", "vivaldi.exe", BrowserEngine.CHROMIUM, ("Vivaldi", "Application")),
    ("Yandex Browser", "browser.exe", BrowserEngine.CHROMIUM, ("Yandex", "YandexBrowser", "Application")),
    ("Firefox (managed automation)", "firefox.exe", BrowserEngine.FIREFOX, ("Mozilla Firefox",)),
)


def detect_supported_browsers() -> list[PortalBrowser]:
    detected: list[PortalBrowser] = []
    for name, executable_name, engine, relative in SUPPORTED_BROWSERS:
        executable = next(
            (path for path in candidate_paths(executable_name, relative) if path.is_file()), None
        )
        if executable is not None:
            detected.append(PortalBrowser(name, executable, engine))
    return detected


def candidate_paths(executable_name: str, relative: tuple[str, ...]) -> list[Path]:
    candidates = registry_paths(executable_name)
    roots = [
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
    ]
    candidates.extend(Path(root).joinpath(*relative, executable_name) for root in roots if root)
    return candidates


def registry_paths(executable_name: str) -> list[Path]:
    values: list[Path] = []
    try:
        import winreg

        subkeys = (
            rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}",
            rf"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}",
        )
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for subkey in subkeys:
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        value, _ = winreg.QueryValueEx(key, "")
                        values.append(Path(value))
                except OSError:
                    continue
    except ImportError:
        pass
    return values
