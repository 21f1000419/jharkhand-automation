from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_DIRECTORY_NAME = "eStampAutomation"
CONFIG_SCHEMA_VERSION = 3
LEGACY_GEMINI_PROFILE = Path(r"D:\Projects\agent-orchestrator\.playwright-chrome-profile")
DEFAULT_SMS_SERVER_URL = "https://sms-server.compitcom.in"


@dataclass
class GlobalRunConfig:
    """Run settings shared by every ID."""

    last_download_path: str = ""
    last_mode: str = "assisted"
    last_portal_browser_path: str = ""
    custom_portal_browser_path: str = ""
    custom_portal_browser_engine: str = ""
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

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> GlobalRunConfig:
        defaults = cls()
        allowed = set(asdict(defaults))
        cleaned = {key: value for key, value in values.items() if key in allowed}
        result = cls(**cleaned)
        if not result.last_download_path:
            result.last_download_path = str(default_download_directory())
        if not result.sms_server_url:
            result.sms_server_url = DEFAULT_SMS_SERVER_URL
        result.payment_trigger_method = (
            "POST" if result.payment_trigger_method.strip().upper() == "POST" else "GET"
        )
        return result

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TabConfig:
    """Persisted, non-secret settings that remain specific to one ID."""

    tab_id: int = 1
    profile_number: int = 1
    enabled: bool = True
    portal_profile_path: str = ""
    sms_user_id: str = ""
    browser_count: int = 1

    @property
    def display_name(self) -> str:
        return f"ID {self.tab_id}"

    @classmethod
    def new(cls, tab_id: int, profile_number: int) -> TabConfig:
        return cls(
            tab_id=tab_id,
            profile_number=profile_number,
            portal_profile_path=str(default_portal_profile_path(profile_number)),
        )

    @classmethod
    def from_dict(cls, values: dict[str, Any], *, tab_id: int, profile_number: int) -> TabConfig:
        defaults = cls.new(tab_id, profile_number)
        allowed = set(asdict(defaults))
        cleaned = {key: value for key, value in values.items() if key in allowed}
        cleaned["tab_id"] = tab_id
        cleaned["profile_number"] = profile_number
        try:
            cleaned["browser_count"] = min(20, max(1, int(cleaned.get("browser_count", 1))))
        except (TypeError, ValueError):
            cleaned["browser_count"] = 1
        return cls(**cleaned)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def reset_portal_profile(self) -> tuple[Path, str]:
        profile_root = (app_data_directory() / "portal-profiles").resolve()
        expected_path = default_portal_profile_path(self.profile_number).resolve()
        try:
            expected_path.relative_to(profile_root)
        except ValueError as error:
            raise OSError("The generated portal profile path is outside the local profile folder.") from error
        configured_path = self.portal_profile_path.strip()
        configured_key = os.path.normcase(os.path.abspath(configured_path)) if configured_path else ""
        expected_key = os.path.normcase(os.path.abspath(expected_path))
        if configured_key != expected_key:
            self.portal_profile_path = str(expected_path)
            return expected_path, "path_reset"
        if expected_path.is_dir():
            shutil.rmtree(expected_path)
            return expected_path, "deleted"
        return expected_path, "not_found"


@dataclass
class AppConfig:
    """Application settings with one global run configuration and multiple IDs."""

    chrome_executable: str = ""
    chrome_profile_path: str = ""
    gemini_verified: bool = False
    debug_port: int = 9347
    transaction_export_path: str = ""
    run_config: GlobalRunConfig = field(default_factory=GlobalRunConfig)
    tabs: list[TabConfig] = field(default_factory=lambda: [TabConfig.new(1, 1)])
    next_profile_number: int = 2

    def __post_init__(self) -> None:
        self._ensure_tab_one()
        self._ensure_unique_profiles()
        if not self.run_config.last_download_path:
            self.run_config.last_download_path = str(default_download_directory())
        if not self.run_config.sms_server_url:
            self.run_config.sms_server_url = DEFAULT_SMS_SERVER_URL
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
        used_numbers: set[int] = set()
        used_paths: set[str] = set()
        next_number = max((tab.profile_number for tab in self.tabs), default=0) + 1
        for tab in self.tabs:
            profile_path = tab.portal_profile_path or str(default_portal_profile_path(tab.profile_number))
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
        run_values = values.get("run_config")
        migrated = not isinstance(run_values, dict)
        if migrated:
            run_values = self._legacy_run_values(values)
        assert isinstance(run_values, dict)
        run_config = GlobalRunConfig.from_dict(run_values)
        config = AppConfig(
            chrome_executable=str(values.get("chrome_executable", "")),
            chrome_profile_path=str(values.get("chrome_profile_path", "")),
            gemini_verified=bool(values.get("gemini_verified", False)),
            debug_port=int(values.get("debug_port", 9347)),
            transaction_export_path=str(values.get("transaction_export_path", "")),
            run_config=run_config,
            tabs=tabs,
            next_profile_number=int(
                values.get("next_profile_number", max(tab.profile_number for tab in tabs) + 1)
            ),
        )
        if not config.chrome_executable:
            detected = detect_chrome()
            config.chrome_executable = str(detected) if detected else ""
        if not config.chrome_profile_path:
            profile = (
                LEGACY_GEMINI_PROFILE
                if not settings_exist and LEGACY_GEMINI_PROFILE.is_dir()
                else app_data_directory() / "chrome-profile"
            )
            config.chrome_profile_path = str(profile)
        if settings_exist and (migrated or int(values.get("schema_version", 0)) < CONFIG_SCHEMA_VERSION):
            self.save(config)
        return config

    @staticmethod
    def _legacy_run_values(values: dict[str, Any]) -> dict[str, Any]:
        raw_tabs = values.get("tabs")
        if isinstance(raw_tabs, list):
            for raw in raw_tabs:
                if not isinstance(raw, dict):
                    continue
                try:
                    tab_id = int(raw.get("tab_id", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if tab_id == 1:
                    return raw
            if raw_tabs and isinstance(raw_tabs[0], dict):
                return raw_tabs[0]
        return values

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
        config._normalize_profile_counter()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "chrome_executable": config.chrome_executable,
            "chrome_profile_path": config.chrome_profile_path,
            "gemini_verified": config.gemini_verified,
            "debug_port": config.debug_port,
            "transaction_export_path": config.transaction_export_path,
            "run_config": config.run_config.to_dict(),
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
