from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from playwright.async_api import Page

from core.controls import RunControls
from core.models import BrowserEngine, Credentials, PortalBrowser, WorkflowStopped
from services.finance_transactions import (
    FINANCE_HEADERS,
    FinanceExportFilters,
    FinanceExportTarget,
    _login,
    _write_finance_csv,
    collect_finance_pages,
    export_finance_transactions_batch,
    extract_finance_otp_reference,
    filter_finance_rows,
    unique_egras_targets,
)
from services.sms_otp_client import SmsOtpServerError


def transaction(
    grn: str = "2605107970",
    entry: str = "06-OCT-2026 01:14:32",
    name: str = "CREDITWISECAPITALPRIVATELIMITED",
    amount: str = "10.00",
    status: str = "SUCCESS",
) -> list[str]:
    return [
        "1",
        grn,
        name,
        entry,
        "06-OCT-2026 01:18:49",
        "NA",
        "10002162026100610106",
        "0694874866713",
        "SBIEPAY",
        amount,
        status,
        "",
        "",
    ]


def target(username: str = "egras", password: str = "password") -> FinanceExportTarget:
    return FinanceExportTarget(
        name=username,
        browser=PortalBrowser("Chrome", Path("chrome.exe"), BrowserEngine.CHROMIUM),
        profile_path=Path("finance-test-profile"),
        credentials=Credentials(egras_username=username, egras_password=password),
        sms_server_url="https://sms.example",
    )


def mock_page() -> MagicMock:
    page = MagicMock()
    page.is_closed.return_value = False
    page.url = "https://finance.jharkhand.gov.in/jegras/Login.aspx"
    page.evaluate = AsyncMock()
    locator = page.locator.return_value
    locator.click = AsyncMock()
    locator.wait_for = AsyncMock()
    locator.fill = AsyncMock()
    locator.is_visible = AsyncMock(return_value=False)
    locator.input_value = AsyncMock(return_value="")
    locator.text_content = AsyncMock(return_value="")
    page.expect_navigation.return_value.__aenter__ = AsyncMock()
    page.expect_navigation.return_value.__aexit__ = AsyncMock()
    return page


class FinanceFilterTests(unittest.TestCase):
    def test_optional_filters_use_lowercase_contains_and_decimal_amount(self) -> None:
        rows = [
            transaction(),
            transaction("2", amount="11"),
            transaction("3", name="Other"),
            transaction("4", status="FAILED"),
        ]
        selected, stopped = filter_finance_rows(
            rows, FinanceExportFilters(name="  WiseCapital  ", amount=Decimal("10"))
        )
        self.assertEqual(selected, [rows[0]])
        self.assertFalse(stopped)

    def test_inclusive_dates_and_cutoff_apply_before_name_and_amount(self) -> None:
        rows = [
            transaction("new", "07-OCT-2026 00:00:00"),
            transaction("end", "06-OCT-2026 23:59:59"),
            transaction("start", "05-OCT-2026 00:00:00"),
            transaction("old", "04-OCT-2026 23:59:59", name="Other", amount="100"),
            transaction("unvisited", "03-OCT-2026 00:00:00"),
        ]
        selected, stopped = filter_finance_rows(
            rows,
            FinanceExportFilters(
                name="capital",
                amount=Decimal("10.00"),
                date_from=date(2026, 10, 5),
                date_to=date(2026, 10, 6),
            ),
        )
        self.assertEqual([row[1] for row in selected], ["end", "start"])
        self.assertTrue(stopped)

    def test_end_date_alone_skips_newer_rows_without_stopping(self) -> None:
        selected, stopped = filter_finance_rows(
            [transaction("new"), transaction("old", "05-OCT-2026 12:00:00")],
            FinanceExportFilters(date_to=date(2026, 10, 5)),
        )
        self.assertEqual([row[1] for row in selected], ["old"])
        self.assertFalse(stopped)

    def test_unreadable_dates_fail_instead_of_silently_omitting_rows(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Cannot read the entry date"):
            filter_finance_rows([transaction(entry="bad")], FinanceExportFilters(date_from=date(2026, 10, 1)))

    def test_rejects_invalid_filters(self) -> None:
        for filters in (
            FinanceExportFilters(date_from=date(2026, 10, 6), date_to=date(2026, 10, 5)),
            FinanceExportFilters(amount=Decimal("NaN")),
            FinanceExportFilters(amount=Decimal("-1")),
        ):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                filters.validate()

    def test_unique_egras_accounts_prefer_complete_credentials(self) -> None:
        targets = [target(" EGRAS ", ""), target("egras"), target("Other"), target("")]
        self.assertEqual(unique_egras_targets(targets), targets[1:3])

    def test_registered_login_reference_format(self) -> None:
        self.assertEqual(
            extract_finance_otp_reference(
                "OTP has been sent to your registered Mobile No. 78XXXX8225. with reference no. 1542714"
            ),
            "1542714",
        )
        self.assertEqual(extract_finance_otp_reference("OTP Reference is 2127753"), "2127753")
        self.assertEqual(extract_finance_otp_reference("OTP sent to mobile 123456"), "")

    def test_csv_preserves_identifiers_and_writes_empty_result_headers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transactions.csv"
            _write_finance_csv(output, [[*transaction(), "egras"]])
            with output.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.reader(file))
            self.assertEqual(rows[1][7], "0694874866713")
            self.assertEqual(rows[1][6], "10002162026100610106")
            self.assertEqual(rows[0], [*FINANCE_HEADERS, "eGRAS Username"])
            _write_finance_csv(output, [])
            with output.open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(list(csv.reader(file)), [[*FINANCE_HEADERS, "eGRAS Username"]])
            self.assertFalse(list(output.parent.glob("*.tmp")))

    def test_failed_atomic_replace_preserves_existing_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transactions.csv"
            _write_finance_csv(output, [[*transaction("old"), "egras"]])
            original = output.read_bytes()
            with (
                patch("services.finance_transactions.os.replace", side_effect=PermissionError("locked")),
                self.assertRaises(PermissionError),
            ):
                _write_finance_csv(output, [[*transaction("new"), "egras"]])
            self.assertEqual(output.read_bytes(), original)
            self.assertFalse(list(output.parent.glob("*.tmp")))


class FinancePaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_account_failure_saves_successes_and_reports_failed_account(self) -> None:
        page = mock_page()
        page.goto = AsyncMock()
        page.locator.return_value.select_option = AsyncMock()
        page.locator.return_value.count = AsyncMock(return_value=1)
        session = MagicMock()
        session.new_portal_page = AsyncMock(return_value=page)
        session.close = AsyncMock()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("services.finance_transactions.PortalBrowserSession", return_value=session),
            patch(
                "services.finance_transactions._login",
                new=AsyncMock(side_effect=[None, RuntimeError("login failed")]),
            ),
            patch(
                "services.finance_transactions.collect_finance_pages",
                new=AsyncMock(return_value=[transaction()]),
            ),
        ):
            output = Path(directory) / "transactions.csv"
            summary = await export_finance_transactions_batch(
                [target("first"), target("FIRST"), target("second")],
                output,
                FinanceExportFilters(),
                lambda _message: None,
                RunControls(lambda _event: None),
            )
            self.assertEqual(summary.appended_rows, 1)
            self.assertEqual(len(summary.results), 2)
            self.assertEqual(summary.results[1].error, "login failed")
            with output.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.reader(file))
            self.assertEqual(rows[1][-1], "first")
            self.assertEqual(session.close.await_count, 2)

    async def test_no_successful_accounts_does_not_replace_destination(self) -> None:
        session = MagicMock()
        session.new_portal_page = AsyncMock(side_effect=RuntimeError("unreachable"))
        session.close = AsyncMock()
        with (
            patch("services.finance_transactions.PortalBrowserSession", return_value=session),
            patch("services.finance_transactions._write_finance_csv") as write_csv,
            self.assertRaisesRegex(RuntimeError, "No accounts could be exported"),
        ):
            await export_finance_transactions_batch(
                [target()],
                Path("existing.csv"),
                FinanceExportFilters(),
                lambda _message: None,
                RunControls(lambda _event: None),
            )
        write_csv.assert_not_called()
        session.close.assert_awaited_once()

    async def test_collects_past_ellipsis_pager_blocks_and_deduplicates_grns(self) -> None:
        page = mock_page()
        page.evaluate.side_effect = [
            {
                "headers": FINANCE_HEADERS,
                "rows": [transaction(str(number)), transaction("shared")],
                "number": number,
                "links": [
                    {"number": number + 1, "href": f"javascript:__doPostBack('grid','Page${number + 1}')"}
                ]
                if number < 12
                else [],
            }
            for number in range(1, 13)
        ]
        rows = await collect_finance_pages(
            cast(Page, page), FinanceExportFilters(), RunControls(lambda _event: None), lambda _message: None
        )
        self.assertEqual(len(rows), 13)
        self.assertEqual(page.locator.return_value.click.await_count, 11)
        selectors = [call.args[0] for call in page.locator.call_args_list]
        self.assertTrue(any("Page$11" in selector for selector in selectors))

    async def test_date_cutoff_does_not_request_next_page(self) -> None:
        page = mock_page()
        page.evaluate.return_value = {
            "headers": FINANCE_HEADERS,
            "rows": [transaction(), transaction("old", "04-OCT-2026 00:00:00")],
            "number": 1,
            "links": [{"number": 2, "href": "javascript:__doPostBack('grid','Page$2')"}],
        }
        rows = await collect_finance_pages(
            cast(Page, page),
            FinanceExportFilters(date_from=date(2026, 10, 5)),
            RunControls(lambda _event: None),
            lambda _message: None,
        )
        self.assertEqual(len(rows), 1)
        page.locator.return_value.click.assert_not_awaited()

    async def test_pagination_that_does_not_advance_fails(self) -> None:
        page = mock_page()
        page.evaluate.return_value = {
            "headers": FINANCE_HEADERS,
            "rows": [transaction()],
            "number": 1,
            "links": [{"number": 2, "href": "javascript:__doPostBack('grid','Page$2')"}],
        }
        with self.assertRaisesRegex(RuntimeError, "Pagination did not advance"):
            await collect_finance_pages(
                cast(Page, page),
                FinanceExportFilters(),
                RunControls(lambda _event: None),
                lambda _message: None,
            )

    async def test_cancellation_preserves_destination(self) -> None:
        controls = RunControls(lambda _event: None)
        controls.stop()
        with (
            patch("services.finance_transactions._write_finance_csv") as write_csv,
            self.assertRaises(WorkflowStopped),
        ):
            await export_finance_transactions_batch(
                [target()],
                Path("transactions.csv"),
                FinanceExportFilters(),
                lambda _message: None,
                controls,
            )
        write_csv.assert_not_called()


class FinanceLoginTests(unittest.IsolatedAsyncioTestCase):
    async def _run_login(self, manual_otp: str = "", sms_error: bool = False) -> tuple[MagicMock, AsyncMock]:
        page = mock_page()
        otp_field = MagicMock()
        otp_field.is_visible = AsyncMock(return_value=True)
        otp_field.input_value = AsyncMock(return_value=manual_otp)
        otp_field.fill = AsyncMock()
        message = MagicMock()
        message.text_content = AsyncMock(return_value="OTP sent with reference no. 1542714")
        message.is_visible = AsyncMock(return_value=False)
        default = page.locator.return_value

        def locate(selector: str) -> MagicMock:
            if selector.endswith("txtOTP"):
                return otp_field
            if selector.endswith("lblMsgs"):
                return message
            return cast(MagicMock, default)

        page.locator.side_effect = locate
        client = MagicMock()
        client.is_configured = True
        client.get_egrass_otp = AsyncMock(return_value="a09afd")
        client.delete_egrass_otp_after_use = AsyncMock()
        if sms_error:
            client.get_egrass_otp.side_effect = SmsOtpServerError("unavailable")
        with (
            patch(
                "services.finance_transactions._is_authenticated",
                new=AsyncMock(side_effect=[False, False, True]),
            ),
            patch("services.finance_transactions.SmsOtpClient", return_value=client),
            patch("services.finance_transactions.asyncio.sleep", new=AsyncMock()),
        ):
            await _login(cast(Page, page), target(), RunControls(lambda _event: None), lambda _message: None)
        return otp_field, client

    async def test_auto_otp_is_six_characters_and_uses_registered_login_reference(self) -> None:
        field, client = await self._run_login()
        client.get_egrass_otp.assert_awaited_once_with("1542714")
        field.fill.assert_awaited_once_with("A09AFD")
        client.delete_egrass_otp_after_use.assert_awaited_once_with("1542714", "A09AFD")

    async def test_manual_otp_takes_priority(self) -> None:
        field, client = await self._run_login(manual_otp="123456")
        field.fill.assert_not_awaited()
        client.get_egrass_otp.assert_not_awaited()
        client.delete_egrass_otp_after_use.assert_not_awaited()

    async def test_sms_failure_allows_manual_login_without_deleting_otp(self) -> None:
        field, client = await self._run_login(sms_error=True)
        field.fill.assert_not_awaited()
        client.delete_egrass_otp_after_use.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
