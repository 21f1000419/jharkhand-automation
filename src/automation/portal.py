from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from core.controls import RunControls
from core.models import AutomationError, Credentials, Stage, UiEvent
from services.downloads import EstampDownloader
from services.gemini_ocr import GeminiCaptchaSolver
from services.otp_wifi import WifiOtpReceiver

CITIZEN_LOGIN_URL = "https://jharnibandhan.gov.in/Citizenentry/citizenlogin"
ESTAMP_URL = "https://jharnibandhan.gov.in/JHWebService/gras_payment_entry_estamp"


StageCallback = Callable[[Stage], Awaitable[None]]
EventCallback = Callable[[UiEvent], None]


class PortalAutomation:
    """Page adapters for the workflow captured in process.md."""

    def __init__(
        self,
        page: Page,
        solver: GeminiCaptchaSolver,
        controls: RunControls,
        on_stage: StageCallback,
        emit: EventCallback,
        otp_receiver: WifiOtpReceiver,
        otp_auto_fill: bool,
    ) -> None:
        self.page = page
        self.solver = solver
        self.controls = controls
        self.on_stage = on_stage
        self.emit = emit
        self.otp_receiver = otp_receiver
        self.otp_auto_fill = otp_auto_fill
        self.otp_sequence_before_egras = otp_receiver.sequence
        self.stage = Stage.IDLE
        self.page.set_default_timeout(15_000)

    async def process_unit(
        self,
        row: dict[str, str],
        article: str,
        credentials: Credentials,
        download_root: Path,
        row_number: int,
        sequence: int,
    ) -> tuple[Path, str]:
        try:
            await self.ensure_citizen_session(credentials)
            await self.fill_estamp_form(row, article)
            await self.confirm_estamp()
            await self.accept_egras_terms()
            await self.complete_egras_login(credentials)
            await self.choose_gateway()
            await self.accept_gateway_terms()
            await self.manual_payment()
            link = await self.find_result_link()
            await self._stage(Stage.DOWNLOAD)
            downloader = EstampDownloader(self.page.context)
            return await downloader.download(
                self.page,
                await link.get_attribute("href") or "",
                download_root,
                row_number,
                sequence,
            )
        except AutomationError:
            raise
        except PlaywrightError as error:
            if self.page.is_closed():
                raise AutomationError(
                    "Chrome or the portal page was closed.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                ) from error
            raise AutomationError(
                f"The portal could not complete {self.stage.value}: {error}",
                stage=self.stage,
                code="playwright_error",
            ) from error
        except Exception as error:
            raise AutomationError(str(error), stage=self.stage) from error

    async def ensure_citizen_session(self, credentials: Credentials) -> None:
        await self._stage(Stage.CITIZEN_LOGIN)
        # Always begin at the documented Citizen login page.  This lets the
        # portal issue a fresh CAPTCHA and preserves the expected login flow.
        if not self.page.url.startswith(CITIZEN_LOGIN_URL):
            await self._goto(CITIZEN_LOGIN_URL)
        if not credentials.citizen_username or not credentials.citizen_password:
            await self.controls.manual_checkpoint(
                "citizen_login",
                "Enter the Citizen username, password, CAPTCHA, and OTP in Chrome. Click Get OTP, "
                "then Login; click Resume only after the eStamp entry link appears.",
            )
        else:
            await fill_first(self.page, ["#username"], credentials.citizen_username)
            await fill_first(self.page, ["#password"], credentials.citizen_password)
            await self._solve_captcha(
                ["#captcha_image"],
                ["#captcha"],
                ["#reload"],
                expected_length=6,
            )
            otp_button = await first_visible(self.page, ["#btnotp"], 10_000)
            if otp_button is not None:
                citizen_otp_sequence = self.otp_receiver.sequence
                await otp_button.click()
                if not await visible(self.page, "#otp", 120_000):
                    raise AutomationError(
                        "Citizen OTP input did not appear after requesting the OTP.",
                        stage=self.stage,
                        code="citizen_otp_unavailable",
                    )
                if self.otp_auto_fill and self.otp_receiver.is_paired:
                    self.emit(UiEvent("otp_waiting", "Waiting for the paired phone's Citizen OTP..."))
                    otp = await self.otp_receiver.wait_for_new(citizen_otp_sequence, timeout_seconds=60)
                    if otp is not None:
                        await self.page.locator("#otp").fill(otp)
                        self.emit(
                            UiEvent("otp_filled", "Citizen OTP received from the paired phone and filled.")
                        )
                elif self.otp_auto_fill:
                    self.emit(
                        UiEvent("otp_manual", "No paired phone is connected; enter the Citizen OTP manually.")
                    )
                await self.controls.manual_checkpoint(
                    "citizen_otp",
                    "Confirm the Citizen OTP, complete Login, and click Resume after the eStamp entry "
                    "link appears.",
                )
            else:
                await click_first(self.page, ["#btnSubmit"])

        await self._wait_for_estamp_entry()

    async def _wait_for_estamp_entry(self) -> None:
        """Wait for the post-login eStamp link; slow portal responses may take two minutes."""
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed during Citizen login.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            if await visible(self.page, "#payment_purpose_id", 500):
                return
            entry = await first_visible(
                self.page,
                ['a[href*="gras_payment_entry_estamp"]', 'a[href*="payment_entry_estamp"]'],
                500,
            )
            if entry is not None:
                await entry.click()
                await self.page.wait_for_load_state("domcontentloaded", timeout=120_000)
                if await visible(self.page, "#payment_purpose_id", 30_000):
                    return
            await self.page.wait_for_timeout(500)
        raise AutomationError(
            "Citizen login did not show the eStamp entry link within two minutes.",
            stage=self.stage,
            code="citizen_login_incomplete",
        )

    async def fill_estamp_form(self, row: dict[str, str], article: str) -> None:
        await self._stage(Stage.FILL_ESTAMP)
        await select_value(
            self.page,
            "#payment_purpose_id",
            value="2",
            field_name="Purpose of Payment",
        )
        await select_value(
            self.page,
            "#district_id",
            label=row["district"].strip(),
            field_name="District",
        )
        await select_value(
            self.page,
            "#article_id",
            label=article.strip(),
            field_name="Article",
        )
        fields = {
            "#party1_fullname_en": row["first_party_name"],
            "#party2_fullname_en": row["second_party_name"] or "NIL",
            "#payee_fname_en": row["stamp_duty_paid_by"],
            "#payment_reason": row["stamp_purpose"],
            "#PANNO": row["pan"],
            "#mobile": row["mobile"],
            "#AMOUNT": row["amount"],
        }
        for selector, value in fields.items():
            await self.controls.checkpoint()
            await fill_first(self.page, [selector], value)
        form_errors = await form_validation_errors(self.page, fields)
        if form_errors:
            raise AutomationError("; ".join(form_errors), stage=self.stage, code="form_validation_failed")
        await click_first(self.page, ["#launchmodal", 'button:has-text("Proceed to Pay")'])

    async def confirm_estamp(self) -> None:
        await self._stage(Stage.CONFIRM_ESTAMP)
        modal = self.page.locator("#exampleModal")
        await modal.wait_for(state="visible")
        await click_first(
            self.page,
            [
                '#exampleModal input[type="submit"][value="Pay Now"]',
                '#exampleModal button:has-text("Pay Now")',
            ],
        )
        await self.page.wait_for_load_state("domcontentloaded", timeout=60_000)

    async def accept_egras_terms(self) -> None:
        await self._stage(Stage.EGRAS_TERMS)
        checkbox = await first_visible(self.page, ["#takenBefore", 'input[name="ch"]'], 20_000)
        if checkbox is None:
            return
        await checkbox.check(force=True)
        await click_first(self.page, ["button.close_model", 'button:has-text("OK")'])

    async def complete_egras_login(self, credentials: Credentials) -> None:
        await self._stage(Stage.EGRAS_LOGIN)
        username = await first_visible(self.page, ["#txtLoginId"], 10_000)
        if username is None:
            return
        if not credentials.egras_username or not credentials.egras_password:
            await self.controls.manual_checkpoint(
                "egras_login",
                "Complete the eGRAS login in Chrome, then click Resume.",
            )
        else:
            await username.fill(credentials.egras_username)
            await fill_first(self.page, ["#txtPassword"], credentials.egras_password)
            await self._solve_captcha(
                ["img.imgcaptcha"],
                ["#txtcaptcha"],
                ["#ImageButton1", "input.rigcp"],
                expected_length=6,
            )
            self.otp_sequence_before_egras = self.otp_receiver.sequence
            await click_first(self.page, ["#btnproceed", 'input[value="Proceed"]'])
            await self.page.wait_for_timeout(750)

        if await visible(self.page, "#txtOTP", 12_000):
            await self._stage(Stage.EGRAS_OTP)
            if self.otp_auto_fill and self.otp_receiver.is_paired:
                self.emit(UiEvent("otp_waiting", "Waiting up to 45 seconds for a paired phone OTP..."))
                otp = await self.otp_receiver.wait_for_new(self.otp_sequence_before_egras, timeout_seconds=45)
                if otp is not None:
                    await self.page.locator("#txtOTP").fill(otp)
                    self.emit(
                        UiEvent("otp_filled", "OTP received from the paired phone and filled in eGRAS.")
                    )
            elif self.otp_auto_fill:
                self.emit(
                    UiEvent("otp_manual", "No paired phone is connected; use the manual eGRAS OTP step.")
                )
            await self.controls.manual_checkpoint(
                "egras_otp",
                "Complete the eGRAS CAPTCHA and submit the OTP in Chrome, then click Resume.",
            )

    async def choose_gateway(self) -> None:
        await self._stage(Stage.GATEWAY_SELECT)
        radio = await first_visible(self.page, ["#rbsbiepay"], 30_000)
        if radio is None:
            raise AutomationError("The SBIePay gateway option was not found.", stage=self.stage)
        await radio.check(force=True)
        await click_first(self.page, ["#btnSubmit", 'input[value^="Pay Rs"]'])
        await self.page.wait_for_load_state("domcontentloaded", timeout=60_000)

    async def accept_gateway_terms(self) -> None:
        await self._stage(Stage.GATEWAY_TERMS)
        agree = await first_visible(
            self.page,
            ["#ContentPlaceHolder1_rblagree_0", 'input[type="radio"][value="Y"]'],
            30_000,
        )
        if agree is None:
            return
        await agree.check(force=True)
        self.page.once("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
        await click_first(
            self.page,
            ["#ContentPlaceHolder1_btncontinue", 'a:has-text("Proceed For Payment")'],
        )
        await self.page.wait_for_load_state("domcontentloaded", timeout=60_000)

    async def manual_payment(self) -> None:
        await self._stage(Stage.PAYMENT)
        await self.controls.manual_checkpoint(
            "payment",
            "Complete payment in Chrome. Do not click Resume until the portal shows success, "
            "failure, or returns to the eStamp page.",
        )

    async def find_result_link(self) -> Locator:
        await self._stage(Stage.RESULT)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "Chrome was closed while waiting for the payment result.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            links = self.page.locator('a[href*="gras_estamp_download"]')
            if await links.count() and await links.first.is_visible():
                return links.first
            content = (await self.page.locator("body").inner_text()).lower()
            if "transaction failed" in content:
                raise AutomationError(
                    "The payment gateway reported: Transaction Failed.",
                    stage=self.stage,
                    code="transaction_failed",
                )
            if any(marker in self.page.url.lower() for marker in ("payment", "payproc", "sbiepay")):
                await self.controls.manual_checkpoint(
                    "payment_incomplete",
                    "The payment result is not available yet. Finish or verify it in Chrome, "
                    "then click Resume.",
                )
            await self.page.wait_for_timeout(500)
        raise AutomationError("Timed out waiting for the eStamp result.", stage=self.stage)

    async def reset_to_start(self, *, record_stage: bool = True) -> None:
        if record_stage:
            await self._stage(Stage.RESET)
        else:
            self.stage = Stage.RESET
            await self.controls.checkpoint()
        if not self.page.is_closed():
            await self._goto(ESTAMP_URL)

    async def _solve_captcha(
        self,
        image_selectors: list[str],
        input_selectors: list[str],
        refresh_selectors: list[str],
        expected_length: int,
    ) -> None:
        last_error = "CAPTCHA could not be solved."
        for attempt in range(3):
            await self.controls.checkpoint()
            image = await first_visible(self.page, image_selectors, 10_000)
            field = await first_visible(self.page, input_selectors, 5_000)
            if image is None or field is None:
                raise AutomationError("The CAPTCHA image or input field was not found.", stage=self.stage)
            try:
                image_bytes = await self._capture_loaded_captcha(image)
                code = await self.solver.solve(image_bytes, expected_length)
                await field.fill(code)
                self.emit(UiEvent("log", f"CAPTCHA solved on attempt {attempt + 1}."))
                return
            except Exception as error:
                last_error = str(error)
                refresh = await first_visible(self.page, refresh_selectors, 1_000)
                if refresh is not None:
                    await refresh.click()
                    await self.page.wait_for_timeout(500)
        raise AutomationError(last_error, stage=self.stage, code="captcha_failed")

    async def _capture_loaded_captcha(self, image: Locator) -> bytes:
        """Capture the exact CAPTCHA only after the browser has decoded the image response."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            loaded = await image.evaluate(
                "element => element.complete && element.naturalWidth > 0 && element.naturalHeight > 0"
            )
            if loaded:
                return await image.screenshot(type="png")
            await self.page.wait_for_timeout(150)
        raise AutomationError(
            "The CAPTCHA image did not finish loading.",
            stage=self.stage,
            code="captcha_not_loaded",
        )

    async def _stage(self, stage: Stage) -> None:
        self.stage = stage
        await self.controls.checkpoint()
        await self.on_stage(stage)

    async def _goto(self, url: str) -> None:
        await self.controls.checkpoint()
        await self.page.goto(url, wait_until="domcontentloaded", timeout=120_000)


async def first_visible(page: Page, selectors: list[str], timeout_ms: int = 15_000) -> Locator | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for selector in selectors:
            matches = page.locator(selector)
            for index in range(await matches.count()):
                candidate = matches.nth(index)
                if await candidate.is_visible():
                    return candidate
        await page.wait_for_timeout(100)
    return None


async def visible(page: Page, selector: str, timeout_ms: int) -> bool:
    return await first_visible(page, [selector], timeout_ms) is not None


async def fill_first(page: Page, selectors: list[str], value: str) -> None:
    field = await first_visible(page, selectors)
    if field is None:
        raise RuntimeError(f"Required field was not found: {selectors[0]}")
    await field.fill(value)


async def click_first(page: Page, selectors: list[str]) -> None:
    target = await first_visible(page, selectors)
    if target is None:
        raise RuntimeError(f"Required button was not found: {selectors[0]}")
    await target.click()


async def select_value(
    page: Page,
    selector: str,
    *,
    value: str | None = None,
    label: str | None = None,
    field_name: str,
) -> None:
    select = page.locator(selector)
    if await select.count() == 0:
        raise RuntimeError(f"Required selection was not found: {selector}")
    try:
        if value is not None:
            selected = await select.select_option(value=value)
        else:
            selected = await select.select_option(label=label or "")
    except PlaywrightError as error:
        available = await option_labels(select)
        choices = ", ".join(available[:8])
        suffix = f" Available choices include: {choices}." if choices else ""
        raise AutomationError(
            f"{field_name} value {label or value!r} is not available on the portal.{suffix}",
            stage=Stage.FILL_ESTAMP,
            code="invalid_select_option",
        ) from error
    if not selected:
        raise AutomationError(
            f"{field_name} value {label or value!r} is not available on the portal.",
            stage=Stage.FILL_ESTAMP,
            code="invalid_select_option",
        )


async def option_labels(select: Locator) -> list[str]:
    options = select.locator("option")
    labels = [label.strip() for label in await options.all_inner_texts()]
    return [label for label in labels if label and label != "--select--"]


async def form_validation_errors(page: Page, fields: dict[str, str]) -> list[str]:
    """Read browser/native validation after all eStamp fields are populated."""
    errors: list[str] = []
    for selector in fields:
        field = page.locator(selector)
        if await field.count() == 0:
            errors.append(f"Required form field is missing: {selector}")
            continue
        is_valid = await field.evaluate("element => element.checkValidity()")
        if not is_valid:
            message = await field.evaluate("element => element.validationMessage")
            errors.append(f"{selector}: {message or 'invalid value'}")
    return errors
