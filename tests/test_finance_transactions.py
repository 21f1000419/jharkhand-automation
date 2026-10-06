from __future__ import annotations

import asyncio
import csv
import queue
import sqlite3
import tempfile
import unittest
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from core.activity_log import DailyActivityLog
from core.controls import RunControls
from core.models import BrowserEngine, Credentials, PortalBrowser, WorkflowStopped
from services.finance_checkpoint import FINANCE_HEADERS, FinanceCheckpoint, FinancePageUpdate
from services.finance_transactions import (
    FinanceExportFilters,
    FinanceExportTarget,
    _advance_finance_page,
    _complete_finance_captcha_login,
    _login,
    _wait_finance_login_progress,
    _write_finance_csv,
    collect_finance_pages,
    export_finance_checkpoint,
    extract_finance_otp_reference,
    fetch_finance_transactions_batch,
    filter_finance_rows,
    unique_egras_targets,
)
from services.sms_otp_client import SmsOtpClient, SmsOtpServerError
from ui.finance_transactions_dialog import FinanceTransactionsDialog


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
    page.wait_for_function = AsyncMock()
    locator = page.locator.return_value
    locator.click = AsyncMock()
    locator.wait_for = AsyncMock()
    locator.fill = AsyncMock()
    locator.is_visible = AsyncMock(return_value=False)
    locator.input_value = AsyncMock(return_value="")
    locator.count = AsyncMock(return_value=1)
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


class FinanceCheckpointTests(unittest.TestCase):
    def test_saved_pages_survive_reopening_and_deduplicate_across_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.sqlite3"
            checkpoint = FinanceCheckpoint.create(path, ["first", "second"], "amount=10")
            self.assertFalse(checkpoint.info().can_download)
            update = checkpoint.save_page("first", 1, [transaction("shared"), transaction("first")])
            self.assertEqual(update.total_rows, 2)
            update = checkpoint.save_page("second", 1, [transaction("shared"), transaction("second")])
            self.assertEqual([row[1] for row in update.rows], ["second"])
            checkpoint.save_page("first", 1, [transaction("should-not-repeat")])
            checkpoint.set_account_status("first", "failed", "browser closed")
            checkpoint.set_account_status("second", "completed")
            recovered = FinanceCheckpoint(path)
            info = recovered.info()
            self.assertEqual(info.row_count, 3)
            self.assertTrue(info.can_download)
            self.assertEqual(info.filters, "amount=10")
            self.assertEqual(info.accounts[0].pages, 1)
            self.assertEqual(info.accounts[1].matching_rows, 2)
            self.assertEqual(info.accounts[1].added_rows, 1)
            rows = recovered.read_rows()
            self.assertEqual([row[1] for row in rows], ["shared", "first", "second"])
            self.assertEqual(rows[0][7], "0694874866713")
            self.assertEqual(rows[0][-1], "first")
            output = Path(directory) / "download.csv"
            self.assertEqual(export_finance_checkpoint(recovered, output), 3)
            with output.open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(list(csv.reader(file))[1:], rows)

    def test_invalid_page_rolls_back_both_rows_and_page_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["account"], "")
            with self.assertRaises(ValueError):
                checkpoint.save_page("account", 1, [transaction("good"), ["invalid"]])
            self.assertEqual(checkpoint.info().row_count, 0)
            self.assertEqual(checkpoint.info().accounts[0].pages, 0)
            checkpoint.save_page("account", 1, [transaction("good")])
            self.assertEqual(checkpoint.info().row_count, 1)

    def test_completed_empty_fetch_can_download_headers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["account"], "")
            checkpoint.set_account_status("account", "completed")
            self.assertTrue(checkpoint.info().can_download)
            output = Path(directory) / "empty.csv"
            self.assertEqual(export_finance_checkpoint(checkpoint, output), 0)
            with output.open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(list(csv.reader(file)), [[*FINANCE_HEADERS, "eGRAS Username"]])

    def test_existing_checkpoint_is_not_overwritten_and_invalid_paths_are_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.sqlite3"
            checkpoint = FinanceCheckpoint.create(path, ["account"], "")
            checkpoint.save_page("account", 1, [transaction()])
            with self.assertRaises(FileExistsError):
                FinanceCheckpoint.create(path, ["other"], "")
            self.assertEqual(checkpoint.info().row_count, 1)
            missing = Path(directory) / "missing.sqlite3"
            with self.assertRaises(sqlite3.Error):
                FinanceCheckpoint(missing).info()
            self.assertFalse(missing.exists())

    def test_failed_download_keeps_existing_csv_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["account"], "")
            checkpoint.save_page("account", 1, [transaction()])
            output = Path(directory) / "existing.csv"
            _write_finance_csv(output, [[*transaction("old"), "account"]])
            original = output.read_bytes()
            with (
                patch("services.finance_transactions.os.replace", side_effect=PermissionError("locked")),
                self.assertRaises(PermissionError),
            ):
                export_finance_checkpoint(checkpoint, output)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(checkpoint.info().row_count, 1)


class FinanceParallelFetchTests(unittest.IsolatedAsyncioTestCase):
    def prepared_page(self, grn: str) -> MagicMock:
        page = mock_page()
        page.goto = AsyncMock()
        page.locator.return_value.select_option = AsyncMock()
        page.evaluate.return_value = {
            "headers": FINANCE_HEADERS,
            "rows": [transaction(grn)],
            "number": 1,
            "links": [],
        }
        return page

    def session_for(self, page: MagicMock) -> MagicMock:
        session = MagicMock()
        session.new_portal_page = AsyncMock(return_value=page)
        session.close = AsyncMock()
        return session

    async def test_accounts_start_together_and_stream_committed_pages_without_creating_csv(self) -> None:
        pages = [self.prepared_page("first"), self.prepared_page("second")]
        sessions = [self.session_for(page) for page in pages]
        started: list[str] = []
        both_started = asyncio.Event()
        updates: list[FinancePageUpdate] = []

        async def login(_page: Page, account: FinanceExportTarget, *_args: object) -> None:
            started.append(account.name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=2)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("services.finance_transactions.PortalBrowserSession", side_effect=sessions) as factory,
            patch("services.finance_transactions._login", new=AsyncMock(side_effect=login)),
        ):
            checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["first", "second"], "")

            def stream(update: FinancePageUpdate) -> None:
                recovered = FinanceCheckpoint(checkpoint.path)
                self.assertEqual(recovered.info().row_count, update.total_rows)
                self.assertTrue(all(row in recovered.read_rows() for row in update.rows))
                updates.append(update)

            info = await fetch_finance_transactions_batch(
                [target("first"), target("FIRST"), target("second")],
                checkpoint,
                FinanceExportFilters(),
                lambda _: None,
                RunControls(lambda _: None),
                stream,
            )
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(info.row_count, 2)
            self.assertEqual(len(updates), 2)
            self.assertTrue(all(account.status == "completed" for account in info.accounts))
            self.assertFalse(list(Path(directory).glob("*.csv")))
        for session in sessions:
            session.close.assert_awaited_once()

    async def test_browser_closed_after_page_one_keeps_rows_and_other_account_finishes(self) -> None:
        first = self.prepared_page("first")
        first.evaluate.return_value["links"] = [
            {"number": 2, "href": "javascript:__doPostBack('grid','Page$2')"}
        ]
        second = self.prepared_page("second")
        sessions = [self.session_for(first), self.session_for(second)]
        streamed: list[FinancePageUpdate] = []
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("services.finance_transactions.PortalBrowserSession", side_effect=sessions),
            patch("services.finance_transactions._login", new=AsyncMock()),
            patch(
                "services.finance_transactions._advance_finance_page",
                new=AsyncMock(side_effect=RuntimeError('browser closed; fill("A09AFD") with password')),
            ),
        ):
            checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["first", "second"], "")
            info = await fetch_finance_transactions_batch(
                [target("first"), target("second")],
                checkpoint,
                FinanceExportFilters(),
                lambda _: None,
                RunControls(lambda _: None),
                streamed.append,
            )
            self.assertEqual(info.row_count, 2)
            self.assertEqual(info.accounts[0].status, "failed")
            self.assertEqual(info.accounts[1].status, "completed")
            self.assertEqual(info.accounts[0].pages, 1)
            self.assertNotIn("A09AFD", info.accounts[0].error)
            self.assertNotIn("password", info.accounts[0].error)
            self.assertTrue(info.can_download)
            self.assertEqual(len(streamed), 2)
            self.assertEqual(export_finance_checkpoint(checkpoint, Path(directory) / "partial.csv"), 2)

    async def test_stop_cancels_blocked_browsers_and_keeps_the_last_committed_page(self) -> None:
        pages = [self.prepared_page("first"), self.prepared_page("second")]
        sessions = [self.session_for(page) for page in pages]
        waiting = asyncio.Event()
        controls = RunControls(lambda _: None)

        async def collect(
            page: Page, *_args: object, on_page: Callable[[int, list[list[str]]], None]
        ) -> list[list[str]]:
            if page is pages[0]:
                on_page(1, [transaction("first")])
            else:
                waiting.set()
            await asyncio.Event().wait()
            return []

        async def stop_after_both_started() -> None:
            await waiting.wait()
            controls.stop()

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("services.finance_transactions.PortalBrowserSession", side_effect=sessions),
            patch("services.finance_transactions._login", new=AsyncMock()),
            patch("services.finance_transactions.collect_finance_pages", new=AsyncMock(side_effect=collect)),
        ):
            checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["first", "second"], "")
            stop = asyncio.create_task(stop_after_both_started())
            with self.assertRaises(WorkflowStopped):
                await asyncio.wait_for(
                    fetch_finance_transactions_batch(
                        [target("first"), target("second")],
                        checkpoint,
                        FinanceExportFilters(),
                        lambda _: None,
                        controls,
                        lambda _: None,
                    ),
                    timeout=2,
                )
            await stop
            info = checkpoint.info()
            self.assertEqual(info.row_count, 1)
            self.assertTrue(all(account.status == "stopped" for account in info.accounts))
            self.assertTrue(info.can_download)
        for session in sessions:
            session.close.assert_awaited_once()


class FinancePaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_account_failure_saves_successes_and_reports_failed_account(self) -> None:
        page = mock_page()
        page.goto = AsyncMock()
        page.locator.return_value.select_option = AsyncMock()
        page.locator.return_value.count = AsyncMock(return_value=1)
        session = MagicMock()
        session.new_portal_page = AsyncMock(return_value=page)
        session.close = AsyncMock()

        async def collect_page(
            *_args: object, on_page: Callable[[int, list[list[str]]], None]
        ) -> list[list[str]]:
            rows = [transaction()]
            on_page(1, rows)
            return rows

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("services.finance_transactions.PortalBrowserSession", return_value=session),
            patch(
                "services.finance_transactions._login",
                new=AsyncMock(side_effect=[None, RuntimeError("login failed")]),
            ),
            patch(
                "services.finance_transactions.collect_finance_pages",
                new=AsyncMock(side_effect=collect_page),
            ),
        ):
            output = Path(directory) / "transactions.csv"
            checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["first", "second"], "")
            summary = await fetch_finance_transactions_batch(
                [target("first"), target("FIRST"), target("second")],
                checkpoint,
                FinanceExportFilters(),
                lambda _message: None,
                RunControls(lambda _event: None),
                lambda _update: None,
            )
            self.assertFalse(output.exists())
            self.assertEqual(summary.row_count, 1)
            self.assertEqual(len(summary.accounts), 2)
            self.assertEqual(summary.accounts[1].error, "login failed")
            export_finance_checkpoint(checkpoint, output)
            with output.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.reader(file))
            self.assertEqual(rows[1][-1], "first")
            self.assertEqual(session.close.await_count, 2)
            page.expect_navigation.assert_not_called()
            page.locator.return_value.wait_for.assert_awaited_once_with(state="visible", timeout=60_000)

    async def test_search_waits_for_the_table_before_collecting_rows(self) -> None:
        page = mock_page()
        page.goto = AsyncMock()
        page.locator.return_value.select_option = AsyncMock()
        session = MagicMock()
        session.new_portal_page = AsyncMock(return_value=page)
        session.close = AsyncMock()

        async def collect_after_table(
            *_args: object, on_page: Callable[[int, list[list[str]]], None]
        ) -> list[list[str]]:
            page.locator.return_value.wait_for.assert_awaited_once_with(state="visible", timeout=60_000)
            on_page(1, [transaction()])
            return [transaction()]

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("services.finance_transactions.PortalBrowserSession", return_value=session),
            patch("services.finance_transactions._login", new=AsyncMock()),
            patch(
                "services.finance_transactions.collect_finance_pages",
                new=AsyncMock(side_effect=collect_after_table),
            ),
        ):
            summary = await fetch_finance_transactions_batch(
                [target()],
                FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["egras"], ""),
                FinanceExportFilters(),
                lambda _message: None,
                RunControls(lambda _event: None),
                lambda _update: None,
            )
        self.assertEqual(summary.row_count, 1)
        page.expect_navigation.assert_not_called()

    async def test_no_successful_accounts_does_not_replace_destination(self) -> None:
        session = MagicMock()
        session.new_portal_page = AsyncMock(side_effect=RuntimeError("unreachable"))
        session.close = AsyncMock()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("services.finance_transactions.PortalBrowserSession", return_value=session),
            patch("services.finance_transactions._write_finance_csv") as write_csv,
        ):
            checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["egras"], "")
            summary = await fetch_finance_transactions_batch(
                [target()],
                checkpoint,
                FinanceExportFilters(),
                lambda _message: None,
                RunControls(lambda _event: None),
                lambda _update: None,
            )
            self.assertFalse(summary.can_download)
            self.assertEqual(summary.accounts[0].status, "failed")
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
        self.assertTrue(any("td:has(> a" in selector and "Page$11" in selector for selector in selectors))
        self.assertEqual(
            [call.kwargs["arg"] for call in page.wait_for_function.call_args_list], list(range(2, 13))
        )
        page.expect_navigation.assert_not_called()

    async def test_clicks_next_page_cell_and_waits_for_in_place_update(self) -> None:
        page = mock_page()
        page.wait_for_function.side_effect = [PlaywrightTimeoutError("updating"), None]
        controls = RunControls(lambda _event: None)
        await _advance_finance_page(
            cast(Page, page), {"number": 2, "href": "javascript:__doPostBack('grid','Page$2')"}, controls
        )
        page.locator.return_value.click.assert_awaited_once_with(timeout=30_000)
        self.assertIn("td:has(> a", page.locator.call_args.args[0])
        self.assertEqual(page.wait_for_function.await_count, 2)
        self.assertEqual(page.wait_for_function.call_args.kwargs["arg"], 2)
        page.expect_navigation.assert_not_called()

    async def test_page_two_moves_to_three_without_revisiting_page_one(self) -> None:
        page = mock_page()
        second = transaction("2605111136", "06-OCT-2026 02:21:22")
        second[0] = "16"
        page.evaluate.side_effect = [
            {
                "headers": FINANCE_HEADERS,
                "rows": [second],
                "number": 2,
                "links": [
                    {"number": 1, "href": "javascript:__doPostBack('grid','Page$1')"},
                    {"number": 3, "href": "javascript:__doPostBack('grid','Page$3')"},
                    {"number": 11, "href": "javascript:__doPostBack('grid','Page$11')"},
                ],
            },
            {"headers": FINANCE_HEADERS, "rows": [transaction("third")], "number": 3, "links": []},
        ]
        rows = await collect_finance_pages(
            cast(Page, page), FinanceExportFilters(), RunControls(lambda _event: None), lambda _message: None
        )
        self.assertEqual([row[1] for row in rows], ["2605111136", "third"])
        self.assertIn("Page$3", page.locator.call_args.args[0])
        page.wait_for_function.assert_awaited_once()
        self.assertEqual(page.wait_for_function.call_args.kwargs["arg"], 3)

    async def test_stop_interrupts_wait_for_page_update(self) -> None:
        page = mock_page()
        controls = RunControls(lambda _event: None)

        async def stop_during_wait(*_args: object, **_kwargs: object) -> None:
            controls.stop()
            raise PlaywrightTimeoutError("updating")

        page.wait_for_function.side_effect = stop_during_wait
        with self.assertRaises(WorkflowStopped):
            await _advance_finance_page(
                cast(Page, page), {"number": 2, "href": "javascript:__doPostBack('grid','Page$2')"}, controls
            )

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
            tempfile.TemporaryDirectory() as directory,
            patch("services.finance_transactions._write_finance_csv") as write_csv,
            self.assertRaises(WorkflowStopped),
        ):
            await fetch_finance_transactions_batch(
                [target()],
                FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["egras"], ""),
                FinanceExportFilters(),
                lambda _message: None,
                controls,
                lambda _update: None,
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
            patch("services.finance_transactions._complete_finance_captcha_login", new=AsyncMock()),
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


class FinanceCaptchaRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejections_and_password_resets_retry_without_a_fixed_limit(self) -> None:
        page = mock_page()
        portal = MagicMock()
        portal._solve_captcha = AsyncMock(return_value=False)
        portal._refresh_captcha = AsyncMock()
        outcomes = ["captcha_failed", "form_reset"] * 4 + ["otp"]
        messages: list[str] = []
        with (
            patch("services.finance_transactions.PortalAutomation", return_value=portal),
            patch("services.finance_transactions._is_authenticated", new=AsyncMock(return_value=False)),
            patch(
                "services.finance_transactions._wait_finance_login_progress",
                new=AsyncMock(side_effect=outcomes),
            ),
        ):
            await _complete_finance_captcha_login(
                cast(Page, page),
                target(),
                SmsOtpClient("https://sms.example"),
                RunControls(lambda _event: None),
                messages.append,
            )
        self.assertEqual(portal._solve_captcha.await_count, 9)
        self.assertEqual(portal._refresh_captcha.await_count, 8)
        portal._solve_captcha.assert_awaited_with(
            "img.imgcaptcha",
            "#ContentPlaceHolder1_txtcaptcha",
            expected_length=6,
            refresh_selector="#ContentPlaceHolder1_ImageButton1",
        )
        values = [call.args[0] for call in page.locator.return_value.fill.call_args_list]
        self.assertEqual(values.count("egras"), 9)
        self.assertEqual(values.count("password"), 9)
        self.assertTrue(any("attempt 9" in message for message in messages))

    async def test_non_captcha_login_error_is_left_for_manual_correction(self) -> None:
        page = mock_page()
        portal = MagicMock()
        portal._solve_captcha = AsyncMock(return_value=False)
        portal._refresh_captcha = AsyncMock()
        with (
            patch("services.finance_transactions.PortalAutomation", return_value=portal),
            patch("services.finance_transactions._is_authenticated", new=AsyncMock(return_value=False)),
            patch(
                "services.finance_transactions._wait_finance_login_progress",
                new=AsyncMock(return_value="login_error"),
            ),
        ):
            await _complete_finance_captcha_login(
                cast(Page, page),
                target(),
                SmsOtpClient("https://sms.example"),
                RunControls(lambda _event: None),
                lambda _message: None,
            )
        portal._solve_captcha.assert_awaited_once()
        portal._refresh_captcha.assert_not_awaited()

    async def test_manual_captcha_does_not_submit_login_automatically(self) -> None:
        page = mock_page()
        portal = MagicMock()
        portal._solve_captcha = AsyncMock(return_value=True)
        with (
            patch("services.finance_transactions.PortalAutomation", return_value=portal),
            patch("services.finance_transactions._is_authenticated", new=AsyncMock(return_value=False)),
            patch(
                "services.finance_transactions._wait_finance_login_progress",
                new=AsyncMock(return_value="otp"),
            ),
        ):
            await _complete_finance_captcha_login(
                cast(Page, page),
                target(),
                SmsOtpClient("https://sms.example"),
                RunControls(lambda _event: None),
                lambda _message: None,
            )
        page.locator.return_value.click.assert_not_awaited()

    async def test_rejected_registered_login_captcha_is_detected(self) -> None:
        page = mock_page()
        label = MagicMock()
        label.is_visible = AsyncMock(return_value=True)
        label.text_content = AsyncMock(return_value="Invalid Captcha !!!")
        default = page.locator.return_value
        page.locator.side_effect = lambda selector: label if selector.endswith("lblMsg") else default
        with patch("services.finance_transactions._is_authenticated", new=AsyncMock(return_value=False)):
            self.assertEqual(
                await _wait_finance_login_progress(cast(Page, page), RunControls(lambda _event: None)),
                "captcha_failed",
            )

    async def test_stop_interrupts_captcha_login(self) -> None:
        page = mock_page()
        controls = RunControls(lambda _event: None)
        controls.stop()
        with self.assertRaises(WorkflowStopped):
            await _complete_finance_captcha_login(
                cast(Page, page),
                target(),
                SmsOtpClient("https://sms.example"),
                controls,
                lambda _message: None,
            )
        page.locator.return_value.click.assert_not_awaited()


class FinanceDialogTests(unittest.TestCase):
    def make_dialog(self) -> FinanceTransactionsDialog:
        dialog = FinanceTransactionsDialog.__new__(FinanceTransactionsDialog)
        dialog.owner = MagicMock()
        dialog.owner.transaction_export_running = False
        dialog.dialog = MagicMock()
        dialog.is_running = False
        dialog.owns_export_slot = False
        dialog.close_after_stop = False
        dialog.controls = None
        dialog.checkpoint = None
        dialog.can_download = False
        dialog.events = queue.Queue()
        dialog.export_id = "test"
        dialog.log_secrets = ()
        dialog.editable = []
        dialog.account_checks = []
        for name in (
            "results",
            "results_var",
            "checkpoint_var",
            "status_var",
            "log",
            "start_button",
            "download_button",
            "stop_button",
            "start_date",
            "end_date",
            "start_enabled",
            "end_enabled",
        ):
            setattr(dialog, name, MagicMock())
        return dialog

    def test_download_does_not_prompt_before_results_are_available(self) -> None:
        dialog = self.make_dialog()
        with patch("ui.finance_transactions_dialog.filedialog.askdirectory") as choose_folder:
            dialog._download()
        choose_folder.assert_not_called()

    def test_download_prompts_for_folder_and_saves_without_opening_a_browser(self) -> None:
        dialog = self.make_dialog()
        with tempfile.TemporaryDirectory() as directory:
            dialog.checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["account"], "")
            dialog.checkpoint.save_page("account", 1, [transaction()])
            dialog.can_download = True

            def run_inline(*_args: object, **kwargs: object) -> MagicMock:
                worker = cast(Callable[[], None], kwargs["target"])
                thread = MagicMock()
                thread.start.side_effect = worker
                return thread

            with (
                patch(
                    "ui.finance_transactions_dialog.filedialog.askdirectory", return_value=directory
                ) as folder,
                patch("ui.finance_transactions_dialog.threading.Thread", side_effect=run_inline),
                patch("services.finance_transactions.PortalBrowserSession") as browser,
            ):
                dialog._download()
            folder.assert_called_once()
            self.assertTrue(folder.call_args.kwargs["mustexist"])
            browser.assert_not_called()
            files = list(Path(directory).glob("finance_success_*.csv"))
            self.assertEqual(len(files), 1)
            self.assertEqual(dialog.checkpoint.info().row_count, 1)
            with files[0].open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(list(csv.reader(file))[1][1], "2605107970")

    def test_cancel_folder_selection_keeps_results_available(self) -> None:
        dialog = self.make_dialog()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("ui.finance_transactions_dialog.filedialog.askdirectory", return_value=""),
            patch("ui.finance_transactions_dialog.threading.Thread") as thread,
        ):
            dialog.checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["account"], "")
            dialog.checkpoint.save_page("account", 1, [transaction()])
            dialog.can_download = True
            dialog._download()
            thread.assert_not_called()
            self.assertTrue(dialog.can_download)
            self.assertFalse(dialog.is_running)

    def test_streamed_page_appears_and_partial_results_enable_download_after_failure(self) -> None:
        dialog = self.make_dialog()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("ui.finance_transactions_dialog.messagebox.showerror"),
        ):
            dialog.checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["account"], "")
            update = dialog.checkpoint.save_page("account", 1, [transaction()])
            dialog.checkpoint.set_account_status("account", "failed", "browser closed")
            dialog._set_running(True)
            dialog.events.put(("page", update))
            dialog.events.put(("error", "Browser closed. Saved pages were kept."))
            dialog._poll_events()
            cast(MagicMock, dialog.results.insert).assert_called_once_with("", "end", values=update.rows[0])
            cast(MagicMock, dialog.download_button.configure).assert_called_with(state="normal")
            self.assertTrue(dialog.can_download)
            self.assertFalse(dialog.is_running)
            self.assertFalse(dialog.owner.transaction_export_running)

    def test_reopening_a_checkpoint_restores_rows_without_changing_another_exports_slot(self) -> None:
        dialog = self.make_dialog()
        dialog.owner.transaction_export_running = True
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = FinanceCheckpoint.create(Path(directory) / "saved.sqlite3", ["account"], "amount=10")
            checkpoint.save_page("account", 1, [transaction()])
            with (
                patch(
                    "ui.finance_transactions_dialog.filedialog.askopenfilename",
                    return_value=str(checkpoint.path),
                ),
                patch("services.finance_transactions.PortalBrowserSession") as browser,
            ):
                dialog._open_checkpoint()
            browser.assert_not_called()
            self.assertTrue(dialog.can_download)
            self.assertEqual(cast(MagicMock, dialog.results.insert).call_args.kwargs["values"][-1], "account")
            self.assertTrue(dialog.owner.transaction_export_running)


class FinanceActivityLogTests(unittest.TestCase):
    def test_progress_and_errors_are_saved_before_the_ui_consumes_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = FinanceTransactionsDialog.__new__(FinanceTransactionsDialog)
            dialog.owner = MagicMock()
            dialog.owner.controller.activity_log = DailyActivityLog(Path(directory))
            dialog.events = queue.Queue()
            dialog.export_id = "export-test"
            dialog.log_secrets = ("saved-password",)
            dialog._publish_event("status", "Moving from page 1 to page 2...")
            dialog._publish_event("error", 'Timeout: fill("A09AFD") failed with saved-password')
            files = list(Path(directory).glob("*.log"))
            self.assertEqual(len(files), 1)
            contents = files[0].read_text(encoding="utf-8")
            self.assertIn("finance_export_status: Moving from page 1 to page 2", contents)
            self.assertIn("[ERROR] finance_export_error", contents)
            self.assertIn('"export_id": "export-test"', contents)
            self.assertNotIn("saved-password", contents)
            self.assertNotIn("A09AFD", contents)
            self.assertEqual(dialog.events.qsize(), 2)

    def test_log_write_failure_still_delivers_the_export_event(self) -> None:
        dialog = FinanceTransactionsDialog.__new__(FinanceTransactionsDialog)
        dialog.owner = MagicMock()
        dialog.owner.controller.activity_log.write.side_effect = OSError("log file locked")
        dialog.events = queue.Queue()
        dialog.export_id = "export-test"
        dialog.log_secrets = ()
        dialog._publish_event("success", "Saved CSV.")
        self.assertIn("Could not save the finance export log", str(dialog.events.get_nowait()[1]))
        self.assertEqual(dialog.events.get_nowait(), ("success", "Saved CSV."))


if __name__ == "__main__":
    unittest.main()
