from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from core.models import PersistenceError, RowStatus, Stage

INPUT_COLUMNS = [
    "district",
    "first_party_name",
    "second_party_name",
    "stamp_duty_paid_by",
    "stamp_purpose",
    "pan",
    "mobile",
    "amount",
    "quantity",
]
STATE_COLUMNS = [
    "status",
    "completed_quantity",
    "processed_quantity",
    "completed_units",
    "processed_units",
    "attempt_count",
    "error_count",
    "last_stage",
    "last_error",
    "updated_at",
    "transaction_refs",
    "transaction_details",
    "estamp_files",
    "skipped_quantities",
]
ALL_STANDARD_COLUMNS = INPUT_COLUMNS + STATE_COLUMNS
RETIRED_COLUMNS = ("row_id", "article")


class CsvBatchStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fieldnames: list[str] = []
        self.rows: list[dict[str, str]] = []

    def load(self) -> None:
        if not self.path.is_file():
            raise PersistenceError(f"CSV file was not found: {self.path}")
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise PersistenceError("The CSV has no header row.")
                self.fieldnames = list(reader.fieldnames)
                self.rows = [
                    {key: value or "" for key, value in row.items() if key is not None}
                    for row in reader
                ]
        except UnicodeDecodeError as error:
            raise PersistenceError("The CSV must use UTF-8 encoding.") from error
        except OSError as error:
            raise PersistenceError(f"Could not read the CSV: {error}") from error

        for column in RETIRED_COLUMNS:
            if column in self.fieldnames:
                self.fieldnames.remove(column)
            for row in self.rows:
                row.pop(column, None)
        for column in ALL_STANDARD_COLUMNS:
            if column not in self.fieldnames:
                self.fieldnames.append(column)
        for row in self.rows:
            self._normalize(row)

    def validate_row(self, row: dict[str, str]) -> list[str]:
        errors: list[str] = []
        labels = {
            "district": "District",
            "first_party_name": "First party name",
            "stamp_duty_paid_by": "Stamp duty paid by",
            "stamp_purpose": "Stamp purpose",
            "amount": "Amount",
        }
        for key, label in labels.items():
            if not row.get(key, "").strip():
                errors.append(f"{label} is required")
        try:
            quantity = int(row.get("quantity", "1"))
            if quantity < 1:
                raise ValueError
        except ValueError:
            errors.append("Quantity must be a positive whole number")
        try:
            if row.get("amount", "").strip() and float(row["amount"]) <= 0:
                raise ValueError
        except ValueError:
            errors.append("Amount must be greater than zero")
        return errors

    def pending_rows(self) -> Iterable[tuple[int, dict[str, str]]]:
        for index, row in enumerate(self.rows):
            if self.pending_quantity_numbers(row):
                yield index, row

    def pending_quantity_numbers(self, row: dict[str, str]) -> list[int]:
        quantity = safe_positive_int(row["quantity"])
        processed = set(row_processed_unit_numbers(row, quantity))
        return [number for number in range(1, quantity + 1) if number not in processed]

    def next_pending_quantity(self, row: dict[str, str]) -> int:
        pending = self.pending_quantity_numbers(row)
        return pending[0] if pending else safe_positive_int(row["quantity"])

    def set_running(self, row: dict[str, str], stage: Stage) -> None:
        row["status"] = RowStatus.RUNNING
        row["attempt_count"] = str(int(row["attempt_count"]) + 1)
        self.set_stage(row, stage)

    def set_stage(self, row: dict[str, str], stage: Stage) -> None:
        row["last_stage"] = stage
        row["updated_at"] = utc_now()

    def mark_success(
        self,
        row: dict[str, str],
        transaction_ref: str,
        relative_file: str,
        details: dict[str, str] | None = None,
        quantity_number: int | None = None,
    ) -> None:
        quantity = safe_positive_int(row["quantity"])
        unit = quantity_number or self.next_pending_quantity(row)
        processed_units = set(row_processed_unit_numbers(row, quantity))
        completed_units = set(normalize_unit_numbers(row["completed_units"], quantity))
        processed_units.add(unit)
        completed_units.add(unit)
        row["processed_units"] = json.dumps(sorted(processed_units))
        row["completed_units"] = json.dumps(sorted(completed_units))
        row["completed_quantity"] = str(len(completed_units))
        row["processed_quantity"] = str(len(processed_units))
        row["transaction_refs"] = set_history_value(
            row["transaction_refs"], transaction_ref, quantity, unit
        )
        row["transaction_details"] = set_history_object(
            row["transaction_details"], details or {}, unit
        )
        row["estamp_files"] = set_history_value(
            row["estamp_files"], relative_file, quantity, unit
        )
        download_error = (details or {}).get("PDF error", "")
        row["last_error"] = f"PDF download failed: {download_error}" if download_error else ""
        row["last_stage"] = Stage.DOWNLOAD
        row["status"] = (
            RowStatus.COMPLETED
            if len(completed_units) >= quantity
            else RowStatus.PARTIAL
        )
        row["updated_at"] = utc_now()

    def mark_skipped_quantity(
        self,
        row: dict[str, str],
        quantity_number: int,
        stage: Stage,
        message: str,
    ) -> None:
        quantity = safe_positive_int(row["quantity"])
        processed_units = set(row_processed_unit_numbers(row, quantity))
        processed_units.add(quantity_number)
        row["processed_units"] = json.dumps(sorted(processed_units))
        row["processed_quantity"] = str(len(processed_units))
        row["skipped_quantities"] = append_history_object(
            row["skipped_quantities"],
            {
                "quantity": str(quantity_number),
                "stage": stage.value,
                "error": message[:1000],
                "skipped_at": utc_now(),
            },
        )
        row["last_stage"] = stage
        row["last_error"] = f"Quantity {quantity_number} skipped: {message}"[:1000]
        row["status"] = RowStatus.PARTIAL if int(row["completed_quantity"]) else RowStatus.ERROR
        row["updated_at"] = utc_now()

    def mark_skipped_row(self, row: dict[str, str], stage: Stage, message: str) -> int:
        """Mark every unfinished quantity in one CSV row as skipped."""
        pending = self.pending_quantity_numbers(row)
        for quantity_number in pending:
            self.mark_skipped_quantity(row, quantity_number, stage, message)
        return len(pending)

    def mark_error(self, row: dict[str, str], stage: Stage, message: str) -> None:
        row["error_count"] = str(int(row["error_count"]) + 1)
        row["last_stage"] = stage
        row["last_error"] = message[:1000]
        row["status"] = RowStatus.PARTIAL if int(row["completed_quantity"]) else RowStatus.ERROR
        row["updated_at"] = utc_now()

    def mark_stopped(self, row: dict[str, str], stage: Stage, message: str = "Stopped by user") -> None:
        row["status"] = RowStatus.STOPPED
        row["last_stage"] = stage
        row["last_error"] = message
        row["updated_at"] = utc_now()

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8-sig",
                newline="",
                delete=False,
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            ) as handle:
                temporary_path = Path(handle.name)
                writer = csv.DictWriter(handle, fieldnames=self.fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(self.rows)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except OSError as error:
            if temporary_path and temporary_path.exists():
                with suppress(OSError):
                    temporary_path.unlink()
            raise PersistenceError(
                "Could not save progress. Close the CSV in Excel or another program, then resume. "
                f"Details: {error}"
            ) from error

    def summaries(self) -> list[dict[str, str]]:
        return [
            {
                "row_number": str(index + 1),
                "status": row["status"],
                "completed": row["completed_quantity"],
                "processed": row["processed_quantity"],
                "quantity": row["quantity"],
                "district": row.get("district", ""),
                "first_party_name": row.get("first_party_name", ""),
                "second_party_name": row.get("second_party_name", ""),
                "amount": row.get("amount", ""),
                "error": row["last_error"],
            }
            for index, row in enumerate(self.rows)
        ]

    @staticmethod
    def write_template(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ALL_STANDARD_COLUMNS)
            writer.writeheader()
            writer.writerow(
                {
                    "second_party_name": "NIL",
                    "quantity": "1",
                    "status": RowStatus.PENDING,
                    "completed_quantity": "0",
                    "processed_quantity": "0",
                    "completed_units": "[]",
                    "processed_units": "[]",
                    "attempt_count": "0",
                    "error_count": "0",
                    "transaction_refs": "[]",
                    "transaction_details": "[]",
                    "estamp_files": "[]",
                    "skipped_quantities": "[]",
                }
            )

    def _normalize(self, row: dict[str, str]) -> None:
        for column in self.fieldnames:
            row.setdefault(column, "")
        row["quantity"] = row["quantity"].strip() or "1"
        row["second_party_name"] = row["second_party_name"].strip() or "NIL"
        if not row["processed_quantity"].strip():
            row["processed_quantity"] = row["completed_quantity"] or "0"
        for key in ("completed_quantity", "processed_quantity", "attempt_count", "error_count"):
            try:
                row[key] = str(max(0, int(row[key] or "0")))
            except ValueError:
                row[key] = "0"
        quantity = safe_positive_int(row["quantity"])
        completed_count = min(int(row["completed_quantity"]), quantity)
        processed_count = min(max(int(row["processed_quantity"]), completed_count), quantity)
        completed_units = normalize_unit_numbers(row.get("completed_units", ""), quantity)
        processed_units = normalize_unit_numbers(row.get("processed_units", ""), quantity)
        if not completed_units and completed_count:
            completed_units = list(range(1, completed_count + 1))
        if not processed_units and processed_count:
            processed_units = list(range(1, processed_count + 1))
        processed_units = sorted(set(processed_units) | set(completed_units))
        row["completed_units"] = json.dumps(completed_units)
        row["processed_units"] = json.dumps(processed_units)
        row["completed_quantity"] = str(len(completed_units))
        row["processed_quantity"] = str(len(processed_units))
        for key in ("transaction_refs", "estamp_files"):
            row[key] = normalize_history_value(row[key], quantity)
        for key in ("transaction_details", "skipped_quantities"):
            row[key] = normalize_history_objects(row[key])
        if row["status"] == RowStatus.RUNNING:
            row["status"] = RowStatus.ERROR
            row["last_error"] = "The previous application run ended before this unit finished."
        if len(completed_units) >= quantity:
            row["status"] = RowStatus.COMPLETED
        elif not row["status"]:
            row["status"] = RowStatus.PENDING


def normalize_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
        if not isinstance(parsed, list):
            raise ValueError
        return [str(item) for item in parsed]
    except (ValueError, TypeError, json.JSONDecodeError):
        return []


def normalize_unit_numbers(value: str, quantity: int) -> list[int]:
    try:
        parsed = json.loads(value or "[]")
    except (ValueError, TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    units: set[int] = set()
    for item in parsed:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= quantity:
            units.add(number)
    return sorted(units)


def row_processed_unit_numbers(row: dict[str, str], quantity: int) -> list[int]:
    processed = set(normalize_unit_numbers(row.get("processed_units", ""), quantity))
    try:
        legacy_count = min(quantity, max(0, int(row.get("processed_quantity", "0"))))
    except ValueError:
        legacy_count = 0
    if len(processed) < legacy_count:
        processed.update(range(1, legacy_count + 1))
    return sorted(processed)


def normalize_history_value(value: str, quantity: int) -> str:
    values = normalize_json_list(value)
    if not values and value.strip() and not value.lstrip().startswith("["):
        values = [value.strip()]
    if quantity == 1:
        return values[0] if values else ""
    return json.dumps(values)


def append_history_value(value: str, item: str, quantity: int) -> str:
    if quantity == 1:
        return item
    values = normalize_json_list(value)
    if not values and value.strip() and not value.lstrip().startswith("["):
        values = [value.strip()]
    values.append(item)
    return json.dumps(values)


def set_history_value(value: str, item: str, quantity: int, quantity_number: int) -> str:
    if quantity == 1:
        return item
    values = normalize_json_list(value)
    if not values and value.strip() and not value.lstrip().startswith("["):
        values = [value.strip()]
    while len(values) < quantity_number:
        values.append("")
    values[quantity_number - 1] = item
    return json.dumps(values)


def normalize_history_objects(value: str) -> str:
    try:
        parsed = json.loads(value or "[]")
    except (ValueError, TypeError, json.JSONDecodeError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    return json.dumps([item for item in parsed if isinstance(item, dict)])


def append_history_object(value: str, item: dict[str, str]) -> str:
    values = json.loads(normalize_history_objects(value))
    values.append(item)
    return json.dumps(values)


def set_history_object(value: str, item: dict[str, str], quantity_number: int) -> str:
    values = json.loads(normalize_history_objects(value))
    while len(values) < quantity_number:
        values.append({})
    values[quantity_number - 1] = item
    return json.dumps(values)


def safe_positive_int(value: str) -> int:
    try:
        return max(1, int(value))
    except ValueError:
        return 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
