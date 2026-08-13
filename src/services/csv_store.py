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
    "attempt_count",
    "error_count",
    "last_stage",
    "last_error",
    "updated_at",
    "transaction_refs",
    "estamp_files",
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
            "mobile": "Mobile",
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
        mobile = row.get("mobile", "").strip()
        if mobile and (not mobile.isdigit() or len(mobile) != 10):
            errors.append("Mobile must contain exactly 10 digits")
        try:
            if row.get("amount", "").strip() and float(row["amount"]) <= 0:
                raise ValueError
        except ValueError:
            errors.append("Amount must be greater than zero")
        return errors

    def pending_rows(self) -> Iterable[tuple[int, dict[str, str]]]:
        for index, row in enumerate(self.rows):
            if int(row["completed_quantity"]) < safe_positive_int(row["quantity"]):
                yield index, row

    def set_running(self, row: dict[str, str], stage: Stage) -> None:
        row["status"] = RowStatus.RUNNING
        row["attempt_count"] = str(int(row["attempt_count"]) + 1)
        self.set_stage(row, stage)

    def set_stage(self, row: dict[str, str], stage: Stage) -> None:
        row["last_stage"] = stage
        row["updated_at"] = utc_now()

    def mark_success(self, row: dict[str, str], transaction_ref: str, relative_file: str) -> None:
        completed = int(row["completed_quantity"]) + 1
        row["completed_quantity"] = str(completed)
        row["transaction_refs"] = append_json_value(row["transaction_refs"], transaction_ref)
        row["estamp_files"] = append_json_value(row["estamp_files"], relative_file)
        row["last_error"] = ""
        row["last_stage"] = Stage.DOWNLOAD
        row["status"] = (
            RowStatus.COMPLETED if completed >= int(row["quantity"]) else RowStatus.PARTIAL
        )
        row["updated_at"] = utc_now()

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
                    "attempt_count": "0",
                    "error_count": "0",
                    "transaction_refs": "[]",
                    "estamp_files": "[]",
                }
            )

    def _normalize(self, row: dict[str, str]) -> None:
        for column in self.fieldnames:
            row.setdefault(column, "")
        row["quantity"] = row["quantity"].strip() or "1"
        row["second_party_name"] = row["second_party_name"].strip() or "NIL"
        for key in ("completed_quantity", "attempt_count", "error_count"):
            try:
                row[key] = str(max(0, int(row[key] or "0")))
            except ValueError:
                row[key] = "0"
        for key in ("transaction_refs", "estamp_files"):
            row[key] = normalize_json_list(row[key])
        if row["status"] == RowStatus.RUNNING:
            row["status"] = RowStatus.ERROR
            row["last_error"] = "The previous application run ended before this unit finished."
        if int(row["completed_quantity"]) >= safe_positive_int(row["quantity"]):
            row["status"] = RowStatus.COMPLETED
        elif not row["status"]:
            row["status"] = RowStatus.PENDING


def normalize_json_list(value: str) -> str:
    try:
        parsed = json.loads(value or "[]")
        if not isinstance(parsed, list):
            raise ValueError
        return json.dumps([str(item) for item in parsed])
    except (ValueError, TypeError, json.JSONDecodeError):
        return "[]"


def append_json_value(value: str, item: str) -> str:
    parsed = json.loads(normalize_json_list(value))
    parsed.append(item)
    return json.dumps(parsed)


def safe_positive_int(value: str) -> int:
    try:
        return max(1, int(value))
    except ValueError:
        return 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
