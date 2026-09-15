from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from core.models import RowStatus, Stage
from services.csv_store import ALL_STANDARD_COLUMNS, CsvBatchStore


class CsvBatchStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "batch.csv"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_rows(self, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
        with self.path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_load_adds_state_and_defaults_without_losing_unknown_columns(self) -> None:
        fields = [column for column in ALL_STANDARD_COLUMNS if column not in {"quantity", "status"}]
        fields.append("customer_note")
        self.write_rows(
            fields,
            [
                {
                    "district": "Ranchi",
                    "first_party_name": "First",
                    "stamp_duty_paid_by": "First",
                    "stamp_purpose": "Test",
                    "mobile": "9999999999",
                    "amount": "50",
                    "customer_note": "preserve me",
                }
            ],
        )

        store = CsvBatchStore(self.path)
        store.load()

        self.assertEqual(store.rows[0]["quantity"], "1")
        self.assertEqual(store.rows[0]["second_party_name"], "NIL")
        self.assertEqual(store.rows[0]["status"], RowStatus.PENDING)
        self.assertEqual(store.rows[0]["customer_note"], "preserve me")
        self.assertNotIn("row_id", store.fieldnames)
        self.assertEqual(store.summaries()[0]["row_number"], "1")
        store.persist()
        self.assertTrue(self.path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_success_history_and_quantity_are_persisted(self) -> None:
        CsvBatchStore.write_template(self.path)
        store = CsvBatchStore(self.path)
        store.load()
        row = store.rows[0]
        row["quantity"] = "2"

        store.set_running(row, Stage.CITIZEN_LOGIN)
        store.mark_success(
            row,
            "ref-one",
            "downloads/one.pdf",
            {"Transaction ID": "ref-one", "PDF status": "saved", "PDF error": ""},
        )
        self.assertEqual(row["status"], RowStatus.PARTIAL)
        store.set_running(row, Stage.CITIZEN_LOGIN)
        store.mark_success(
            row,
            "ref-two",
            "downloads/two.pdf",
            {"Transaction ID": "ref-two", "PDF status": "saved", "PDF error": ""},
        )

        self.assertEqual(row["completed_quantity"], "2")
        self.assertEqual(row["processed_quantity"], "2")
        self.assertEqual(row["status"], RowStatus.COMPLETED)
        self.assertEqual(json.loads(row["transaction_refs"]), ["ref-one", "ref-two"])
        self.assertEqual(json.loads(row["estamp_files"]), ["downloads/one.pdf", "downloads/two.pdf"])
        self.assertEqual(
            [details["Transaction ID"] for details in json.loads(row["transaction_details"])],
            ["ref-one", "ref-two"],
        )

    def test_single_quantity_stores_result_values_directly(self) -> None:
        CsvBatchStore.write_template(self.path)
        store = CsvBatchStore(self.path)
        store.load()
        row = store.rows[0]

        store.mark_success(row, "ref-one", "downloads/one.pdf")

        self.assertEqual(row["transaction_refs"], "ref-one")
        self.assertEqual(row["estamp_files"], "downloads/one.pdf")

    def test_skip_advances_processed_quantity_without_counting_success(self) -> None:
        CsvBatchStore.write_template(self.path)
        store = CsvBatchStore(self.path)
        store.load()
        row = store.rows[0]
        row["quantity"] = "2"

        store.mark_skipped_quantity(row, 1, Stage.EGRAS_LOGIN, "Portal rejected the CAPTCHA")

        self.assertEqual(row["completed_quantity"], "0")
        self.assertEqual(row["processed_quantity"], "1")
        self.assertEqual(row["status"], RowStatus.ERROR)
        self.assertEqual(json.loads(row["skipped_quantities"])[0]["quantity"], "1")
        self.assertEqual(len(list(store.pending_rows())), 1)

        store.mark_success(
            row,
            "transaction-two",
            "",
            {
                "Transaction ID": "transaction-two",
                "PDF status": "failed",
                "PDF error": "HTTP 500",
            },
        )

        self.assertEqual(row["completed_quantity"], "1")
        self.assertEqual(row["processed_quantity"], "2")
        self.assertEqual(row["status"], RowStatus.PARTIAL)
        self.assertIn("PDF download failed", row["last_error"])
        self.assertEqual(len(list(store.pending_rows())), 0)

    def test_skip_row_marks_every_unfinished_quantity_processed(self) -> None:
        CsvBatchStore.write_template(self.path)
        store = CsvBatchStore(self.path)
        store.load()
        row = store.rows[0]
        row["quantity"] = "4"
        row["processed_quantity"] = "1"

        skipped = store.mark_skipped_row(
            row,
            Stage.EGRAS_OTP,
            "Browser was closed",
        )

        self.assertEqual(skipped, 3)
        self.assertEqual(row["processed_quantity"], "4")
        self.assertEqual(
            [item["quantity"] for item in json.loads(row["skipped_quantities"])],
            ["2", "3", "4"],
        )
        self.assertEqual(len(list(store.pending_rows())), 0)

    def test_out_of_order_parallel_results_keep_quantity_slots_and_resume_safely(self) -> None:
        CsvBatchStore.write_template(self.path)
        store = CsvBatchStore(self.path)
        store.load()
        row = store.rows[0]
        row["quantity"] = "3"

        store.mark_success(
            row,
            "ref-three",
            "downloads/three.pdf",
            {"Transaction ID": "three"},
            quantity_number=3,
        )
        store.mark_success(
            row,
            "ref-one",
            "downloads/one.pdf",
            {"Transaction ID": "one"},
            quantity_number=1,
        )
        store.persist()

        reloaded = CsvBatchStore(self.path)
        reloaded.load()
        row = reloaded.rows[0]
        self.assertEqual(reloaded.pending_quantity_numbers(row), [2])
        self.assertEqual(json.loads(row["processed_units"]), [1, 3])
        self.assertEqual(
            json.loads(row["transaction_refs"]),
            ["ref-one", "", "ref-three"],
        )

    def test_interrupted_running_row_becomes_retryable_error(self) -> None:
        CsvBatchStore.write_template(self.path)
        store = CsvBatchStore(self.path)
        store.load()
        store.rows[0]["status"] = RowStatus.RUNNING
        store.persist()

        reloaded = CsvBatchStore(self.path)
        reloaded.load()

        self.assertEqual(reloaded.rows[0]["status"], RowStatus.ERROR)
        self.assertIn("previous application run", reloaded.rows[0]["last_error"])
        self.assertEqual(len(list(reloaded.pending_rows())), 1)

    def test_invalid_quantity_is_reported_without_breaking_pending_scan(self) -> None:
        CsvBatchStore.write_template(self.path)
        store = CsvBatchStore(self.path)
        store.load()
        store.rows[0]["quantity"] = "many"

        self.assertEqual(len(list(store.pending_rows())), 1)
        self.assertIn("Quantity must be a positive whole number", store.validate_row(store.rows[0]))

    def test_mobile_is_optional_and_not_format_validated(self) -> None:
        CsvBatchStore.write_template(self.path)
        store = CsvBatchStore(self.path)
        store.load()
        row = store.rows[0]
        row.update(
            {
                "district": "Ranchi",
                "first_party_name": "First Party",
                "stamp_duty_paid_by": "First Party",
                "stamp_purpose": "Test purpose",
                "amount": "50",
                "mobile": "",
            }
        )
        self.assertEqual(store.validate_row(row), [])

        row["mobile"] = "not-a-phone-number"
        self.assertEqual(store.validate_row(row), [])

    def test_legacy_article_and_row_id_columns_are_removed(self) -> None:
        fields = ["row_id", "article", "district", "quantity"]
        self.write_rows(
            fields,
            [{"row_id": "generated-id", "article": "Old Article", "district": "Ranchi", "quantity": "1"}],
        )

        store = CsvBatchStore(self.path)
        store.load()
        store.persist()

        self.assertNotIn("row_id", store.fieldnames)
        self.assertNotIn("article", store.fieldnames)
        self.assertNotIn("row_id", store.rows[0])
        self.assertNotIn("article", store.rows[0])


if __name__ == "__main__":
    unittest.main()
