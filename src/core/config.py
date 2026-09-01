from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_DIRECTORY_NAME = "eStampAutomation"
LEGACY_GEMINI_PROFILE = Path(r"D:\Projects\agent-orchestrator\.playwright-chrome-profile")
DEFAULT_SMS_SERVER_URL = "https://sms-server.compitcom.in"


@dataclass
class TabConfig:
    """Persisted, non-secret settings for one automation tab."""

    tab_id: int = 1
    profile_number: int = 1
    enabled: bool = True
    last_download_path: str = ""
    last_mode: str = "assisted"
    last_portal_browser_path: str = ""
    custom_portal_browser_path: str = ""
    custom_portal_browser_engine: str = ""
    portal_profile_path: str = ""
    sms_user_id: str = ""
    sms_server_url: str = DEFAULT_SMS_SERVER_URL
    payment_trigger_url: str = ""
    payment_trigger_method: str = "GET"
    captcha_copy_mode: str = "direct_copy"
    ocr_engine: str = "paddleocr"
    ocr_enabled: bool = True
    last_article: str = ""
    last_csv_path: str = ""
    save_captcha_images: bool = True
    fresh_browser_per_unit: bool = False
    retry_egras_otp_once: bool = True

    @property
    def display_name(self) -> str:
        return f"ID {self.tab_id}"

    @classmethod
    def new(cls, tab_id: int, profile_number: int) -> TabConfig:
        """Create a blank tab with the app's normal system defaults."""
        return cls(
            tab_id=tab_id,
            profile_number=profile_number,
            last_download_path=str(default_download_directory()),
            portal_profile_path=str(default_portal_profile_path(profile_number)),
        )

    @classmethod
    def from_dict(cls, values: dict[str, Any], *, tab_id: int, profile_number: int) -> TabConfig:
        defaults = cls.new(tab_id, profile_number)
        allowed = set(asdict(defaults))
        cleaned = {key: value for key, value in values.items() if key in allowed}
        cleaned["tab_id"] = tab_id
        cleaned["profile_number"] = profile_number
        return cls(**cleaned)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def copy_run_settings_from(self, source: TabConfig) -> None:
        """Copy another tab's settings without sharing its browser profile identity."""
        preserved = {"tab_id", "profile_number", "portal_profile_path", "enabled"}
        for name in _TAB_FIELDS:
            if name not in preserved:
                setattr(self, name, getattr(source, name))


_TAB_FIELDS = tuple(asdict(TabConfig()).keys())


@dataclass
class AppConfig:
    """Application settings, with tab-specific values stored under ``tabs``."""

    chrome_executable: str = ""
    chrome_profile_path: str = ""
    gemini_verified: bool = False
    debug_port: int = 9347
    transaction_export_path: str = ""
    tabs: list[TabConfig] = field(default_factory=lambda: [TabConfig.new(1, 1)])
    next_profile_number: int = 2

    # Deprecated flat fields. ConfigStore writes only the tab representation.
    enabled: bool = True
    last_download_path: str = ""
    last_mode: str = "assisted"
    last_portal_browser_path: str = ""
    custom_portal_browser_path: str = ""
    custom_portal_browser_engine: str = ""
    portal_profile_path: str = ""
    sms_user_id: str = ""
    sms_server_url: str = DEFAULT_SMS_SERVER_URL
    payment_trigger_url: str = ""
    payment_trigger_method: str = "GET"
    captcha_copy_mode: str = "direct_copy"
    ocr_engine: str = "paddleocr"
    ocr_enabled: bool = True
    last_article: str = ""
    last_csv_path: str = ""
    save_captcha_images: bool = True
    fresh_browser_per_unit: bool = False
    retry_egras_otp_once: bool = True

    def __post_init__(self) -> None:
        self._ensure_tab_one()
        self._ensure_unique_profiles()
        first = self.tabs[0]
        # Support callers still constructing AppConfig with the old flat keys.
        for name in _TAB_FIELDS:
            if name in {"tab_id", "profile_number"}:
                continue
            value = getattr(self, name)
            default = _legacy_default(name)
            if value != default and value != getattr(first, name):
                setattr(first, name, value)
        self._sync_legacy_fields_from_tab()
        self._normalize_profile_counter()

    def _ensure_tab_one(self) -> None:
        valid = [
            tab
            for tab in self.tabs
            if isinstance(tab, TabConfig) and tab.tab_id > 0 and tab.profile_number > 0
        ]
        if not any(tab.tab_id == 1 for tab in valid):
            valid.insert(0, TabConfig.new(1, 1))
        self.tabs = sorted(valid, key=lambda tab: tab.tab_id)

    def _normalize_profile_counter(self) -> None:
        used = {tab.profile_number for tab in self.tabs}
        candidate = 2
        while candidate in used:
            candidate += 1
        self.next_profile_number = candidate

    def _ensure_unique_profiles(self) -> None:
        """Repair duplicate profile identities from malformed or hand-edited settings."""
        used_numbers: set[int] = set()
        used_paths: set[str] = set()
        next_number = max((tab.profile_number for tab in self.tabs), default=0) + 1
        for tab in self.tabs:
            profile_path = tab.portal_profile_path or str(
                default_portal_profile_path(tab.profile_number)
            )
            path_key = os.path.normcase(os.path.abspath(profile_path))
            if tab.profile_number in used_numbers or path_key in used_paths:
                while next_number in used_numbers:
                    next_number += 1
                tab.profile_number = next_number
                tab.portal_profile_path = str(default_portal_profile_path(next_number))
                next_number += 1
            elif not tab.portal_profile_path:
                tab.portal_profile_path = profile_path
            used_numbers.add(tab.profile_number)
            used_paths.add(os.path.normcase(os.path.abspath(tab.portal_profile_path)))

    def _sync_legacy_fields_from_tab(self) -> None:
        first = self.get_tab(1)
        for name in _TAB_FIELDS:
            if name not in {"tab_id", "profile_number"}:
                setattr(self, name, getattr(first, name))

    def sync_legacy_fields_to_tab(self) -> None:
        first = self.get_tab(1)
        for name in _TAB_FIELDS:
            if name not in {"tab_id", "profile_number"}:
                setattr(first, name, getattr(self, name))

    def get_tab(self, tab_id: int = 1) -> TabConfig:
        for tab in self.tabs:
            if tab.tab_id == tab_id:
                return tab
        raise KeyError(f"Unknown tab ID: {tab_id}")

    def create_tab(self) -> TabConfig:
        used_ids = {tab.tab_id for tab in self.tabs}
        tab_id = 2
        while tab_id in used_ids:
            tab_id += 1

        used_profiles = {tab.profile_number for tab in self.tabs}
        profile_number = tab_id
        while profile_number in used_profiles:
            profile_number += 1

        # Removed profile directories stay on disk. Reusing the first free ID
        # reconnects a replacement tab to that ID's existing browser profile.
        tab = TabConfig.new(tab_id, profile_number)
        self.tabs.append(tab)
        self._normalize_profile_counter()
        return tab

    new_tab = create_tab

    @property
    def profile_path(self) -> Path:
        if self.chrome_profile_path:
            return Path(self.chrome_profile_path)
        return app_data_directory() / "chrome-profile"


def _legacy_default(name: str) -> Any:
    return getattr(TabConfig(), name)


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_directory() / "settings.json"

    def load(self) -> AppConfig:
        settings_exist = self.path.is_file()
        values: dict[str, Any] = {}
        if settings_exist:
            try:
                parsed = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    values = parsed
            except (OSError, ValueError, TypeError):
                values = {}

        tabs = self._load_tabs(values)
        config = AppConfig(
            chrome_executable=values.get("chrome_executable", ""),
            chrome_profile_path=values.get("chrome_profile_path", ""),
            gemini_verified=values.get("gemini_verified", False),
            debug_port=values.get("debug_port", 9347),
            transaction_export_path=values.get("transaction_export_path", ""),
            tabs=tabs,
            next_profile_number=values.get(
                "next_profile_number", max(tab.profile_number for tab in tabs) + 1
            ),
        )
        if not config.chrome_executable:
            detected = detect_chrome()
            config.chrome_executable = str(detected) if detected else ""
        if not config.chrome_profile_path:
            profile = LEGACY_GEMINI_PROFILE if not settings_exist and LEGACY_GEMINI_PROFILE.is_dir() else (
                app_data_directory() / "chrome-profile"
            )
            config.chrome_profile_path = str(profile)
        for tab in config.tabs:
            if not tab.last_download_path:
                tab.last_download_path = str(default_download_directory())
            if not tab.sms_server_url:
                tab.sms_server_url = DEFAULT_SMS_SERVER_URL
            if not tab.portal_profile_path:
                tab.portal_profile_path = str(default_portal_profile_path(tab.profile_number))
        config._sync_legacy_fields_from_tab()
        if settings_exist and "tabs" not in values:
            self.save(config)
        return config

    def _load_tabs(self, values: dict[str, Any]) -> list[TabConfig]:
        raw_tabs = values.get("tabs")
        if isinstance(raw_tabs, list):
            result: list[TabConfig] = []
            seen: set[int] = set()
            for raw in raw_tabs:
                if not isinstance(raw, dict):
                    continue
                try:
                    tab_id = int(raw.get("tab_id", 0))
                    profile_number = int(raw.get("profile_number", 0))
                except (TypeError, ValueError):
                    continue
                if tab_id <= 0 or profile_number <= 0 or tab_id in seen:
                    continue
                seen.add(tab_id)
                result.append(TabConfig.from_dict(raw, tab_id=tab_id, profile_number=profile_number))
            if result:
                return result
        return [TabConfig.from_dict(values, tab_id=1, profile_number=1)]

    def save(self, config: AppConfig) -> None:
        config._ensure_tab_one()
        # ``tabs`` is the source of truth in the multi-run UI. Constructor-time
        # legacy values are already migrated by AppConfig.__post_init__.
        config._sync_legacy_fields_from_tab()
        config._normalize_profile_counter()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "chrome_executable": config.chrome_executable,
            "chrome_profile_path": config.chrome_profile_path,
            "gemini_verified": config.gemini_verified,
            "debug_port": config.debug_port,
            "transaction_export_path": config.transaction_export_path,
            "tabs": [tab.to_dict() for tab in config.tabs],
            "next_profile_number": config.next_profile_number,
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def create_tab(self, config: AppConfig) -> TabConfig:
        tab = config.create_tab()
        self.save(config)
        return tab


def app_data_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "Compitcom" / APP_DIRECTORY_NAME


def default_download_directory() -> Path:
    """Return the normal per-user Downloads folder used as the app default."""
    return Path.home() / "Downloads"


def default_portal_profile_path(profile_number: int) -> Path:
    return app_data_directory() / "portal-profiles" / f"profile-{profile_number}"


def detect_chrome() -> Path | None:
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.extend(
            (
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            )
        )
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
