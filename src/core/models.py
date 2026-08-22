from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from importlib.util import find_spec
from pathlib import Path
from typing import Any


class RunMode(StrEnum):
    ASSISTED = "assisted"
    CONTINUOUS = "continuous"


class BrowserEngine(StrEnum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"


class CaptchaCopyMode(StrEnum):
    DIRECT = "direct_copy"
    MOUSE_CURSOR = "mouse_cursor"


class OcrEngine(StrEnum):
    PADDLEOCR = "paddleocr"
    EASYOCR = "easyocr"
    GEMINI = "gemini"


def available_ocr_engines() -> tuple[OcrEngine, ...]:
    """Return the OCR engines included in this build."""
    engines = [OcrEngine.PADDLEOCR, OcrEngine.GEMINI]
    if find_spec("easyocr") is not None:
        engines.insert(1, OcrEngine.EASYOCR)
    return tuple(engines)


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


STAGE_CHECKPOINTS: dict[Stage, tuple[Stage, str, str]] = {
    Stage.CITIZEN_LOGIN: (
        Stage.FILL_ESTAMP,
        "eStamp Payment Entry Form",
        "Complete Citizen login and leave the browser on the eStamp payment entry form.",
    ),
    Stage.OPEN_ESTAMP: (
        Stage.FILL_ESTAMP,
        "eStamp Payment Entry Form",
        "Navigate and leave the browser on the eStamp payment entry form.",
    ),
    Stage.FILL_ESTAMP: (
        Stage.CONFIRM_ESTAMP,
        "Pay Now Confirmation Modal",
        "Fill required form fields, click 'Proceed to Pay', and leave the browser on the 'Pay Now' confirmation modal.",
    ),
    Stage.CONFIRM_ESTAMP: (
        Stage.EGRAS_TERMS,
        "eGRAS Disclaimer / Terms",
        "Click 'Pay Now' in the modal and leave the browser on the eGRAS Terms and Conditions checkbox page.",
    ),
    Stage.EGRAS_TERMS: (
        Stage.EGRAS_LOGIN,
        "eGRAS User Login",
        "Check the terms checkbox, click OK, and leave the browser on the eGRAS Login page.",
    ),
    Stage.EGRAS_LOGIN: (
        Stage.EGRAS_OTP,
        "eGRAS OTP & Validation CAPTCHA",
        "Enter eGRAS login credentials and CAPTCHA, click Proceed, and leave the browser on the eGRAS OTP page.",
    ),
    Stage.EGRAS_OTP: (
        Stage.GATEWAY_SELECT,
        "Payment Gateway Selection (SBIePay)",
        "Enter OTP and validation CAPTCHA, click Validate OTP, and leave the browser on the Payment Gateway (SBIePay) selection page.",
    ),
    Stage.GATEWAY_SELECT: (
        Stage.GATEWAY_TERMS,
        "Gateway Terms & Conditions",
        "Select SBIePay radio button, click Pay, and leave the browser on the Terms Agreement page.",
    ),
    Stage.GATEWAY_TERMS: (
        Stage.UPI_SELECT,
        "SBI Hosted Payment Page (UPI Option)",
        "Agree to terms, click Proceed For Payment, and leave the browser on the SBI Payment page.",
    ),
    Stage.UPI_SELECT: (
        Stage.PAYMENT,
        "UPI QR Payment Screen",
        "Select UPI, select UPI QR option, click Pay Now, and leave the browser on the UPI QR code screen.",
    ),
    Stage.PAYMENT: (
        Stage.RESULT,
        "Transaction Confirmation Details",
        "Complete the UPI payment in your banking app and leave the browser on the Transaction Details confirmation page.",
    ),
    Stage.RESULT: (
        Stage.DOWNLOAD,
        "eStamp Download Page",
        "Leave the browser on the page with the 'Download eStamp Certificate' button.",
    ),
    Stage.DOWNLOAD: (
        Stage.DOWNLOAD,
        "eStamp Download Page",
        "Leave the browser on the page with the 'Download eStamp Certificate' button.",
    ),
}


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
    ocr_enabled: bool = False
    ocr_engine: OcrEngine = OcrEngine.PADDLEOCR
    sms_user_id: str = ""
    sms_server_url: str = ""
    payment_trigger_url: str = ""
    payment_trigger_method: str = "GET"
    captcha_copy_mode: CaptchaCopyMode = CaptchaCopyMode.DIRECT
    save_captcha_images: bool = True
    fresh_browser_per_unit: bool = False


@dataclass(frozen=True)
class TransactionResult:
    details: dict[str, str]
    reference: str
    destination: Path | None = None
    download_error: str = ""


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
