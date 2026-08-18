from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class RunMode(StrEnum):
    ASSISTED = "assisted"
    CONTINUOUS = "continuous"


class BrowserEngine(StrEnum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"


@dataclass(frozen=True)
class PortalBrowser:
    name: str
    executable: Path
    engine: BrowserEngine


class Stage(StrEnum):
    IDLE = "idle"
    VALIDATING = "validating"
    CITIZEN_LOGIN = "citizen_login"
    OPEN_ESTAMP = "open_estamp"
    FILL_ESTAMP = "fill_estamp"
    CONFIRM_ESTAMP = "confirm_estamp"
    EGRAS_TERMS = "egras_terms"
    EGRAS_LOGIN = "egras_login"
    EGRAS_OTP = "egras_otp"
    GATEWAY_SELECT = "gateway_select"
    GATEWAY_TERMS = "gateway_terms"
    UPI_SELECT = "upi_select"
    PAYMENT = "payment"
    RESULT = "result"
    DOWNLOAD = "download"
    RESET = "reset"


class RowStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PARTIAL = "partial"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(frozen=True)
class Credentials:
    citizen_username: str = ""
    citizen_password: str = ""
    egras_username: str = ""
    egras_password: str = ""


@dataclass(frozen=True)
class RunOptions:
    csv_path: Path
    download_root: Path | None
    article: str
    portal_browser: PortalBrowser
    mode: RunMode
    credentials: Credentials
    otp_auto_fill: bool = False


@dataclass(frozen=True)
class UiEvent:
    kind: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class AutomationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: Stage,
        code: str = "automation_error",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.retryable = retryable


class BrowserClosedError(AutomationError):
    def __init__(self) -> None:
        super().__init__(
            "Chrome was closed. The automation has been stopped.",
            stage=Stage.IDLE,
            code="browser_closed",
            retryable=False,
        )


class PersistenceError(RuntimeError):
    """Raised when batch progress cannot be safely persisted."""


class WorkflowStopped(RuntimeError):
    """Cooperative stop signal for the workflow."""
