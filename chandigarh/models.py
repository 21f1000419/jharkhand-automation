from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class BrowserEngine(StrEnum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"


@dataclass(frozen=True)
class PortalBrowser:
    name: str
    executable: Path
    engine: BrowserEngine


@dataclass(frozen=True)
class Credentials:
    citizen_username: str = ""
    citizen_password: str = ""
