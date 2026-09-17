from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from services.transaction_reconciliation import (
    reconcile_transactions,
    write_reconciliation_csv,
    write_reconciliation_text,
)


class TransactionReconciliationTests(unittest.TestCase):
    def test_reports_missing_and_unmatched_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "transactions.csv"
            stamps = root / "stamps"
            nested_stamps = stamps / "nested"
            nested_stamps.mkdir(parents=True)
            csv_path.write_text(
                "Name,Transaction ID,Status\nFirst,txn-1,SUCCESS\nSecond,txn-2,SUCCESS\n",
                encoding="utf-8",
            )
            (stamps / "eStamp_txn-1.pdf").touch()
            (nested_stamps / "eStamp_txn-3.pdf").touch()

            report = reconcile_transactions(csv_path, stamps)

            self.assertEqual([item.transaction_id for item in report.missing_pdfs], ["txn-2"])
            self.assertEqual([item.transaction_id for item in report.unmatched_pdfs], ["txn-3"])

            output_csv = root / "report.csv"
            write_reconciliation_csv(output_csv, report)
            with output_csv.open("r", newline="", encoding="utf-8-sig") as file:
                rows = list(csv.reader(file))
            self.assertEqual(rows[1][0], "Transactions without PDF")
            self.assertEqual(rows[2][0], "PDFs without CSV transaction")

            output_text = root / "report.txt"
            write_reconciliation_text(output_text, report)
            text = output_text.read_text(encoding="utf-8")
            self.assertIn("Transactions without PDF", text)
            self.assertIn("PDFs without CSV transaction", text)
