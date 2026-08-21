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
from services.credential_store import decode_credentials, encode_credentials
from services.downloads import EstampDownloader, extract_reference
from services.gemini_ocr import normalize_captcha


class ServiceTests(unittest.TestCase):
    def test_normalize_captcha_prefers_expected_length(self) -> None:
        self.assertEqual(normalize_captcha("The code is `UL1HVY`.", 6), "UL1HVY")
        self.assertEqual(normalize_captcha("answer: AB12"), "AB12")
        self.assertEqual(normalize_captcha("I cannot read it", 6), "")

    def test_extract_download_reference(self) -> None:
        self.assertEqual(
            extract_reference("https://example.test/gras_estamp_download/7489afcddfd00e8d892a"),
            "7489afcddfd00e8d892a",
        )

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
            )
            store.save(config)

            reloaded = store.load()
            self.assertEqual(reloaded.last_article, "AFFIDAVIT (Art. 4)")
            self.assertEqual(reloaded.last_csv_path, r"C:\batches\sample.csv")
            self.assertEqual(reloaded.last_mode, "continuous")
            self.assertEqual(reloaded.captcha_copy_mode, CaptchaCopyMode.MOUSE_CURSOR)

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
