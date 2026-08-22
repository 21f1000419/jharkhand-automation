from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from core.controls import RunControls
from core.models import (
    AutomationError,
    CaptchaCopyMode,
    Credentials,
    Stage,
    TransactionResult,
    UiEvent,
    WorkflowStopped,
)
from services.desktop_copy_image import copy_image_from_screen_position
from services.downloads import EstampDownloader
from services.captcha_ocr import CaptchaSolver
from services.payment_trigger import send_payment_trigger_request
from services.sms_otp_client import SmsOtpClient, SmsOtpServerError

CITIZEN_LOGIN_URL = "https://jharnibandhan.gov.in/Citizenentry/citizenlogin"
CITIZEN_WELCOME_URL = "https://jharnibandhan.gov.in/Citizenentry/welcome"
ESTAMP_URL = "https://jharnibandhan.gov.in/JHWebService/gras_payment_entry_estamp"
SBI_HOSTED_PAYMENT_URL = "https://epay.sbi.bank.in/secure/AggregatorHostedListener"
MAIN_OTP_POLL_TIMEOUT_SECONDS = 90
EGRASS_OTP_POLL_TIMEOUT_SECONDS = 100
TRANSACTION_FIELD_NAMES = {
    "name": "Name",
    "token no / depositor id": "Token No / Depositor ID",
    "amount": "Amount",
    "transaction id": "Transaction ID",
    "grn": "GRN",
    "cin": "CIN",
    "time": "Time",
}
UPI_QR_READY_TEXT = "scan upi qr"
UPI_TRANSACTION_TIMER_TEXT = "time left to complete the transaction"
CAPTCHA_FAILURE_TEXT = "captcha validation failed"


StageCallback = Callable[[Stage], Awaitable[None]]
EventCallback = Callable[[UiEvent], None]


class PortalAutomation:
    """Page adapters for the workflow captured in process.md."""

    def __init__(
        self,
        page: Page,
        solver: CaptchaSolver | None,
        controls: RunControls,
        on_stage: StageCallback,
        emit: EventCallback,
        sms_otp_client: SmsOtpClient,
        sms_user_id: str,
        captcha_copy_mode: CaptchaCopyMode,
        payment_trigger_url: str = "",
        payment_trigger_method: str = "GET",
    ) -> None:
        self.page = page
        self.solver = solver
        self.controls = controls
        self.on_stage = on_stage
        self.emit = emit
        self.sms_otp_client = sms_otp_client
        self.sms_user_id = sms_user_id.strip()
        self.captcha_copy_mode = captcha_copy_mode
        self.payment_trigger_url = payment_trigger_url.strip()
        self.payment_trigger_method = (
            "POST" if payment_trigger_method.strip().upper() == "POST" else "GET"
        )
        self.citizen_otp_for_cleanup: str | None = None
        self.egrass_otp_for_cleanup: str | None = None
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
    ) -> TransactionResult:
        try:
            await self.ensure_citizen_session(credentials)
            await self.fill_estamp_form(row, article)
            await self.confirm_estamp()
            await self.accept_egras_terms()
            await self.complete_egras_login(credentials)
            await self.choose_gateway()
            await self.accept_gateway_terms()
            await self.select_upi()
            await self.select_upi_qr_and_pay()
            await self.wait_for_upi_qr_and_trigger()
            details = await self.find_result_details()
            reference = (
                details.get("Transaction ID")
                or details.get("GRN")
                or details.get("CIN")
                or details.get("Token No / Depositor ID")
                or f"unit-{sequence}"
            )
            try:
                await self._stage(Stage.DOWNLOAD)
                link = await first_visible(self.page, ['a[href*="gras_estamp_download"]'], 5_000)
                if link is None:
                    raise RuntimeError("The eStamp download button was not found.")
                downloader = EstampDownloader()
                destination, _download_reference = await downloader.download(
                    self.page,
                    link,
                    download_root,
                    row_number,
                    sequence,
                )
            except Exception as download_error:
                error = str(download_error)
                self._report_nonfatal_download_error(error)
                return TransactionResult(details, reference, download_error=error)
            return TransactionResult(details, reference, destination)
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
        if await visible(self.page, "#payment_purpose_id", 1_000):
            return
        if not await visible(self.page, "#username", 1_000):
            await self._goto(CITIZEN_LOGIN_URL, timeout_ms=30_000)
        await self._wait_for_login_element("#username")
        # Start watching before CAPTCHA handling.  The watcher does not poll the
        # SMS server until the portal exposes the OTP field, so a manually
        # entered CAPTCHA (and the user's later Get OTP click) cannot delay
        # automatic OTP handling.
        citizen_otp_task = self._start_otp_watcher("main", "#otp")
        captcha_entered_manually = True
        while True:
            await self._prepare_citizen_login_attempt(credentials)
            if credentials.citizen_username and credentials.citizen_password:
                captcha_entered_manually = await self._solve_captcha(
                    "#captcha_image",
                    "#captcha",
                    expected_length=6,
                )

            if not captcha_entered_manually and not await visible(self.page, "#otp", 500):
                otp_button = await first_visible(self.page, ["#btnotp"], 5_000)
                if otp_button is not None:
                    await otp_button.click()

            outcome = await self._wait_for_login_progress(
                otp_selector="#otp",
                success_url_prefix=CITIZEN_WELCOME_URL,
                field_selectors=["#username", "#password", "#captcha"],
            )
            if outcome != "captcha_failed":
                break
            self.emit(
                UiEvent(
                    "log",
                    "Citizen CAPTCHA validation failed. Retrying with fresh credentials and CAPTCHA.",
                )
            )
        try:
            await self._wait_for_login_element("#otp")
            otp = await self._await_otp_watcher(citizen_otp_task)
            if otp is not None:
                # Manual CAPTCHA does not imply manual OTP/login.  Only a
                # manual OTP takes ownership of the final Login action.
                await click_first(self.page, ["#btnSubmit", 'button:has-text("Login")'])
                self.emit(UiEvent("log", "Citizen OTP login submitted automatically."))
            else:
                self.emit(
                    UiEvent("otp_manual", "Use the manually entered Citizen OTP, then click Login.")
                )
        finally:
            await self._cancel_otp_watcher(citizen_otp_task)

        await self._wait_for_manual_citizen_login(
            "Waiting for the Citizen welcome page after the user completes the login steps."
        )

        await self._open_estamp_entry()
        if self.citizen_otp_for_cleanup is not None:
            self._delete_used_otp_in_background("main", self.citizen_otp_for_cleanup)
            self.citizen_otp_for_cleanup = None

    async def _wait_for_manual_citizen_login(self, message: str) -> None:
        self.emit(UiEvent("log", message))
        self._status("Waiting for Citizen login to complete in the portal…")
        self.emit(UiEvent("log", "Waiting for the Citizen welcome page after Login is submitted."))
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed during Citizen login.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            if self.page.url.rstrip("/").startswith(CITIZEN_WELCOME_URL):
                self.emit(UiEvent("log", "Citizen login detected; opening the eStamp form."))
                return
            await self.page.wait_for_timeout(500)

    async def _open_estamp_entry(self) -> None:
        """Open eStamp directly after login or row reset."""
        self._status("Opening the direct eStamp form…")
        self.emit(UiEvent("log", "Opening the direct eStamp form."))
        if await visible(self.page, "#payment_purpose_id", 1_000):
            return

        for _attempt in range(1, 4):
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed while opening the eStamp form.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            with suppress(PlaywrightError):
                await self.page.goto(
                    CITIZEN_WELCOME_URL,
                    wait_until="domcontentloaded",
                    timeout=15_000,
                )
            with suppress(PlaywrightError):
                await self.page.goto(ESTAMP_URL, wait_until="domcontentloaded", timeout=15_000)

            if await visible(self.page, "#payment_purpose_id", 8_000):
                return
            await self.page.wait_for_timeout(500)

        if await visible(self.page, "#payment_purpose_id", 5_000):
            return

        raise AutomationError(
            "The eStamp payment entry form (#payment_purpose_id) did not load in time.",
            stage=self.stage,
            code="estamp_form_load_timeout",
        )

    async def _wait_for_login_element(self, selector: str) -> Locator:
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed during Citizen login.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            element = await first_visible(self.page, [selector], 500)
            if element is not None:
                return element

    async def fill_estamp_form(self, row: dict[str, str], article: str) -> None:
        await self._stage(Stage.FILL_ESTAMP)
        mobile = row.get("mobile", "").strip()
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
            "#AMOUNT": row["amount"],
        }
        if mobile:
            fields["#mobile"] = mobile
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
        try:
            await checkbox.check(force=True)
        except PlaywrightError:
            # This portal sometimes handles the forced click but immediately reports the
            # checkbox as unchanged. Set the native property and emit the same form events
            # so a transient Playwright actionability mismatch does not discard the unit.
            await checkbox.evaluate(
                """element => {
                    element.checked = true;
                    element.dispatchEvent(new Event('input', {bubbles: true}));
                    element.dispatchEvent(new Event('change', {bubbles: true}));
                }"""
            )
        if not await checkbox.is_checked():
            raise AutomationError(
                "The eGRAS terms checkbox could not be selected.",
                stage=self.stage,
                code="terms_checkbox_failed",
            )
        await click_first(self.page, ["button.close_model", 'button:has-text("OK")'])

    async def complete_egras_login(self, credentials: Credentials) -> None:
        await self._stage(Stage.EGRAS_LOGIN)
        username = await first_visible(self.page, ["#txtLoginId"], 10_000)
        if username is None:
            return
        # This watcher remains independent of both eGRAS CAPTCHA steps.  It
        # waits for #txtOTP to appear before beginning the SMS poll, then fills
        # the field as soon as a code is available.
        egrass_otp_task = self._start_otp_watcher("egrass", "#txtOTP")
        login_captcha_entered_manually = True
        while True:
            if credentials.egras_username and credentials.egras_password:
                await username.fill(credentials.egras_username)
                await fill_first(self.page, ["#txtPassword"], credentials.egras_password)
                login_captcha_entered_manually = await self._solve_captcha(
                    "img.imgcaptcha",
                    "#txtcaptcha",
                    expected_length=6,
                )

            if not login_captcha_entered_manually and not await visible(self.page, "#txtOTP", 500):
                proceed_btn = await first_visible(
                    self.page, ["#btnproceed", 'input[value="Proceed"]'], 5_000
                )
                if proceed_btn is not None:
                    await proceed_btn.click()

            outcome = await self._wait_for_login_progress(
                otp_selector="#txtOTP",
                success_url_prefix="",
                field_selectors=["#txtLoginId", "#txtPassword", "#txtcaptcha"],
            )
            if outcome != "captcha_failed":
                break
            self.emit(
                UiEvent(
                    "log",
                    "eGRAS CAPTCHA validation failed. Retrying with fresh credentials and CAPTCHA.",
                )
            )
        try:
            await self._wait_for_egras_otp_step()

            await self._stage(Stage.EGRAS_OTP)
            # The watcher can now fill #txtOTP while Gemini or the user handles
            # the validation CAPTCHA.
            otp_captcha_entered_manually = await self._solve_captcha(
                "div.tab-pane.active img.imgcaptcha",
                "div.tab-pane.active #txtcaptcha",
                expected_length=6,
            )

            otp = await self._await_otp_watcher(egrass_otp_task)
            if otp is not None:
                # eGRAS requires every input at this validation point to be
                # automatic before clicking Validate OTP.  A manual CAPTCHA or
                # manual OTP leaves that action to the user.
                if not login_captcha_entered_manually and not otp_captcha_entered_manually:
                    await click_first(self.page, ["#btnproceed", 'input[value="Validate OTP"]'])
                    self.emit(UiEvent("log", "eGRAS OTP validation submitted automatically."))
            else:
                self.emit(
                    UiEvent("otp_manual", "Use the manually entered eGRAS OTP, then click Validate OTP.")
                )
            await self._wait_for_manual_egras_otp(
                "Confirm the eGRAS CAPTCHA and OTP, then click Validate OTP in Chrome. The app will "
                "continue when the payment option appears."
            )
        finally:
            await self._cancel_otp_watcher(egrass_otp_task)
        if self.egrass_otp_for_cleanup is not None:
            self._delete_used_otp_in_background("egrass", self.egrass_otp_for_cleanup)
            self.egrass_otp_for_cleanup = None

    def _capture_otp_request_time(self) -> str | None:
        """Use UTC so differing local timezones cannot admit old OTPs."""
        return self.sms_otp_client.request_time() if self.sms_user_id else None

    def _start_otp_watcher(
        self, otp_type: str, field_selector: str
    ) -> asyncio.Task[str | None] | None:
        """Start an OTP watcher without coupling it to CAPTCHA entry."""
        if not self.sms_user_id:
            return None
        timeout_seconds = (
            MAIN_OTP_POLL_TIMEOUT_SECONDS if otp_type == "main" else EGRASS_OTP_POLL_TIMEOUT_SECONDS
        )
        return asyncio.create_task(
            self._watch_and_fill_sms_otp(otp_type, field_selector, timeout_seconds)
        )

    async def _await_otp_watcher(self, task: asyncio.Task[str | None] | None) -> str | None:
        if task is None:
            return None
        return await task

    async def _cancel_otp_watcher(self, task: asyncio.Task[str | None] | None) -> None:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _watch_and_fill_sms_otp(
        self, otp_type: str, field_selector: str, timeout_seconds: float
    ) -> str | None:
        """Wait for the portal OTP field, then retrieve and fill an untouched OTP."""
        await self._wait_for_login_element(field_selector)
        field = self.page.locator(field_selector).first
        not_before = self._capture_otp_request_time()
        if not_before is None:
            return None
        self.emit(UiEvent("otp_waiting", f"Waiting for {otp_type} OTP from the SMS server..."))
        otp = await self._wait_for_sms_otp(otp_type, field_selector, not_before, timeout_seconds)
        if otp is None:
            return None
        try:
            # Check again immediately before filling so manual input always
            # wins if it was entered while the SMS request was in flight.
            if (await field.input_value()).strip():
                self.emit(
                    UiEvent("otp_manual", "Manual OTP input detected; automatic OTP entry is disabled.")
                )
                return None
            await field.fill(otp)
        except PlaywrightError:
            return None

        if otp_type == "main":
            self.citizen_otp_for_cleanup = otp
            message = "Citizen OTP received from the SMS server and filled."
        else:
            self.egrass_otp_for_cleanup = otp
            message = "OTP received from the SMS server and filled in eGRAS."
        self.emit(UiEvent("otp_filled", message))
        return otp

    async def _wait_for_sms_otp(
        self, otp_type: str, field_selector: str, not_before: str, timeout_seconds: float
    ) -> str | None:
        """Poll without blocking Playwright; a missing/unreachable server falls back to manual entry."""
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        reported_connection_issue = False
        last_poll_time = 0.0
        while time.monotonic() < deadline:
            await self.controls.checkpoint()
            field = self.page.locator(field_selector).first
            try:
                if await field.count() > 0 and (await field.input_value()).strip():
                    self.emit(
                        UiEvent("otp_manual", "Manual OTP input detected; automatic OTP entry is disabled.")
                    )
                    return None
            except PlaywrightError:
                pass

            now = time.monotonic()
            elapsed_seconds = now - started_at
            poll_interval = 1.0 if elapsed_seconds < 10 else 2.0 if elapsed_seconds < 40 else 3.0
            if now - last_poll_time >= poll_interval:
                last_poll_time = now
                try:
                    otp = await self.sms_otp_client.get_otp(self.sms_user_id, otp_type, not_before)
                except SmsOtpServerError as error:
                    if not reported_connection_issue:
                        self.emit(
                            UiEvent("otp_server_issue", f"SMS OTP server unavailable; retrying: {error}")
                        )
                        reported_connection_issue = True
                else:
                    if otp is not None:
                        try:
                            if await field.count() > 0 and (await field.input_value()).strip():
                                self.emit(
                                    UiEvent(
                                        "otp_manual",
                                        "Manual OTP input detected; automatic OTP entry is disabled.",
                                    )
                                )
                                return None
                        except PlaywrightError:
                            pass
                        return otp

            await self.page.wait_for_timeout(250)
        return None

    def _delete_used_otp_in_background(self, otp_type: str, otp: str) -> None:
        """Cleanup is deliberately detached so it cannot delay the browser workflow."""

        async def delete() -> None:
            try:
                deleted = await self.sms_otp_client.delete_otp_after_use(
                    self.sms_user_id, otp_type, otp
                )
            except SmsOtpServerError as error:
                self.emit(UiEvent("otp_cleanup_issue", f"Used {otp_type} OTP could not be removed: {error}"))
                return
            if deleted:
                self.emit(UiEvent("otp_removed", f"Used {otp_type} OTP removed from the SMS server."))

        asyncio.create_task(delete())

    async def _wait_for_manual_egras_login(self, message: str) -> None:
        self.emit(UiEvent("log", message))
        self._status("Waiting for the eGRAS OTP page…")
        self.emit(UiEvent("log", "Waiting for the eGRAS OTP page and second CAPTCHA."))
        await self._wait_for_egras_otp_step()
        self.emit(UiEvent("log", "eGRAS OTP page detected; solving the second CAPTCHA."))

    async def _wait_for_egras_otp_step(self) -> None:
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed while waiting for the eGRAS OTP page.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            otp = await first_visible(self.page, ["#txtOTP"], 500)
            captcha = await first_visible(self.page, ["div.tab-pane.active img.imgcaptcha"], 500)
            captcha_input = await first_visible(self.page, ["div.tab-pane.active #txtcaptcha"], 500)
            if otp is not None and captcha is not None and captcha_input is not None:
                return
            await self.page.wait_for_timeout(500)

    async def _wait_for_manual_egras_otp(self, message: str) -> None:
        self.emit(UiEvent("log", message))
        self._status("Waiting for eGRAS OTP validation to complete…")
        self.emit(UiEvent("log", "Waiting for the SBIePay option and payment button after eGRAS OTP."))
        await self._wait_for_gateway_options()
        self.emit(UiEvent("log", "Payment options detected; continuing to gateway selection."))

    async def _wait_for_gateway_options(self) -> None:
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed while waiting for payment options.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            gateway = await first_visible(self.page, ["#rbsbiepay"], 500)
            pay_button = await first_visible(self.page, ["#btnSubmit"], 500)
            if gateway is not None and pay_button is not None:
                return
            await self.page.wait_for_timeout(500)

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
        await self.page.wait_for_timeout(500)

    async def select_upi(self) -> None:
        """Choose UPI on the SBI hosted payment page before manual payment."""
        await self._stage(Stage.UPI_SELECT)
        self._status("Waiting for the SBI payment page and UPI option…")
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed while waiting for the SBI payment page.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            if self.page.url.startswith(SBI_HOSTED_PAYMENT_URL):
                upi = await first_visible(self.page, ["#activeUPI a.collapseup", "#activeUPI"], 500)
                if upi is not None:
                    await upi.click()
                    await self.page.wait_for_timeout(1_000)
                    self.emit(UiEvent("log", "UPI selected on the SBI payment page."))
                    return
            await self.page.wait_for_timeout(500)

    async def select_upi_qr_and_pay(self) -> None:
        """Select UPI QR and begin the user-facing UPI payment."""
        await self._stage(Stage.PAYMENT)
        self._status("Selecting UPI QR and preparing payment…")
        automatic_selection_attempted = False
        manual_takeover = False
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed while preparing the UPI payment.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            qr_option = self.page.locator("#upiQR1").first
            qr_available = await qr_option.count() > 0
            qr_checked = await locator_is_checked(qr_option) if qr_available else False

            if qr_available and not qr_checked and not automatic_selection_attempted:
                automatic_selection_attempted = True
                try:
                    await qr_option.click(force=True, timeout=3_000)
                except PlaywrightError as error:
                    self.emit(UiEvent("log", f"Normal UPI QR click was not accepted: {error}"))
                qr_checked = await locator_is_checked(qr_option)

                if not qr_checked:
                    try:
                        await qr_option.evaluate(
                            """element => {
                                element.scrollIntoView({block: 'center', inline: 'center'});
                                element.click();
                                if (!element.checked) {
                                    element.checked = true;
                                    element.dispatchEvent(new Event('input', {bubbles: true}));
                                    element.dispatchEvent(new Event('change', {bubbles: true}));
                                }
                                return element.checked;
                            }"""
                        )
                    except PlaywrightError as error:
                        self.emit(UiEvent("log", f"JavaScript UPI QR click was not accepted: {error}"))
                    qr_checked = await locator_is_checked(qr_option)

                if qr_checked:
                    self.emit(UiEvent("log", "UPI QR selected using the automatic fallback."))
                else:
                    manual_takeover = True
                    self._status("Select UPI QR and click Pay Now manually…")
                    self.emit(
                        UiEvent(
                            "notification",
                            "UPI QR could not be verified automatically. Select UPI QR and "
                            "click Pay Now in the browser; automation will keep waiting.",
                            {
                                "title": "Manual UPI QR selection needed",
                                "level": "warning",
                            },
                        )
                    )

            # Look up Pay Now independently of the native radio state. SBI can run its
            # selection handler without reflecting `checked` back to browser automation.
            pay_now = await first_visible(self.page, ["#upiButton"], 500)
            if pay_now is not None:
                if manual_takeover:
                    self.emit(
                        UiEvent(
                            "log",
                            "Pay Now is available; waiting for the user to complete UPI QR selection.",
                        )
                    )
                    return
                if qr_checked:
                    await pay_now.click()
                    self.emit(UiEvent("log", "UPI QR selected and Pay Now clicked."))
                    return
            await self.page.wait_for_timeout(500)

    async def wait_for_upi_qr_and_trigger(self) -> None:
        """Wait for SBI's QR payment screen, then call the optional external trigger once."""
        self._status("Waiting for the SBI UPI QR payment screen…")
        body = self.page.locator("body")
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed while waiting for the UPI QR payment screen.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            try:
                visible_text = " ".join((await body.inner_text()).casefold().split())
            except PlaywrightError:
                await self.page.wait_for_timeout(250)
                continue
            if (
                UPI_QR_READY_TEXT in visible_text
                and UPI_TRANSACTION_TIMER_TEXT in visible_text
            ):
                self.emit(
                    UiEvent(
                        "log",
                        'UPI QR payment screen detected: "Scan UPI QR" and transaction timer are visible.',
                    )
                )
                break
            await self.page.wait_for_timeout(250)

        if not self.payment_trigger_url:
            return

        try:
            status = await send_payment_trigger_request(
                self.payment_trigger_url,
                self.payment_trigger_method,
            )
        except Exception as error:
            self.emit(
                UiEvent(
                    "log",
                    f"Payment trigger request failed and was ignored: {error}",
                    {"level": "warning"},
                )
            )
            return

        self.emit(
            UiEvent(
                "log",
                f"Payment trigger request sent with {self.payment_trigger_method}; HTTP {status}.",
            )
        )

    async def find_result_details(self) -> dict[str, str]:
        await self._stage(Stage.RESULT)
        self._status("Waiting for UPI payment completion and transaction confirmation…")
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "Chrome was closed while waiting for the payment result.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            content = (await self.page.locator("body").inner_text()).lower()
            if "transaction failed" in content:
                raise AutomationError(
                    "The payment gateway reported: Transaction Failed.",
                    stage=self.stage,
                    code="transaction_failed",
                )
            details = await self._read_transaction_details()
            if details:
                self.emit(
                    UiEvent(
                        "log",
                        "Transaction confirmed: "
                        f"ID {details.get('Transaction ID', 'N/A')}, "
                        f"GRN {details.get('GRN', 'N/A')}, CIN {details.get('CIN', 'N/A')}.",
                    )
                )
                return details
            await self.page.wait_for_timeout(500)

    async def _read_transaction_details(self) -> dict[str, str]:
        tables = self.page.locator("table.table-bordred")
        for table_index in range(await tables.count()):
            table = tables.nth(table_index)
            if not await table.is_visible():
                continue
            cell_rows: list[list[str]] = []
            rows = table.locator("tr")
            for row_index in range(await rows.count()):
                cell_rows.append(await rows.nth(row_index).locator("td").all_inner_texts())
            details = transaction_details_from_rows(cell_rows)
            if any(details.get(key) for key in ("Transaction ID", "GRN", "CIN")):
                return details
        return {}

    def _report_nonfatal_download_error(self, error: str) -> None:
        self.emit(
            UiEvent(
                "notification",
                "The transaction succeeded, but its PDF could not be saved. "
                "Transaction details were recorded.",
                {
                    "title": "eStamp PDF download issue",
                    "level": "warning",
                    "error": error,
                },
            )
        )

    async def reset_to_start(self, *, record_stage: bool = True) -> None:
        if record_stage:
            await self._stage(Stage.RESET)
        else:
            self.stage = Stage.RESET
            await self.controls.checkpoint()
        if not self.page.is_closed():
            await self._open_estamp_entry()

    async def _solve_captcha(
        self,
        image_selector: str,
        input_selector: str,
        expected_length: int,
    ) -> bool:
        """Fill with OCR when possible; return True when the user entered it manually."""
        await self.controls.checkpoint()
        self._status(f"Reading {self.stage.value.replace('_', ' ')} CAPTCHA with OCR...")
        image = await first_visible(self.page, [image_selector], 10_000)
        field = await first_visible(self.page, [input_selector], 5_000)
        if image is None or field is None:
            raise AutomationError("The CAPTCHA image or input field was not found.", stage=self.stage)
        if (await field.input_value()).strip():
            await self._manual_captcha_entered()
            return True

        solver = self.solver
        if solver is None:
            self._status("OCR is inactive; enter the CAPTCHA manually...")
            await self._wait_for_manual_captcha_input(field)
            return True

        ocr_task: asyncio.Task[str] | None = None
        try:
            if self.captcha_copy_mode == CaptchaCopyMode.DIRECT:
                image_png = await self._capture_captcha_directly(image)
                ocr_task = asyncio.create_task(solver.solve_image(image_png, expected_length))
            else:
                await self._copy_captcha_with_browser_menu(image)
                ocr_task = asyncio.create_task(solver.solve(expected_length))
            while not ocr_task.done():
                await self.controls.checkpoint()
                if (await field.input_value()).strip():
                    ocr_task.cancel()
                    await asyncio.gather(ocr_task, return_exceptions=True)
                    await solver.cancel_active_response()
                    await self._manual_captcha_entered()
                    return True
                await self.page.wait_for_timeout(100)
            code = await ocr_task
            if (await field.input_value()).strip():
                await self._manual_captcha_entered()
                return True
            await field.fill(code)
            self._status("CAPTCHA filled; continuing with the portal...")
            self.emit(UiEvent("log", "CAPTCHA solved by OCR."))
            return False
        except WorkflowStopped:
            raise
        except PlaywrightError as error:
            if self.page.is_closed():
                raise AutomationError(
                    "Chrome or the portal page was closed.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                ) from error
            raise
        except Exception as error:
            self.emit(
                UiEvent(
                    "notification",
                    "OCR could not read the CAPTCHA. Please enter it manually.",
                    {"title": "CAPTCHA needs manual entry", "level": "warning", "error": str(error)},
                )
            )
            await self._wait_for_manual_captcha_input(field)
            return True
        finally:
            if ocr_task is not None and not ocr_task.done():
                ocr_task.cancel()
                await asyncio.gather(ocr_task, return_exceptions=True)
                await solver.cancel_active_response()

    async def _manual_captcha_entered(self) -> None:
        self._status("Manual CAPTCHA entered; waiting for the portal action...")
        self.emit(UiEvent("log", "Manual CAPTCHA input detected; OCR was stopped."))

    async def _wait_for_manual_captcha_input(self, field: Locator) -> None:
        self._status("Waiting for manual CAPTCHA input...")
        self.emit(UiEvent("log", "Waiting for manual CAPTCHA input while continuing to poll the portal."))
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed while waiting for the CAPTCHA.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            if (await field.input_value()).strip():
                await self._manual_captcha_entered()
                return
            await self.page.wait_for_timeout(100)

    async def _capture_captcha_directly(self, image: Locator) -> bytes:
        """Capture the rendered CAPTCHA for direct in-memory upload to Gemini."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            await self.controls.checkpoint()
            loaded = await image.evaluate(
                "element => element.complete && element.naturalWidth > 0 && element.naturalHeight > 0"
            )
            if loaded:
                return await image.screenshot()
            await self.page.wait_for_timeout(150)
        raise AutomationError(
            "The CAPTCHA image did not finish loading.",
            stage=self.stage,
            code="captcha_not_loaded",
        )

    async def _prepare_citizen_login_attempt(self, credentials: Credentials) -> None:
        if credentials.citizen_username and credentials.citizen_password:
            await fill_first(self.page, ["#username"], credentials.citizen_username)
            await fill_first(self.page, ["#password"], credentials.citizen_password)

    async def _wait_for_login_progress(
        self,
        *,
        otp_selector: str,
        success_url_prefix: str,
        field_selectors: list[str],
    ) -> str:
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed during login.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            if success_url_prefix and self.page.url.rstrip("/").startswith(success_url_prefix):
                return "success"
            if await visible(self.page, otp_selector, 250):
                return "otp"
            body_text = " ".join((await self.page.locator("body").inner_text()).casefold().split())
            if CAPTCHA_FAILURE_TEXT in body_text:
                return "captcha_failed"
            if all([await visible(self.page, selector, 250) for selector in field_selectors]):
                await self.page.wait_for_timeout(250)
                continue
            await self.page.wait_for_timeout(250)

    async def _copy_captcha_with_browser_menu(self, image: Locator) -> None:
        """Calculate desktop coordinates and use Chrome's native Copy image action."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            await self.controls.checkpoint()
            loaded = await image.evaluate(
                "element => element.complete && element.naturalWidth > 0 && element.naturalHeight > 0"
            )
            if loaded:
                await image.scroll_into_view_if_needed()
                await self.page.bring_to_front()
                await self.page.wait_for_timeout(400)
                position = await image.evaluate(
                    """element => {
                        const rect = element.getBoundingClientRect();
                        const borderX = Math.max(0, (window.outerWidth - window.innerWidth) / 2);
                        const browserTop = Math.max(0, window.outerHeight - window.innerHeight - borderX);
                        const scale = window.devicePixelRatio || 1;
                        return {
                            x: Math.round((window.screenX + borderX + rect.left + rect.width / 2) * scale),
                            y: Math.round((window.screenY + browserTop + rect.top + rect.height / 2) * scale)
                        };
                    }"""
                )
                if not isinstance(position, dict) or "x" not in position or "y" not in position:
                    raise RuntimeError("Could not calculate the CAPTCHA's desktop position.")
                await asyncio.to_thread(
                    copy_image_from_screen_position,
                    int(position["x"]),
                    int(position["y"]),
                )
                return
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

    def _status(self, message: str) -> None:
        self.emit(UiEvent("status", message))

    async def _goto(self, url: str, *, timeout_ms: int = 120_000) -> None:
        await self.controls.checkpoint()
        await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)


def transaction_details_from_rows(rows: list[list[str]]) -> dict[str, str]:
    details: dict[str, str] = {}
    for cells in rows:
        cleaned = [text.strip() for text in cells]
        if len(cleaned) < 2:
            continue
        canonical_name = TRANSACTION_FIELD_NAMES.get(" ".join(cleaned[0].casefold().split()))
        if canonical_name:
            details[canonical_name] = cleaned[1]
    return details


async def locator_is_checked(locator: Locator) -> bool:
    try:
        return await locator.is_checked()
    except PlaywrightError:
        return False


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
