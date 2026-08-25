from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from services.estamp_transactions import append_unique_transactions


class TransactionExportTests(unittest.TestCase):
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
