from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from core.controls import RunControls
from core.models import AutomationError, Credentials, Stage, UiEvent, WorkflowStopped
from services.desktop_copy_image import copy_image_from_screen_position
from services.downloads import EstampDownloader
from services.gemini_ocr import GeminiCaptchaSolver
from services.otp_wifi import WifiOtpReceiver

CITIZEN_LOGIN_URL = "https://jharnibandhan.gov.in/Citizenentry/citizenlogin"
CITIZEN_WELCOME_URL = "https://jharnibandhan.gov.in/Citizenentry/welcome"
ESTAMP_URL = "https://jharnibandhan.gov.in/JHWebService/gras_payment_entry_estamp"
SBI_HOSTED_PAYMENT_URL = "https://epay.sbi.bank.in/secure/AggregatorHostedListener"


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
            await self.select_upi()
            await self.select_upi_qr_and_pay()
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
        if await visible(self.page, "#payment_purpose_id", 1_000):
            return
        if not await visible(self.page, "#username", 1_000):
            await self._goto(CITIZEN_LOGIN_URL, timeout_ms=0)
        await self._wait_for_login_element("#username")
        if not credentials.citizen_username or not credentials.citizen_password:
            await self._wait_for_manual_citizen_login(
                "Enter the Citizen username, password, CAPTCHA, and OTP in Chrome. Click Get OTP "
                "then Login. The app will continue when the Citizen welcome page opens."
            )
        else:
            await fill_first(self.page, ["#username"], credentials.citizen_username)
            await fill_first(self.page, ["#password"], credentials.citizen_password)
            captcha_entered_manually = await self._solve_captcha(
                "#captcha_image",
                "#captcha",
                expected_length=6,
            )
            if not captcha_entered_manually:
                otp_button = await self._wait_for_login_element("#btnotp")
                citizen_otp_sequence = self.otp_receiver.sequence
                await otp_button.click()
                await self._wait_for_login_element("#otp")
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
            await self._wait_for_manual_citizen_login(
                "Waiting for the Citizen welcome page after the user completes the login steps."
            )

        await self._open_estamp_entry()

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
        """Open eStamp directly after login; retry portal-aborted navigations."""
        self._status("Opening the direct eStamp form…")
        self.emit(UiEvent("log", "Opening the direct eStamp form."))
        while True:
            await self.controls.checkpoint()
            if self.page.is_closed():
                raise AutomationError(
                    "The portal browser was closed while opening the eStamp form.",
                    stage=self.stage,
                    code="browser_closed",
                    retryable=False,
                )
            if await visible(self.page, "#payment_purpose_id", 500):
                return
            try:
                # The Citizen portal can abort a normal DOM-content-loaded
                # navigation while it completes its own redirect. Committing
                # the request is enough; the form itself is then polled.
                await self.page.goto(ESTAMP_URL, wait_until="commit", timeout=60_000)
            except PlaywrightError as error:
                if self.page.is_closed():
                    raise AutomationError(
                        "The portal browser was closed while opening the eStamp form.",
                        stage=self.stage,
                        code="browser_closed",
                        retryable=False,
                    ) from error
                self._status("eStamp navigation was interrupted; retrying…")
                self.emit(UiEvent("log", "eStamp navigation was interrupted; retrying."))
            await self.page.wait_for_timeout(500)

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
            await self._wait_for_manual_egras_login(
                "Complete the eGRAS username, password, and CAPTCHA in Chrome, then click Proceed. "
                "The app will continue when the OTP page appears."
            )
        else:
            await username.fill(credentials.egras_username)
            await fill_first(self.page, ["#txtPassword"], credentials.egras_password)
            self.otp_sequence_before_egras = self.otp_receiver.sequence
            captcha_entered_manually = await self._solve_captcha(
                "img.imgcaptcha",
                "#txtcaptcha",
                expected_length=6,
            )
            if not captcha_entered_manually:
                await click_first(self.page, ["#btnproceed", 'input[value="Proceed"]'])
            await self._wait_for_egras_otp_step()

        await self._stage(Stage.EGRAS_OTP)
        # eGRAS presents a new CAPTCHA beside the OTP field after the login
        # CAPTCHA was accepted. The server reuses the same IDs, so the active
        # tab's visible controls are targeted.
        await self._solve_captcha(
            "div.tab-pane.active img.imgcaptcha",
            "div.tab-pane.active #txtcaptcha",
            expected_length=6,
        )
        if self.otp_auto_fill and self.otp_receiver.is_paired:
            self.emit(UiEvent("otp_waiting", "Waiting up to 45 seconds for a paired phone OTP..."))
            otp = await self.otp_receiver.wait_for_new(self.otp_sequence_before_egras, timeout_seconds=45)
            if otp is not None:
                await self.page.locator("#txtOTP").fill(otp)
                self.emit(UiEvent("otp_filled", "OTP received from the paired phone and filled in eGRAS."))
        elif self.otp_auto_fill:
            self.emit(
                UiEvent("otp_manual", "No paired phone is connected; use the manual eGRAS OTP step.")
            )
        await self._wait_for_manual_egras_otp(
            "Confirm the eGRAS CAPTCHA and OTP, then click Validate OTP in Chrome. The app will "
            "continue when the payment option appears."
        )

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
                    await qr_option.check(force=True)
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
            await self._goto(ESTAMP_URL)

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

        ocr_task: asyncio.Task[str] | None = None
        try:
            await self._copy_captcha_with_browser_menu(image)
            ocr_task = asyncio.create_task(self.solver.solve(expected_length))
            while not ocr_task.done():
                await self.controls.checkpoint()
                if (await field.input_value()).strip():
                    ocr_task.cancel()
                    await asyncio.gather(ocr_task, return_exceptions=True)
                    await self.solver.cancel_active_response()
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
                await self.solver.cancel_active_response()

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
