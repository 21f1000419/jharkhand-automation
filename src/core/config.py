from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

APP_DIRECTORY_NAME = "eStampAutomation"
LEGACY_GEMINI_PROFILE = Path(r"D:\Projects\agent-orchestrator\.playwright-chrome-profile")
DEFAULT_SMS_SERVER_URL = "https://sms-server.compitcom.in"


@dataclass
class AppConfig:
    chrome_executable: str = ""
    chrome_profile_path: str = ""
    last_download_path: str = ""
    last_mode: str = "assisted"
    last_portal_browser_path: str = ""
    custom_portal_browser_path: str = ""
    custom_portal_browser_engine: str = ""
    sms_user_id: str = ""
    sms_server_url: str = DEFAULT_SMS_SERVER_URL
    payment_trigger_url: str = ""
    payment_trigger_method: str = "GET"
    captcha_copy_mode: str = "direct_copy"
    ocr_engine: str = "ddddocr"
    gemini_verified: bool = False
    debug_port: int = 9347
    last_article: str = ""
    last_csv_path: str = ""

    @property
    def profile_path(self) -> Path:
        if self.chrome_profile_path:
            return Path(self.chrome_profile_path)
        return app_data_directory() / "chrome-profile"


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_directory() / "settings.json"

    def load(self) -> AppConfig:
        config = AppConfig()
        settings_exist = self.path.is_file()
        if settings_exist:
            try:
                values = json.loads(self.path.read_text(encoding="utf-8"))
                allowed = set(asdict(config))
                config = AppConfig(**{key: value for key, value in values.items() if key in allowed})
            except (OSError, ValueError, TypeError):
                config = AppConfig()

        if not config.chrome_executable:
            detected = detect_chrome()
            config.chrome_executable = str(detected) if detected else ""
        if not config.chrome_profile_path:
            profile = LEGACY_GEMINI_PROFILE if not settings_exist and LEGACY_GEMINI_PROFILE.is_dir() else (
                app_data_directory() / "chrome-profile"
            )
            config.chrome_profile_path = str(profile)
        if not config.last_download_path:
            config.last_download_path = str(default_download_directory())
        return config

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


def app_data_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "Compitcom" / APP_DIRECTORY_NAME


def default_download_directory() -> Path:
    """Return the normal per-user Downloads folder used as the app default."""
    return Path.home() / "Downloads"


def detect_chrome() -> Path | None:
    candidates: list[Path] = []
    try:
        import winreg

        registry_locations = (
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
            ),
        )
        for hive, subkey in registry_locations:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    candidates.append(Path(value))
            except OSError:
                continue
    except ImportError:
        pass

    for variable in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")

    return next((candidate for candidate in candidates if candidate.is_file()), None)
