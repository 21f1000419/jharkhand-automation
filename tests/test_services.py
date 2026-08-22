from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from playwright.async_api import Download
from playwright.async_api import Error as PlaywrightError

from automation.portal import PortalAutomation, transaction_details_from_rows
from core.config import AppConfig, ConfigStore
from core.controls import RunControls
from core.models import CaptchaCopyMode, Credentials, WorkflowStopped
from services.captcha_ocr import join_ocr_fragments, normalize_captcha
from services.credential_store import decode_credentials, encode_credentials
from services.ddddocr_ocr import normalize_ddddocr_captcha
from services.downloads import EstampDownloader, extract_reference
from services.payment_trigger import send_payment_trigger_request


class ServiceTests(unittest.TestCase):
    def test_normalize_captcha_prefers_expected_length(self) -> None:
        self.assertEqual(normalize_captcha("The code is `UL1HVY`.", 6), "UL1HVY")
        self.assertEqual(normalize_captcha("answer: AB12"), "AB12")
        self.assertEqual(normalize_captcha("I cannot read it", 6), "")

    def test_normalize_captcha_uppercases_alphanumeric_values(self) -> None:
        self.assertEqual(normalize_captcha("ab12cd", 6), "AB12CD")

    def test_normalize_ddddocr_captcha_prefers_expected_length(self) -> None:
        self.assertEqual(normalize_ddddocr_captcha("captcha: ab12cd", 6), "AB12CD")
        self.assertEqual(normalize_ddddocr_captcha("ab12"), "AB12")

    def test_join_ocr_fragments_uses_left_to_right_order(self) -> None:
        results = [
            ([[100, 0], [150, 0], [150, 30], [100, 30]], "CXJ", 0.9),
            ([[0, 0], [70, 0], [70, 30], [0, 30]], "1C8", 0.9),
        ]

        self.assertEqual(join_ocr_fragments(results, 6), "1C8CXJ")

    def test_join_ocr_fragments_requires_the_expected_length(self) -> None:
        results = [
            ([[0, 0], [30, 0], [30, 30], [0, 30]], "ABC", 0.9),
            ([[50, 0], [80, 0], [80, 30], [50, 30]], "12", 0.9),
        ]

        self.assertEqual(join_ocr_fragments(results, 6), "")

    def test_extract_download_reference(self) -> None:
        self.assertEqual(
            extract_reference("https://example.test/gras_estamp_download/7489afcddfd00e8d892a"),
            "7489afcddfd00e8d892a",
        )

    def test_payment_trigger_supports_get_and_post(self) -> None:
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                response = MagicMock()
                response.status = 202
                response.read.return_value = b""
                context = MagicMock()
                context.__enter__.return_value = response
                with patch("services.payment_trigger.urlopen", return_value=context) as open_url:
                    status = asyncio.run(
                        send_payment_trigger_request(
                            "https://payment-trigger.example/start",
                            method,
                        )
                    )

                request = open_url.call_args.args[0]
                self.assertEqual(request.get_method(), method)
                self.assertEqual(request.data, b"" if method == "POST" else None)
                self.assertEqual(status, 202)

    def test_transaction_confirmation_table_details_are_normalized(self) -> None:
        details = transaction_details_from_rows(
            [
                ["Name", " MahindraAndMahindraFinancialServices "],
                ["Token No / Depositor ID", "C157925"],
                ["Amount", "20"],
                ["Transaction ID", "15a44b09719f32951d72"],
                ["GRN", "2604247253"],
                ["CIN", "10002162026082107215"],
                ["Time", "2026-08-21 17:16:10"],
            ]
        )

        self.assertEqual(details["Name"], "MahindraAndMahindraFinancialServices")
        self.assertEqual(details["Token No / Depositor ID"], "C157925")
        self.assertEqual(details["Transaction ID"], "15a44b09719f32951d72")
        self.assertEqual(details["GRN"], "2604247253")
        self.assertEqual(details["CIN"], "10002162026082107215")

    def test_confirmed_transaction_survives_pdf_download_failure(self) -> None:
        page = MagicMock()
        events: list[object] = []
        portal = PortalAutomation(
            page,
            None,
            RunControls(lambda _event: None),
            AsyncMock(),
            events.append,
            MagicMock(),
            "",
            CaptchaCopyMode.DIRECT,
        )
        for method_name in (
            "ensure_citizen_session",
            "fill_estamp_form",
            "confirm_estamp",
            "accept_egras_terms",
            "complete_egras_login",
            "choose_gateway",
            "accept_gateway_terms",
            "select_upi",
            "select_upi_qr_and_pay",
            "wait_for_upi_qr_and_trigger",
        ):
            setattr(portal, method_name, AsyncMock())
        details = {
            "Transaction ID": "transaction-123",
            "GRN": "grn-123",
            "CIN": "cin-123",
        }
        portal.find_result_details = AsyncMock(return_value=details)  # type: ignore[method-assign]
        portal._stage = AsyncMock()  # type: ignore[method-assign]
        link = MagicMock()

        with (
            patch("automation.portal.first_visible", new=AsyncMock(return_value=link)),
            patch("automation.portal.EstampDownloader") as downloader_type,
        ):
            downloader_type.return_value.download = AsyncMock(side_effect=RuntimeError("HTTP 500"))
            result = asyncio.run(
                portal.process_unit(
                    {},
                    "Article",
                    Credentials(),
                    Path("downloads"),
                    1,
                    1,
                )
            )

        self.assertEqual(result.reference, "transaction-123")
        self.assertEqual(result.details, details)
        self.assertIsNone(result.destination)
        self.assertEqual(result.download_error, "HTTP 500")

    def test_egras_terms_checkbox_uses_native_fallback(self) -> None:
        portal = PortalAutomation(
            MagicMock(),
            None,
            RunControls(lambda _event: None),
            AsyncMock(),
            lambda _event: None,
            MagicMock(),
            "",
            CaptchaCopyMode.DIRECT,
        )
        portal._stage = AsyncMock()  # type: ignore[method-assign]
        checkbox = MagicMock()
        checkbox.check = AsyncMock(side_effect=PlaywrightError("Checkbox state did not change"))
        checkbox.evaluate = AsyncMock()
        checkbox.is_checked = AsyncMock(return_value=True)

        with (
            patch("automation.portal.first_visible", new=AsyncMock(return_value=checkbox)),
            patch("automation.portal.click_first", new=AsyncMock()) as click,
        ):
            asyncio.run(portal.accept_egras_terms())

        checkbox.evaluate.assert_awaited_once()
        click.assert_awaited_once()

    def test_upi_qr_uses_javascript_fallback_before_clicking_pay(self) -> None:
        page = MagicMock()
        page.is_closed.return_value = False
        page.wait_for_timeout = AsyncMock()
        qr_option = MagicMock()
        qr_option.count = AsyncMock(return_value=1)
        qr_option.is_checked = AsyncMock(side_effect=[False, False, True])
        qr_option.click = AsyncMock()
        qr_option.evaluate = AsyncMock(return_value=True)
        page.locator.return_value.first = qr_option
        pay_now = MagicMock()
        pay_now.click = AsyncMock()
        portal = PortalAutomation(
            page,
            None,
            RunControls(lambda _event: None),
            AsyncMock(),
            lambda _event: None,
            MagicMock(),
            "",
            CaptchaCopyMode.DIRECT,
        )
        portal._stage = AsyncMock()  # type: ignore[method-assign]

        with patch("automation.portal.first_visible", new=AsyncMock(return_value=pay_now)):
            asyncio.run(portal.select_upi_qr_and_pay())

        qr_option.click.assert_awaited_once()
        qr_option.evaluate.assert_awaited_once()
        pay_now.click.assert_awaited_once()

    def test_upi_qr_unverified_hands_payment_to_user_without_failing(self) -> None:
        page = MagicMock()
        page.is_closed.return_value = False
        page.wait_for_timeout = AsyncMock()
        qr_option = MagicMock()
        qr_option.count = AsyncMock(return_value=1)
        qr_option.is_checked = AsyncMock(return_value=False)
        qr_option.click = AsyncMock()
        qr_option.evaluate = AsyncMock(return_value=False)
        page.locator.return_value.first = qr_option
        pay_now = MagicMock()
        pay_now.click = AsyncMock()
        events: list[object] = []
        portal = PortalAutomation(
            page,
            None,
            RunControls(lambda _event: None),
            AsyncMock(),
            events.append,
            MagicMock(),
            "",
            CaptchaCopyMode.DIRECT,
        )
        portal._stage = AsyncMock()  # type: ignore[method-assign]

        with patch("automation.portal.first_visible", new=AsyncMock(return_value=pay_now)):
            asyncio.run(portal.select_upi_qr_and_pay())

        pay_now.click.assert_not_awaited()
        self.assertTrue(
            any(getattr(event, "kind", "") == "notification" for event in events),
            "Manual UPI takeover should notify the user.",
        )

    def test_payment_trigger_runs_after_both_upi_qr_markers_appear(self) -> None:
        page = MagicMock()
        page.is_closed.return_value = False
        page.wait_for_timeout = AsyncMock()
        body = MagicMock()
        body.inner_text = AsyncMock(
            side_effect=[
                "Scan UPI QR",
                "Scan UPI QR\nTime left to complete the transaction 04:59",
            ]
        )
        page.locator.return_value = body
        events: list[object] = []
        portal = PortalAutomation(
            page,
            None,
            RunControls(lambda _event: None),
            AsyncMock(),
            events.append,
            MagicMock(),
            "",
            CaptchaCopyMode.DIRECT,
            "https://payment-trigger.example/start",
            "POST",
        )

        with patch(
            "automation.portal.send_payment_trigger_request",
            new=AsyncMock(return_value=204),
        ) as trigger:
            asyncio.run(portal.wait_for_upi_qr_and_trigger())

        trigger.assert_awaited_once_with("https://payment-trigger.example/start", "POST")
        page.wait_for_timeout.assert_awaited_once_with(250)
        self.assertTrue(any("HTTP 204" in getattr(event, "message", "") for event in events))

    def test_payment_trigger_failure_is_logged_and_ignored(self) -> None:
        page = MagicMock()
        page.is_closed.return_value = False
        body = MagicMock()
        body.inner_text = AsyncMock(
            return_value="Scan UPI QR Time left to complete the transaction"
        )
        page.locator.return_value = body
        events: list[object] = []
        portal = PortalAutomation(
            page,
            None,
            RunControls(lambda _event: None),
            AsyncMock(),
            events.append,
            MagicMock(),
            "",
            CaptchaCopyMode.DIRECT,
            "https://payment-trigger.example/start",
            "GET",
        )

        with patch(
            "automation.portal.send_payment_trigger_request",
            new=AsyncMock(side_effect=RuntimeError("connection refused")),
        ):
            asyncio.run(portal.wait_for_upi_qr_and_trigger())

        self.assertTrue(
            any(
                "failed and was ignored" in getattr(event, "message", "")
                for event in events
            )
        )

    def test_download_retries_in_live_browser_and_saves_pdf(self) -> None:
        pdf = b"%PDF-1.7\n" + (b"valid-pdf-data" * 50)
        page = MagicMock()
        page.url = "https://example.test/payment/result"
        page.evaluate = AsyncMock(
            side_effect=[
                {"status": 500, "contentType": "text/html", "body": ""},
                {
                    "status": 200,
                    "contentType": "application/pdf",
                    "body": base64.b64encode(pdf).decode("ascii"),
                },
            ]
        )
        link = MagicMock()
        link.get_attribute = AsyncMock(
            return_value="/JHWebService/gras_estamp_download/reference123"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("services.downloads.asyncio.sleep", new=AsyncMock()) as sleep:
                destination, reference = asyncio.run(
                    EstampDownloader().download(
                        page,
                        link,
                        Path(temp_dir),
                        2,
                        3,
                    )
                )

            self.assertEqual(reference, "reference123")
            self.assertEqual(destination.read_bytes(), pdf)
            self.assertEqual(page.evaluate.await_count, 2)
            sleep.assert_awaited_once_with(1)

    def test_download_clicks_estamp_button_after_fetch_retries_fail(self) -> None:
        pdf = b"%PDF-1.7\n" + (b"native-download-data" * 40)
        page = MagicMock()
        page.url = "https://example.test/payment/result"
        page.evaluate = AsyncMock(
            return_value={"status": 500, "contentType": "text/html", "body": ""}
        )
        link = MagicMock()
        link.get_attribute = AsyncMock(
            return_value="/JHWebService/gras_estamp_download/native123"
        )
        link.click = AsyncMock()
        download = MagicMock(spec=Download)
        download.failure = AsyncMock(return_value=None)

        async def save_pdf(path: Path) -> None:
            path.write_bytes(pdf)

        download.save_as = AsyncMock(side_effect=save_pdf)
        page.wait_for_event = AsyncMock(return_value=download)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("services.downloads.asyncio.sleep", new=AsyncMock()):
                destination, reference = asyncio.run(
                    EstampDownloader().download(
                        link=link,
                        page=page,
                        output_directory=Path(temp_dir),
                        row_number=1,
                        sequence=1,
                    )
                )

            self.assertEqual(reference, "native123")
            self.assertEqual(destination.read_bytes(), pdf)
            self.assertEqual(page.evaluate.await_count, 3)
            link.click.assert_awaited_once_with()
            page.wait_for_event.assert_awaited_once_with("download", timeout=60_000)

    def test_short_ocr_result_refreshes_captcha_before_retrying(self) -> None:
        page = MagicMock()

        async def yield_to_ocr_task(_milliseconds: int) -> None:
            await asyncio.sleep(0)

        page.wait_for_timeout = AsyncMock(side_effect=yield_to_ocr_task)
        controls = RunControls(lambda _event: None)
        solver = MagicMock()
        solver.solve_image = AsyncMock(side_effect=["AB12", "ABCDEF"])
        solver.cancel_active_response = AsyncMock()
        portal = PortalAutomation(
            page,
            solver,
            controls,
            AsyncMock(),
            lambda _event: None,
            MagicMock(),
            "",
            CaptchaCopyMode.DIRECT,
        )
        image = MagicMock()
        field = MagicMock()
        field.input_value = AsyncMock(return_value="")
        field.fill = AsyncMock()
        portal._capture_captcha_directly = AsyncMock(side_effect=[b"first", b"second"])  # type: ignore[method-assign]
        portal._refresh_captcha = AsyncMock()  # type: ignore[method-assign]

        with patch(
            "automation.portal.first_visible",
            new=AsyncMock(side_effect=[image, field]),
        ):
            entered_manually = asyncio.run(
                portal._solve_captcha(
                    "img.imgcaptcha",
                    "#txtcaptcha",
                    expected_length=6,
                    refresh_selector="#ImageButton1",
                )
            )

        self.assertFalse(entered_manually)
        portal._refresh_captcha.assert_awaited_once_with(image, "#ImageButton1")
        field.fill.assert_awaited_once_with("ABCDEF")

    def test_manual_citizen_captcha_does_not_click_get_otp(self) -> None:
        page = MagicMock()
        controls = RunControls(lambda _event: None)
        portal = PortalAutomation(
            page,
            None,
            controls,
            AsyncMock(),
            lambda _event: None,
            AsyncMock(),
            "",
            CaptchaCopyMode.DIRECT,
        )
        portal._solve_captcha = AsyncMock(return_value=True)  # type: ignore[method-assign]
        portal._wait_for_login_element = AsyncMock()  # type: ignore[method-assign]
        portal._wait_for_manual_citizen_login = AsyncMock()  # type: ignore[method-assign]
        portal._open_estamp_entry = AsyncMock()  # type: ignore[method-assign]

        async def is_visible(_page: object, selector: str, _timeout: int) -> bool:
            return selector == "#username"

        get_otp = AsyncMock()
        with (
            patch("automation.portal.visible", side_effect=is_visible),
            patch("automation.portal.fill_first", new=AsyncMock()),
            patch("automation.portal.first_visible", new=AsyncMock(return_value=get_otp)) as find,
        ):
            asyncio.run(portal.ensure_citizen_session(Credentials("user", "pass")))

        find.assert_not_awaited()
        get_otp.click.assert_not_awaited()

    def test_automatic_citizen_otp_clicks_login(self) -> None:
        page = MagicMock()
        controls = RunControls(lambda _event: None)
        sms_client = MagicMock()
        sms_client.request_time.return_value = "2026-08-21T10:00:00Z"
        portal = PortalAutomation(
            page,
            None,
            controls,
            AsyncMock(),
            lambda _event: None,
            sms_client,
            "sms-user",
            CaptchaCopyMode.DIRECT,
        )
        portal._solve_captcha = AsyncMock(return_value=False)  # type: ignore[method-assign]
        portal._wait_for_login_element = AsyncMock()  # type: ignore[method-assign]
        portal._wait_for_sms_otp = AsyncMock(return_value="123456")  # type: ignore[method-assign]
        portal._wait_for_manual_citizen_login = AsyncMock()  # type: ignore[method-assign]
        portal._open_estamp_entry = AsyncMock()  # type: ignore[method-assign]
        portal._delete_used_otp_in_background = MagicMock()  # type: ignore[method-assign]
        otp_field = MagicMock()
        otp_field.count = AsyncMock(return_value=1)
        otp_field.input_value = AsyncMock(return_value="")
        otp_field.fill = AsyncMock()
        page.locator.return_value.first = otp_field
        get_otp_button = MagicMock()
        get_otp_button.click = AsyncMock()
        login_button = MagicMock()
        login_button.click = AsyncMock()

        async def is_visible(_page: object, selector: str, _timeout: int) -> bool:
            return selector == "#username"

        with (
            patch("automation.portal.visible", side_effect=is_visible),
            patch("automation.portal.fill_first", new=AsyncMock()),
            patch(
                "automation.portal.first_visible",
                new=AsyncMock(side_effect=[get_otp_button, login_button]),
            ),
        ):
            asyncio.run(portal.ensure_citizen_session(Credentials("user", "pass")))

        otp_field.fill.assert_awaited_once_with("123456")
        get_otp_button.click.assert_awaited_once_with()
        login_button.click.assert_awaited_once_with()

    def test_manual_citizen_captcha_with_automatic_otp_clicks_login(self) -> None:
        page = MagicMock()
        controls = RunControls(lambda _event: None)
        sms_client = MagicMock()
        sms_client.request_time.return_value = "2026-08-21T10:00:00Z"
        portal = PortalAutomation(
            page,
            None,
            controls,
            AsyncMock(),
            lambda _event: None,
            sms_client,
            "sms-user",
            CaptchaCopyMode.DIRECT,
        )
        portal._solve_captcha = AsyncMock(return_value=True)  # type: ignore[method-assign]
        portal._wait_for_login_element = AsyncMock()  # type: ignore[method-assign]
        portal._wait_for_sms_otp = AsyncMock(return_value="123456")  # type: ignore[method-assign]
        portal._wait_for_manual_citizen_login = AsyncMock()  # type: ignore[method-assign]
        portal._open_estamp_entry = AsyncMock()  # type: ignore[method-assign]
        portal._delete_used_otp_in_background = MagicMock()  # type: ignore[method-assign]
        otp_field = MagicMock()
        otp_field.count = AsyncMock(return_value=1)
        otp_field.input_value = AsyncMock(return_value="")
        otp_field.fill = AsyncMock()
        page.locator.return_value.first = otp_field

        async def is_visible(_page: object, selector: str, _timeout: int) -> bool:
            return selector == "#username"

        with (
            patch("automation.portal.visible", side_effect=is_visible),
            patch("automation.portal.fill_first", new=AsyncMock()),
            patch("automation.portal.first_visible", new=AsyncMock()) as find,
            patch("automation.portal.click_first", new=AsyncMock()) as click,
        ):
            asyncio.run(portal.ensure_citizen_session(Credentials("user", "pass")))

        find.assert_not_awaited()
        otp_field.fill.assert_awaited_once_with("123456")
        click.assert_awaited_once_with(page, ["#btnSubmit", 'button:has-text("Login")'])

    def test_citizen_captcha_failure_retries_credentials_and_captcha(self) -> None:
        page = MagicMock()
        controls = RunControls(lambda _event: None)
        portal = PortalAutomation(
            page,
            None,
            controls,
            AsyncMock(),
            lambda _event: None,
            MagicMock(),
            "",
            CaptchaCopyMode.DIRECT,
        )
        portal._wait_for_login_element = AsyncMock()
        portal._solve_captcha = AsyncMock(side_effect=[False, False])  # type: ignore[method-assign]
        portal._wait_for_login_progress = AsyncMock(side_effect=["captcha_failed", "otp"])  # type: ignore[method-assign]
        portal._wait_for_manual_citizen_login = AsyncMock()  # type: ignore[method-assign]
        portal._open_estamp_entry = AsyncMock()  # type: ignore[method-assign]
        portal._await_otp_watcher = AsyncMock(return_value=None)  # type: ignore[method-assign]
        portal._cancel_otp_watcher = AsyncMock()  # type: ignore[method-assign]
        portal._start_otp_watcher = MagicMock(return_value=None)  # type: ignore[method-assign]

        async def is_visible(_page: object, selector: str, _timeout: int) -> bool:
            return selector == "#username"

        with (
            patch("automation.portal.visible", side_effect=is_visible),
            patch("automation.portal.fill_first", new=AsyncMock()) as fill,
        ):
            asyncio.run(portal.ensure_citizen_session(Credentials("user", "pass")))

        self.assertEqual(fill.await_count, 4)
        self.assertEqual(portal._solve_captcha.await_count, 2)

    def test_egras_captcha_failure_retries_credentials_and_captcha(self) -> None:
        page = MagicMock()
        controls = RunControls(lambda _event: None)
        portal = PortalAutomation(
            page,
            None,
            controls,
            AsyncMock(),
            lambda _event: None,
            MagicMock(),
            "",
            CaptchaCopyMode.DIRECT,
        )
        portal._solve_captcha = AsyncMock(side_effect=[False, False])  # type: ignore[method-assign]
        portal._wait_for_login_progress = AsyncMock(side_effect=["captcha_failed", "otp"])  # type: ignore[method-assign]
        portal._wait_for_egras_otp_step = AsyncMock()  # type: ignore[method-assign]
        portal._wait_for_manual_egras_otp = AsyncMock()  # type: ignore[method-assign]
        portal._await_otp_watcher = AsyncMock(return_value=None)  # type: ignore[method-assign]
        portal._cancel_otp_watcher = AsyncMock()  # type: ignore[method-assign]
        portal._start_otp_watcher = MagicMock(return_value=None)  # type: ignore[method-assign]
        username = MagicMock()
        username.fill = AsyncMock()

        with (
            patch("automation.portal.fill_first", new=AsyncMock()) as fill,
            patch("automation.portal.first_visible", new=AsyncMock(return_value=username)),
        ):
            asyncio.run(
                portal.complete_egras_login(
                    Credentials(egras_username="egras-user", egras_password="egras-pass")
                )
            )

        self.assertEqual(username.fill.await_count, 2)
        self.assertEqual(fill.await_count, 2)
        self.assertEqual(portal._solve_captcha.await_count, 2)

    def test_automatic_egras_captcha_and_otp_submit_validation(self) -> None:
        page = MagicMock()
        controls = RunControls(lambda _event: None)
        sms_client = MagicMock()
        sms_client.request_time.return_value = "2026-08-21T10:00:00Z"
        portal = PortalAutomation(
            page,
            None,
            controls,
            AsyncMock(),
            lambda _event: None,
            sms_client,
            "sms-user",
            CaptchaCopyMode.DIRECT,
        )
        portal._solve_captcha = AsyncMock(side_effect=[False, False])  # type: ignore[method-assign]
        portal._wait_for_login_element = AsyncMock()  # type: ignore[method-assign]
        portal._wait_for_sms_otp = AsyncMock(return_value="A09AFD")  # type: ignore[method-assign]
        portal._wait_for_egras_otp_step = AsyncMock()  # type: ignore[method-assign]
        portal._wait_for_manual_egras_otp = AsyncMock()  # type: ignore[method-assign]
        portal._delete_used_otp_in_background = MagicMock()  # type: ignore[method-assign]
        username = MagicMock()
        username.fill = AsyncMock()
        proceed = MagicMock()
        proceed.click = AsyncMock()
        otp_field = MagicMock()
        otp_field.count = AsyncMock(return_value=1)
        otp_field.input_value = AsyncMock(return_value="")
        otp_field.fill = AsyncMock()
        page.locator.return_value.first = otp_field

        with (
            patch("automation.portal.visible", new=AsyncMock(return_value=False)),
            patch("automation.portal.fill_first", new=AsyncMock()),
            patch(
                "automation.portal.first_visible",
                new=AsyncMock(side_effect=[username, proceed]),
            ),
            patch("automation.portal.click_first", new=AsyncMock()) as click,
        ):
            asyncio.run(
                portal.complete_egras_login(
                    Credentials(egras_username="egras-user", egras_password="egras-pass")
                )
            )

        proceed.click.assert_awaited_once_with()
        otp_field.fill.assert_awaited_once_with("A09AFD")
        click.assert_awaited_once_with(page, ["#btnproceed", 'input[value="Validate OTP"]'])

    def test_manual_egras_captchas_do_not_submit_portal_actions(self) -> None:
        page = MagicMock()
        controls = RunControls(lambda _event: None)
        sms_client = MagicMock()
        sms_client.request_time.return_value = "2026-08-21T10:00:00Z"
        portal = PortalAutomation(
            page,
            None,
            controls,
            AsyncMock(),
            lambda _event: None,
            sms_client,
            "sms-user",
            CaptchaCopyMode.DIRECT,
        )
        portal._solve_captcha = AsyncMock(side_effect=[True, True])  # type: ignore[method-assign]
        portal._wait_for_login_element = AsyncMock()  # type: ignore[method-assign]
        portal._wait_for_sms_otp = AsyncMock(return_value="123456")  # type: ignore[method-assign]
        portal._wait_for_egras_otp_step = AsyncMock()  # type: ignore[method-assign]
        portal._wait_for_manual_egras_otp = AsyncMock()  # type: ignore[method-assign]
        portal._delete_used_otp_in_background = MagicMock()  # type: ignore[method-assign]
        username = MagicMock()
        username.fill = AsyncMock()
        otp_field = MagicMock()
        otp_field.count = AsyncMock(return_value=1)
        otp_field.input_value = AsyncMock(return_value="")
        otp_field.fill = AsyncMock()
        page.locator.return_value.first = otp_field

        with (
            patch("automation.portal.fill_first", new=AsyncMock()),
            patch("automation.portal.first_visible", new=AsyncMock(return_value=username)) as find,
            patch("automation.portal.click_first", new=AsyncMock()) as click,
        ):
            asyncio.run(
                portal.complete_egras_login(
                    Credentials(egras_username="egras-user", egras_password="egras-pass")
                )
            )

        find.assert_awaited_once_with(page, ["#txtLoginId"], 10_000)
        self.assertEqual(portal._solve_captcha.await_count, 2)
        otp_field.fill.assert_awaited_once_with("123456")
        click.assert_not_awaited()

    def test_saved_credentials_round_trip(self) -> None:
        credentials = Credentials("citizen-user", "citizen-pass", "egras-user", "egras-pass")
        self.assertEqual(decode_credentials(encode_credentials(credentials)), credentials)

    def test_run_controls_stop_interrupts_checkpoint(self) -> None:
        controls = RunControls(lambda _event: None)
        controls.stop()
        with self.assertRaises(WorkflowStopped):
            asyncio.run(controls.checkpoint())

    def test_error_decision_queue(self) -> None:
        controls = RunControls(lambda _event: None)
        controls.decide("next")
        self.assertEqual(asyncio.run(controls.wait_for_decision()), "next")
    def test_config_store_persists_article_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            store = ConfigStore(settings_path)
            config = AppConfig(
                last_article="AFFIDAVIT (Art. 4)",
                last_csv_path=r"C:\batches\sample.csv",
                last_mode="continuous",
                captcha_copy_mode=CaptchaCopyMode.MOUSE_CURSOR,
                payment_trigger_url="https://payment-trigger.example/start",
                payment_trigger_method="POST",
            )
            store.save(config)

            reloaded = store.load()
            self.assertEqual(reloaded.last_article, "AFFIDAVIT (Art. 4)")
            self.assertEqual(reloaded.last_csv_path, r"C:\batches\sample.csv")
            self.assertEqual(reloaded.last_mode, "continuous")
            self.assertEqual(reloaded.captcha_copy_mode, CaptchaCopyMode.MOUSE_CURSOR)
            self.assertEqual(
                reloaded.payment_trigger_url,
                "https://payment-trigger.example/start",
            )
            self.assertEqual(reloaded.payment_trigger_method, "POST")

    def test_config_store_handles_missing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(
                json.dumps({"last_mode": "assisted", "chrome_executable": "chrome.exe"}),
                encoding="utf-8",
            )
            store = ConfigStore(settings_path)
            loaded = store.load()
            self.assertEqual(loaded.last_article, "")
            self.assertEqual(loaded.last_csv_path, "")
            self.assertEqual(loaded.last_mode, "assisted")

if __name__ == "__main__":
    unittest.main()
