from __future__ import annotations

import asyncio
import csv
import datetime
import io
import tempfile
import tkinter as tk
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from core.config import AppConfig, TabConfig
from core.controls import RunControls
from core.models import BrowserEngine, Credentials, OcrEngine, PortalBrowser
from services.estamp_transactions import (
    BatchTransactionExportSummary,
    MissingStampDownloadSummary,
    TransactionExportSummary,
    TransactionExportTarget,
    append_unique_transactions,
    download_missing_stamps,
    export_payment_transactions_batch,
    export_payment_transactions_for_target,
)
from services.pdf_receipt_dates import (
    _read_receipt_date,
    extract_first_party_name,
    extract_receipt_amount,
    extract_receipt_date,
    find_pdf_folder_defaults,
)
from services.transaction_reconciliation import (
    MissingPdf,
    TransactionReconciliation,
    reconcile_transactions,
)
from ui.download_certificates_dialog import (
    DownloadUndownloadedCertificatesDialog,
    IdSelectionItem,
)


class DownloadCertificatesTests(unittest.TestCase):
    def test_primary_stop_button_cancels_automatic_pdf_date_scan(self) -> None:
        dialog = DownloadUndownloadedCertificatesDialog.__new__(
            DownloadUndownloadedCertificatesDialog
        )
        dialog.is_running = False
        dialog.export_stop_requested = False
        dialog.current_controls = None
        dialog.is_scanning_pdf_dates = True
        dialog._pdf_scan_token = 7
        dialog.reconcile_status_var = MagicMock()
        dialog.stop_btn = MagicMock()
        dialog._append_log = MagicMock()  # type: ignore[method-assign]

        dialog._stop_export()

        self.assertEqual(dialog._pdf_scan_token, 8)
        self.assertFalse(dialog.is_scanning_pdf_dates)
        dialog.reconcile_status_var.set.assert_called_once_with(
            "Automatic PDF date extraction stopped."
        )
        dialog.stop_btn.configure.assert_called_once_with(state="disabled")
        dialog._append_log.assert_called_once_with(
            "Automatic PDF date extraction stopped by user."
        )

    def test_extract_receipt_date(self) -> None:
        text = "Certificate details\nReceipt Date : 05-Sep-2026 12:15:13 pm\nReference"

        self.assertEqual(extract_receipt_date(text), datetime.date(2026, 9, 5))

    def test_extract_receipt_date_ignores_invalid_text(self) -> None:
        self.assertIsNone(extract_receipt_date("Receipt Date : 42-Sep-2026 12:15:13 pm"))

    def test_extract_first_party_name_and_receipt_amount(self) -> None:
        text = (
            "Receipt Amount : 100/-\n"
            "Amount In Words : One Hundred Rupees Only\n"
            "First Party Name : HDFC Bank Ltd\n"
            "Second Party Name : As per agreement with HDFC Bank Ltd"
        )

        self.assertEqual(extract_first_party_name(text), "HDFC Bank Ltd")
        self.assertEqual(extract_receipt_amount(text), "100")

    def test_pdf_reader_closes_objects_without_context_manager_support(self) -> None:
        text_page = MagicMock()
        text_page.get_text_range.return_value = "Receipt Date : 05-Sep-2026 12:15:13 pm"
        page = MagicMock()
        page.get_textpage.return_value = text_page
        document = MagicMock()
        document.__len__.return_value = 1
        document.__getitem__.return_value = page

        with patch("services.pdf_receipt_dates.pdfium.PdfDocument") as pdf_document:
            pdf_document.return_value.__enter__.return_value = document
            result = _read_receipt_date(Path("sample.pdf"))

        self.assertEqual(result, datetime.date(2026, 9, 5))
        text_page.close.assert_called_once_with()
        page.close.assert_called_once_with()

    def test_pdf_reader_ignores_unexpected_extraction_errors(self) -> None:
        with patch(
            "services.pdf_receipt_dates.pdfium.PdfDocument",
            side_effect=Exception("unsupported PDF"),
        ):
            self.assertIsNone(_read_receipt_date(Path("sample.pdf")))

    def test_pdf_folder_defaults_keep_parallel_results_complete(self) -> None:
        paths = [Path(f"{index:02}.pdf") for index in range(20)]
        scan_results = {
            path: (
                datetime.date(2026, 9, 13 + index % 7),
                f"Party {index}",
                str(20 + index),
                True,
            )
            for index, path in enumerate(paths)
        }
        progress: list[tuple[int, int]] = []

        with (
            patch("services.pdf_receipt_dates._list_pdf_paths", return_value=paths),
            patch(
                "services.pdf_receipt_dates._scan_single_pdf",
                side_effect=lambda path: scan_results[path],
            ),
            patch(
                "services.pdf_receipt_dates.ProcessPoolExecutor",
                side_effect=lambda max_workers: ThreadPoolExecutor(max_workers=max_workers),
            ) as process_pool,
        ):
            defaults = find_pdf_folder_defaults(
                Path("pdfs"),
                max_workers=4,
                progress_callback=lambda done, total: progress.append((done, total)),
            )

        process_pool.assert_called_once_with(max_workers=4)
        self.assertEqual(defaults.date_from, datetime.date(2026, 9, 13))
        self.assertEqual(defaults.date_to, datetime.date(2026, 9, 19))
        self.assertEqual(defaults.first_party_name, "Party 0")
        self.assertEqual(defaults.receipt_amount, "20")
        self.assertEqual(progress[-1], (20, 20))
        self.assertEqual(len(progress), 20)

    def test_pdf_folder_defaults_fall_back_when_processes_cannot_start(self) -> None:
        paths = [Path(f"{index:02}.pdf") for index in range(20)]

        def scan(path: Path) -> tuple[datetime.date, str, str, bool]:
            index = paths.index(path)
            return datetime.date(2026, 9, 13 + index % 7), "Party", "20", True

        with (
            patch("services.pdf_receipt_dates._list_pdf_paths", return_value=paths),
            patch("services.pdf_receipt_dates._scan_single_pdf", side_effect=scan) as scanner,
            patch(
                "services.pdf_receipt_dates.ProcessPoolExecutor",
                side_effect=RuntimeError("process creation blocked"),
            ),
        ):
            defaults = find_pdf_folder_defaults(Path("pdfs"), max_workers=4)

        self.assertEqual(defaults.date_from, datetime.date(2026, 9, 13))
        self.assertEqual(defaults.date_to, datetime.date(2026, 9, 19))
        self.assertEqual(scanner.call_count, 20)

    def test_target_dataclass_defaults(self) -> None:
        browser = PortalBrowser("Managed Chrome", Path("/path/to/chrome"), BrowserEngine.CHROMIUM)
        target = TransactionExportTarget(
            name="ID 1",
            browser=browser,
            profile_path=Path("/tmp/profile"),
        )
        self.assertEqual(target.name, "ID 1")
        self.assertEqual(target.credentials.citizen_username, "")
        self.assertTrue(target.auto_login)
        self.assertFalse(target.skip_status_updates)

    def test_target_can_skip_status_updates(self) -> None:
        browser = PortalBrowser("Managed Chrome", Path("/path/to/chrome"), BrowserEngine.CHROMIUM)
        target = TransactionExportTarget(
            name="ID 1",
            browser=browser,
            profile_path=Path("/tmp/profile"),
            skip_status_updates=True,
        )

        self.assertTrue(target.skip_status_updates)

    def test_target_prewarms_ocr_before_opening_browser(self) -> None:
        browser = PortalBrowser("Managed Chrome", Path("/path/to/chrome"), BrowserEngine.CHROMIUM)
        solver = MagicMock()
        solver.verify_ready = AsyncMock(return_value=True)
        target = TransactionExportTarget(
            name="ID 1",
            browser=browser,
            profile_path=Path("/tmp/profile"),
            solver=solver,
        )
        statuses: list[str] = []

        def create_session(*_args: object, **_kwargs: object) -> object:
            solver.verify_ready.assert_awaited_once_with()
            raise RuntimeError("browser construction reached")

        with (
            patch(
                "services.estamp_transactions.PortalBrowserSession",
                side_effect=create_session,
            ),
            self.assertRaisesRegex(RuntimeError, "browser construction reached"),
        ):
            asyncio.run(
                export_payment_transactions_for_target(
                    target,
                    Path("transactions.csv"),
                    statuses.append,
                )
            )

        self.assertEqual(statuses, ["ID 1: Preparing CAPTCHA OCR..."])

    def test_target_skips_status_updates_when_requested(self) -> None:
        browser = PortalBrowser("Managed Chrome", Path("/path/to/chrome"), BrowserEngine.CHROMIUM)
        target = TransactionExportTarget(
            name="ID 1",
            browser=browser,
            profile_path=Path("/tmp/profile"),
            auto_login=False,
            skip_status_updates=True,
        )
        table = MagicMock()
        table.wait_for = AsyncMock()
        page = MagicMock()
        page.goto = AsyncMock()
        page.locator.return_value = table
        session = MagicMock()
        session.new_portal_page = AsyncMock(return_value=page)
        session.close = AsyncMock()
        headers = ["Transaction ID", "Status", "eStamp Download URL", "User ID"]
        rows = [["tx-1", "SUCCESS", "https://example.test/tx-1", "ID 1"]]

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "transactions.csv"
            with (
                patch("services.estamp_transactions.PortalBrowserSession", return_value=session),
                patch("services.estamp_transactions._wait_for_manual_login", new=AsyncMock()),
                patch(
                    "services.estamp_transactions._resolve_pending_payment_statuses",
                    new=AsyncMock(),
                ) as resolve_statuses,
                patch(
                    "services.estamp_transactions._collect_table_pages",
                    new=AsyncMock(return_value=(headers, rows)),
                ) as collect_pages,
            ):
                summary = asyncio.run(
                    export_payment_transactions_for_target(target, output_path, lambda _message: None)
                )

            resolve_statuses.assert_not_awaited()
            collect_pages.assert_awaited_once()
            self.assertEqual(summary.scraped_rows, 1)
            self.assertEqual(summary.appended_rows, 1)

    def test_export_payment_transactions_batch_aggregates_results(self) -> None:
        browser = PortalBrowser("Managed Chrome", Path("/path/to/chrome"), BrowserEngine.CHROMIUM)
        target1 = TransactionExportTarget("ID 1", browser, Path("/tmp/p1"))
        target2 = TransactionExportTarget("ID 2", browser, Path("/tmp/p2"))

        statuses: list[str] = []

        async def mock_export_target(
            target: TransactionExportTarget,
            output_path: Path,
            report_status: Any,
            controls: RunControls | None = None,
            append_lock: asyncio.Lock | None = None,
        ) -> TransactionExportSummary:
            if target.name == "ID 1":
                return TransactionExportSummary(
                    scraped_rows=10,
                    appended_rows=8,
                    skipped_duplicates=2,
                    output_path=output_path,
                    target_name=target.name,
                )
            else:
                return TransactionExportSummary(
                    scraped_rows=5,
                    appended_rows=3,
                    skipped_duplicates=2,
                    output_path=output_path,
                    target_name=target.name,
                )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "tx.csv"
            with patch(
                "services.estamp_transactions.export_payment_transactions_for_target",
                side_effect=mock_export_target,
            ):
                summary = asyncio.run(
                    export_payment_transactions_batch(
                        [target1, target2],
                        out_path,
                        lambda msg: statuses.append(msg),
                    )
                )

            self.assertEqual(summary.scraped_rows, 15)
            self.assertEqual(summary.appended_rows, 11)
            self.assertEqual(summary.skipped_duplicates, 4)
            self.assertEqual(len(summary.results), 2)
            self.assertTrue(any("ID 1" in s for s in statuses))
            self.assertTrue(any("ID 2" in s for s in statuses))

    def test_export_payment_transactions_batch_handles_individual_error(self) -> None:
        browser = PortalBrowser("Managed Chrome", Path("/path/to/chrome"), BrowserEngine.CHROMIUM)
        target1 = TransactionExportTarget("ID 1", browser, Path("/tmp/p1"))
        target2 = TransactionExportTarget("ID 2", browser, Path("/tmp/p2"))

        async def mock_export_target(
            target: TransactionExportTarget,
            output_path: Path,
            report_status: Any,
            controls: RunControls | None = None,
            append_lock: asyncio.Lock | None = None,
        ) -> TransactionExportSummary:
            if target.name == "ID 1":
                raise RuntimeError("Login failed")
            return TransactionExportSummary(
                scraped_rows=5,
                appended_rows=5,
                skipped_duplicates=0,
                output_path=output_path,
                target_name=target.name,
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "tx.csv"
            with patch(
                "services.estamp_transactions.export_payment_transactions_for_target",
                side_effect=mock_export_target,
            ):
                summary = asyncio.run(
                    export_payment_transactions_batch(
                        [target1, target2],
                        out_path,
                        lambda _msg: None,
                    )
                )

            self.assertEqual(summary.scraped_rows, 5)
            self.assertEqual(summary.appended_rows, 5)
            self.assertEqual(len(summary.results), 2)
            self.assertEqual(summary.results[0].error, "Login failed")
            self.assertEqual(summary.results[1].error, "")

    def test_append_unique_transactions_with_url_and_user_id(self) -> None:
        headers = [
            "Name", "Payment Date", "Amount", "Transaction ID", "GRN", "CIN", "Status", "Actions",
            "eStamp Download URL", "User ID",
        ]
        row1 = [
            "User 1", "2026-08-26", "20", "tx_001", "grn_1", "cin_1", "SUCCESS", "",
            "https://jharnibandhan.gov.in/JHWebService/gras_estamp_download/tx_001", "citizen_alpha",
        ]
        row2 = [
            "User 2", "2026-08-26", "20", "tx_002", "grn_2", "cin_2", "SUCCESS", "",
            "https://jharnibandhan.gov.in/JHWebService/gras_estamp_download/tx_002", "citizen_beta",
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "transactions.csv"
            appended = append_unique_transactions(csv_path, headers, [row1, row2])
            self.assertEqual(appended, 2)

            # Test duplicate skipping
            appended_dup = append_unique_transactions(csv_path, headers, [row1])
            self.assertEqual(appended_dup, 0)

            # Test upgrade from old 8-column format
            old_csv = Path(tmp_dir) / "old.csv"
            old_headers = ["Name", "Payment Date", "Amount", "Transaction ID", "GRN", "CIN", "Status", "Actions"]
            old_row = ["User Old", "2026-08-25", "20", "tx_old", "grn_0", "cin_0", "SUCCESS", ""]
            with old_csv.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(old_headers)
                writer.writerow(old_row)

            new_row = [
                "User New", "2026-08-26", "20", "tx_new", "grn_3", "cin_3", "SUCCESS", "",
                "https://jharnibandhan.gov.in/JHWebService/gras_estamp_download/tx_new", "citizen_gamma",
            ]
            appended_new = append_unique_transactions(old_csv, headers, [new_row])
            self.assertEqual(appended_new, 1)

            # Check migrated file content
            with old_csv.open("r", newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
                self.assertEqual(rows[0], headers)
                self.assertEqual(len(rows), 3)  # header + old_row (padded) + new_row
                self.assertEqual(rows[1][3], "tx_old")
                self.assertEqual(rows[1][8], "")  # padded
                self.assertEqual(rows[2][3], "tx_new")
                self.assertEqual(rows[2][8], "https://jharnibandhan.gov.in/JHWebService/gras_estamp_download/tx_new")
                self.assertEqual(rows[2][9], "citizen_gamma")

    def test_reconcile_transactions_extracts_url_and_user_id(self) -> None:
        headers = [
            "Name", "Payment Date", "Amount", "Transaction ID", "GRN", "CIN", "Status", "Actions",
            "eStamp Download URL", "User ID",
        ]
        row_created = [
            "User 0", "2026-08-25", "20", "tx_created", "grn_0", "cin_0", "CREATED", "",
            "", "user_0",
        ]
        row_fail = [
            "User F", "2026-08-25", "20", "tx_fail", "grn_f", "cin_f", "FAIL", "",
            "", "user_f",
        ]
        row_success = [
            "User 1", "2026-08-26", "20", "tx_abc", "grn_1", "cin_1", "SUCCESS", "",
            "https://jharnibandhan.gov.in/JHWebService/gras_estamp_download/tx_abc", "user_123",
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "tx.csv"
            stamps_dir = Path(tmp_dir) / "stamps"
            stamps_dir.mkdir()

            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerow(row_created)
                writer.writerow(row_fail)
                writer.writerow(row_success)

            report = reconcile_transactions(csv_path, stamps_dir)
            # Only row_success should be considered, CREATED and FAIL are ignored
            self.assertEqual(len(report.missing_pdfs), 1)
            missing = report.missing_pdfs[0]
            self.assertEqual(missing.transaction_id, "tx_abc")
            self.assertEqual(missing.estamp_url, "https://jharnibandhan.gov.in/JHWebService/gras_estamp_download/tx_abc")
            self.assertEqual(missing.user_id, "user_123")

    def test_download_missing_stamps(self) -> None:
        missing_item1 = MissingPdf(
            transaction_id="tx_111",
            values=[],
            estamp_url="https://jharnibandhan.gov.in/JHWebService/gras_estamp_download/tx_111",
            user_id="user_1",
        )
        missing_item2 = MissingPdf(
            transaction_id="tx_222",
            values=[],
            estamp_url="https://jharnibandhan.gov.in/JHWebService/gras_estamp_download/tx_222",
            user_id="user_2",
        )

        dummy_pdf = b"%PDF-1.4 " + b"x" * 600

        class MockResponse:
            def __init__(self, content: bytes, content_type: str = "application/pdf") -> None:
                self.content = content
                self.headers = MagicMock()
                self.headers.get_content_type.return_value = content_type

            def read(self) -> bytes:
                return self.content

            def __enter__(self) -> MockResponse:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp_dir:
            stamps_dir = Path(tmp_dir) / "stamps"

            # Pre-create tx_111 to test skipping existing
            stamps_dir.mkdir()
            existing_file = stamps_dir / "eStamp_tx_111.pdf"
            existing_file.write_bytes(dummy_pdf)

            with patch("urllib.request.urlopen", return_value=MockResponse(dummy_pdf)):
                summary = download_missing_stamps([missing_item1, missing_item2], stamps_dir, lambda _m: None)

            self.assertEqual(summary.total, 2)
            self.assertEqual(summary.skipped_existing, 1)
            self.assertEqual(summary.downloaded, 1)
            self.assertEqual(summary.failed, 0)
            self.assertTrue((stamps_dir / "eStamp_tx_222.pdf").is_file())

    def test_download_missing_stamps_stop_and_resume(self) -> None:
        items = [
            MissingPdf(
                transaction_id=f"tx_{i}",
                values=[],
                estamp_url=f"https://jharnibandhan.gov.in/JHWebService/gras_estamp_download/tx_{i}",
                user_id=f"user_{i}",
            )
            for i in range(1, 4)
        ]
        dummy_pdf = b"%PDF-1.4 " + b"x" * 600

        class MockResponse:
            def __init__(self, content: bytes, content_type: str = "application/pdf") -> None:
                self.content = content
                self.headers = MagicMock()
                self.headers.get_content_type.return_value = content_type

            def read(self) -> bytes:
                return self.content

            def __enter__(self) -> MockResponse:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp_dir:
            stamps_dir = Path(tmp_dir) / "stamps"
            controls = RunControls(lambda _e: None)

            # Hook report_status: when downloading tx_1 finishes, stop the download
            def on_status(msg: str) -> None:
                if "tx_1" in msg and "Successfully" in msg:
                    controls.stop("User stopped")

            with patch("urllib.request.urlopen", return_value=MockResponse(dummy_pdf)):
                summary1 = download_missing_stamps(items, stamps_dir, on_status, controls=controls)

            # First run downloaded tx_1 and stopped before tx_2 & tx_3
            self.assertEqual(summary1.downloaded, 1)
            self.assertTrue((stamps_dir / "eStamp_tx_1.pdf").is_file())
            self.assertFalse((stamps_dir / "eStamp_tx_2.pdf").is_file())

            # Second run: Resume!
            controls2 = RunControls(lambda _e: None)
            with patch("urllib.request.urlopen", return_value=MockResponse(dummy_pdf)):
                summary2 = download_missing_stamps(items, stamps_dir, lambda _m: None, controls=controls2)

            # Second run skips tx_1 (already existing) and downloads tx_2 & tx_3
            self.assertEqual(summary2.skipped_existing, 1)
            self.assertEqual(summary2.downloaded, 2)
            self.assertTrue((stamps_dir / "eStamp_tx_2.pdf").is_file())
            self.assertTrue((stamps_dir / "eStamp_tx_3.pdf").is_file())

    def test_dialog_deduplication_and_selection_defaults(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tkinter display not available")

        try:
            # Mock MainWindow with 4 tabs: 2 share the same citizen username
            owner = MagicMock()
            owner.root = root
            owner.config = AppConfig()
            owner.transaction_export_running = False
            owner._selected_tab.return_value = None
            owner.tab_accent_color.return_value = "#2563eb"

            browser = PortalBrowser("Managed Chrome", Path("/path/to/chrome"), BrowserEngine.CHROMIUM)
            owner.portal_browsers = {"Managed Chrome": browser}

            def make_mock_tab(
                tab_id: int, citizen_user: str, citizen_pwd: str = "pwd"
            ) -> MagicMock:
                tab = MagicMock()
                tab.tab_id = tab_id
                tab.display_name = f"ID {tab_id}"
                tab.config = TabConfig(tab_id=tab_id)
                tab.entered_credentials.return_value = Credentials(
                    citizen_username=citizen_user,
                    citizen_password=citizen_pwd,
                )
                tab._selected_browser.return_value = browser
                tab.sms_user_id_var = tk.StringVar(value=f"sms_{tab_id}")
                tab.sms_server_url_var = tk.StringVar(value="")
                tab.ocr_engine_var = tk.StringVar(value="paddleocr")
                tab.ocr_enabled_var = tk.BooleanVar(value=True)
                tab.captcha_copy_mode_var = tk.StringVar(value="direct_copy")
                tab.download_var = tk.StringVar(value="")
                tab.is_active = False
                tab.portal_session_open = False
                return tab

            tab1 = make_mock_tab(1, "user_alpha")
            tab2 = make_mock_tab(2, "user_beta")
            tab3 = make_mock_tab(3, "user_alpha")  # Duplicate username
            tab4 = make_mock_tab(4, "")  # No username

            owner.tabs = {1: tab1, 2: tab2, 3: tab3, 4: tab4}

            dialog = DownloadUndownloadedCertificatesDialog(owner, initial_tab=0)

            # Only IDs with a citizen username are listed: tab1 (user_alpha)
            # and tab2 (user_beta). tab3 is a duplicate username and tab4 has
            # no username, so both are hidden.
            self.assertEqual(len(dialog.id_items), 2)
            self.assertEqual(
                sorted(item.citizen_username for item in dialog.id_items),
                ["user_alpha", "user_beta"],
            )

            # All should be checked by default
            for item in dialog.id_items:
                self.assertTrue(item.var.get())

            self.assertIn("2 of 2 IDs selected (Automatic login mode)", dialog.selection_summary_var.get())

            # Deselect all -> manual mode
            dialog._deselect_all_ids()
            for item in dialog.id_items:
                self.assertFalse(item.var.get())
            self.assertEqual("0 IDs selected (Manual Citizen login mode)", dialog.selection_summary_var.get())

            # Select all -> auto mode
            dialog._select_all_ids()
            for item in dialog.id_items:
                self.assertTrue(item.var.get())
            self.assertIn("2 of 2 IDs selected (Automatic login mode)", dialog.selection_summary_var.get())

            # Check Use Chrome For All is True by default
            self.assertTrue(dialog.use_chrome_for_all_var.get())
            self.assertEqual(dialog._get_chrome_browser(), browser)

            dialog.dialog.destroy()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
