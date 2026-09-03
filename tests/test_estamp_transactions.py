from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from services.estamp_transactions import _successful_rows_in_payment_date_range, append_unique_transactions


class TransactionExportTests(unittest.TestCase):
    def test_filters_to_successful_payments_inside_an_inclusive_date_range(self) -> None:
        headers = ["Name", "Payment Date", "Transaction ID", "Status"]
        rows = [
            ["Before", "2026-08-20", "tx-before", "SUCCESS"],
            ["Created", "", "tx-created", "CREATED"],
            ["Start", "21/08/2026", "tx-start", "SUCCESS"],
            ["End", "2026-08-22 09:30:00", "tx-end", "SUCCESS"],
            ["Failed", "2026-08-22", "tx-failed", "FAILED"],
            ["After", "2026-08-23", "tx-after", "SUCCESS"],
        ]

        selected = _successful_rows_in_payment_date_range(
            rows,
            headers,
            payment_date_from=date(2026, 8, 21),
            payment_date_to=date(2026, 8, 22),
        )

        self.assertEqual([row[2] for row in selected], ["tx-start", "tx-end"])

    def test_appends_only_new_transaction_ids(self) -> None:
        headers = ["Name", "Transaction ID", "Status"]
        first_rows = [["First", "txn-1", "SUCCESS"], ["Second", "txn-2", "CREATED"]]
        later_rows = [["First", "txn-1", "SUCCESS"], ["Third", "txn-3", "SUCCESS"]]

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "transactions.csv"

            self.assertEqual(append_unique_transactions(output_path, headers, first_rows), 2)
            self.assertEqual(append_unique_transactions(output_path, headers, later_rows), 1)

            with output_path.open("r", newline="", encoding="utf-8-sig") as file:
                self.assertEqual(list(csv.reader(file)), [headers, *first_rows, later_rows[1]])

    def test_rejects_output_with_different_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "transactions.csv"
            output_path.write_text("Transaction ID,Other header\ntxn-1,value\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "different columns"):
                append_unique_transactions(output_path, ["Transaction ID"], [["txn-1"]])
