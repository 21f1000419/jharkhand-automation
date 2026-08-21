from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from core.controls import RunControls
from core.models import AutomationError, CaptchaCopyMode, Credentials, Stage, UiEvent, WorkflowStopped
from services.desktop_copy_image import copy_image_from_screen_position
from services.downloads import EstampDownloader
from services.gemini_ocr import GeminiCaptchaSolver
from services.sms_otp_client import SmsOtpClient, SmsOtpServerError

CITIZEN_LOGIN_URL = "https://jharnibandhan.gov.in/Citizenentry/citizenlogin"
CITIZEN_WELCOME_URL = "https://jharnibandhan.gov.in/Citizenentry/welcome"
ESTAMP_URL = "https://jharnibandhan.gov.in/JHWebService/gras_payment_entry_estamp"
SBI_HOSTED_PAYMENT_URL = "https://epay.sbi.bank.in/secure/AggregatorHostedListener"
MAIN_OTP_POLL_TIMEOUT_SECONDS = 90
EGRASS_OTP_POLL_TIMEOUT_SECONDS = 100


StageCallback = Callable[[Stage], Awaitable[None]]
EventCallback = Callable[[UiEvent], None]


class PortalAutomation:
    """Page adapters for the workflow captured in process.md."""

    def __init__(
        self,
        page: Page,
        solver: GeminiCaptchaSolver | None,
        controls: RunControls,
        on_stage: StageCallback,
        emit: EventCallback,
        sms_otp_client: SmsOtpClient,
        sms_user_id: str,
        captcha_copy_mode: CaptchaCopyMode,
    ) -> None:
        self.page = page
        self.solver = solver
        self.controls = controls
        self.on_stage = on_stage
        self.emit = emit
        self.sms_otp_client = sms_otp_client
        self.sms_user_id = sms_user_id.strip()
        self.captcha_copy_mode = captcha_copy_mode
        self.citizen_otp_for_cleanup: str | None = None
        self.egrass_otp_for_cleanup: str | None = None
        self.citizen_otp_not_before: str | None = None
        self.egrass_otp_not_before: str | None = None
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
            await self.select_upi()
            await self.select_upi_qr_and_pay()
            link = await self.find_result_link()
            await self._stage(Stage.DOWNLOAD)
            downloader = EstampDownloader()
            return await downloader.download(
                self.page,
                link,
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
        if await visible(self.page, "#payment_purpose_id", 1_000):
            return
        if not await visible(self.page, "#username", 1_000):
            await self._goto(CITIZEN_LOGIN_URL, timeout_ms=30_000)
        await self._wait_for_login_element("#username")
        if credentials.citizen_username and credentials.citizen_password:
            await fill_first(self.page, ["#username"], credentials.citizen_username)
            await fill_first(self.page, ["#password"], credentials.citizen_password)
            captcha_entered_manually = await self._solve_captcha(
                "#captcha_image",
                "#captcha",
                expected_length=6,
            )
        else:
            captcha_entered_manually = True

        # Once a person takes over CAPTCHA entry, they also own the associated
        # portal action. Do not submit while they may still be typing.
        if not captcha_entered_manually and not await visible(self.page, "#otp", 500):
            otp_button = await first_visible(self.page, ["#btnotp"], 5_000)
            if otp_button is not None:
                self.citizen_otp_not_before = self._capture_otp_request_time()
                await otp_button.click()
        if not self.citizen_otp_not_before:
            self.citizen_otp_not_before = self._capture_otp_request_time()

        await self._wait_for_login_element("#otp")
        if self.sms_user_id and self.citizen_otp_not_before:
            self.emit(UiEvent("otp_waiting", "Waiting for Citizen OTP from the SMS server..."))
            otp = await self._wait_for_sms_otp(
                "main",
                "#otp",
                self.citizen_otp_not_before,
                timeout_seconds=MAIN_OTP_POLL_TIMEOUT_SECONDS,
            )
            if otp is not None:
                otp_field = self.page.locator("#otp").first
                if await otp_field.count() > 0 and not (await otp_field.input_value()).strip():
                    await otp_field.fill(otp)
                    self.citizen_otp_for_cleanup = otp
                    self.emit(
                        UiEvent("otp_filled", "Citizen OTP received from the SMS server and filled.")
                    )
                    await click_first(self.page, ["#btnSubmit", 'button:has-text("Login")'])
                    self.emit(UiEvent("log", "Citizen OTP login submitted automatically."))
            else:
                self.emit(
                    UiEvent("otp_manual", "Use the manually entered Citizen OTP, then click Login.")
                )
        else:
            self.emit(
                UiEvent("otp_manual", "SMS User ID is empty; enter the Citizen OTP manually.")
            )

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
        await checkbox.check(force=True)
        await click_first(self.page, ["button.close_model", 'button:has-text("OK")'])

    async def complete_egras_login(self, credentials: Credentials) -> None:
        await self._stage(Stage.EGRAS_LOGIN)
        username = await first_visible(self.page, ["#txtLoginId"], 10_000)
        if username is None:
            return
        self.egrass_otp_not_before = self._capture_otp_request_time()
        if credentials.egras_username and credentials.egras_password:
            await username.fill(credentials.egras_username)
            await fill_first(self.page, ["#txtPassword"], credentials.egras_password)
            login_captcha_entered_manually = await self._solve_captcha(
                "img.imgcaptcha",
                "#txtcaptcha",
                expected_length=6,
            )
        else:
            login_captcha_entered_manually = True

        # Manual CAPTCHA entry also makes Proceed a manual action.
        if not login_captcha_entered_manually and not await visible(self.page, "#txtOTP", 500):
            proceed_btn = await first_visible(self.page, ["#btnproceed", 'input[value="Proceed"]'], 5_000)
            if proceed_btn is not None:
                self.egrass_otp_not_before = self._capture_otp_request_time()
                await proceed_btn.click()
        await self._wait_for_egras_otp_step()

        await self._stage(Stage.EGRAS_OTP)
        # Start OTP reader task immediately in background
        otp_task: asyncio.Task[str | None] | None = None
        if self.sms_user_id and self.egrass_otp_not_before:
            self.emit(UiEvent("otp_waiting", "Waiting for eGRAS OTP from the SMS server..."))
            otp_task = asyncio.create_task(
                self._wait_for_sms_otp(
                    "egrass",
                    "#txtOTP",
                    self.egrass_otp_not_before,
                    timeout_seconds=EGRASS_OTP_POLL_TIMEOUT_SECONDS,
                )
            )

        # Solve second CAPTCHA (whether via Gemini OCR or manual entry)
        otp_captcha_entered_manually = await self._solve_captcha(
            "div.tab-pane.active img.imgcaptcha",
            "div.tab-pane.active #txtcaptcha",
            expected_length=6,
        )

        if otp_task is not None:
            otp = await otp_task
            if otp is not None:
                otp_field = self.page.locator("#txtOTP").first
                if await otp_field.count() > 0 and not (await otp_field.input_value()).strip():
                    await otp_field.fill(otp)
                    self.egrass_otp_for_cleanup = otp
                    self.emit(UiEvent("otp_filled", "OTP received from the SMS server and filled in eGRAS."))
                    if not otp_captcha_entered_manually:
                        await click_first(self.page, ["#btnproceed", 'input[value="Validate OTP"]'])
                        self.emit(UiEvent("log", "eGRAS OTP validation submitted automatically."))
            else:
                self.emit(
                    UiEvent("otp_manual", "Use the manually entered eGRAS OTP, then click Validate OTP.")
                )
        else:
            self.emit(
                UiEvent("otp_manual", "SMS User ID is empty; use the manual eGRAS OTP step.")
            )

        await self._wait_for_manual_egras_otp(
            "Confirm the eGRAS CAPTCHA and OTP, then click Validate OTP in Chrome. The app will "
            "continue when the payment option appears."
        )
        if self.egrass_otp_for_cleanup is not None:
            self._delete_used_otp_in_background("egrass", self.egrass_otp_for_cleanup)
            self.egrass_otp_for_cleanup = None

    def _capture_otp_request_time(self) -> str | None:
        """Use UTC so differing local timezones cannot admit old OTPs."""
        return self.sms_otp_client.request_time() if self.sms_user_id else None

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
                    # SBI renders the UPI QR radio asynchronously after the UPI section expands.
                    # Give its handler a moment to finish before the next stage begins polling #upiQR1.
                    await self.page.wait_for_timeout(1_000)
                    self.emit(UiEvent("log", "UPI selected on the SBI payment page."))
                    return
            await self.page.wait_for_timeout(500)

    async def select_upi_qr_and_pay(self) -> None:
        """Select UPI QR and begin the user-facing UPI payment."""
        await self._stage(Stage.PAYMENT)
        self._status("Selecting UPI QR and preparing payment…")
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed while preparing the UPI payment.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            qr_option = await first_visible(self.page, ["#upiQR1"], 500)
            if qr_option is not None:
                if not await qr_option.is_checked():
                    # SBI's UPI page can run its own selection handler without updating the
                    # native radio state. Click it, but do not fail solely on that state.
                    await qr_option.click(force=True)
                pay_now = await first_visible(self.page, ["#upiButton"], 10_000)
                if pay_now is not None:
                    await pay_now.click()
                    self.emit(UiEvent("log", "UPI QR selected and Pay Now clicked."))
                    return
            await self.page.wait_for_timeout(500)

    async def find_result_link(self) -> Locator:
        await self._stage(Stage.RESULT)
        self._status("Waiting for UPI payment completion and eStamp download…")
        while True:
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
            await self.page.wait_for_timeout(500)

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
        self._status(f"Reading {self.stage.value.replace('_', ' ')} CAPTCHA with Gemini…")
        image = await first_visible(self.page, [image_selector], 10_000)
        field = await first_visible(self.page, [input_selector], 5_000)
        if image is None or field is None:
            raise AutomationError("The CAPTCHA image or input field was not found.", stage=self.stage)
        if (await field.input_value()).strip():
            await self._manual_captcha_entered()
            return True

        solver = self.solver
        if solver is None:
            self._status("Gemini OCR is inactive; enter the CAPTCHA manually…")
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
            self._status("CAPTCHA filled; continuing with the portal…")
            self.emit(UiEvent("log", "CAPTCHA solved by Gemini."))
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
                    "Gemini could not read the CAPTCHA. Please enter it manually.",
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
        self._status("Manual CAPTCHA entered; waiting for the portal action…")
        self.emit(UiEvent("log", "Manual CAPTCHA input detected; Gemini OCR was stopped."))

    async def _wait_for_manual_captcha_input(self, field: Locator) -> None:
        self._status("Waiting for manual CAPTCHA input…")
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
