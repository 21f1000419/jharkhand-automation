"""Compare exported payment transactions with downloaded eStamp PDFs."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

_TRANSACTION_ID_HEADER = "transaction id"
_DOWNLOADED_STAMP_NAME = re.compile(
    r"^estamp_row-\d+_unit-\d+_(?P<transaction_id>[A-Za-z0-9_-]+)$", re.IGNORECASE
)


@dataclass(frozen=True)
class MissingPdf:
    transaction_id: str
    values: list[str]


@dataclass(frozen=True)
class UnmatchedPdf:
    path: Path
    transaction_id: str


@dataclass(frozen=True)
class TransactionReconciliation:
    headers: list[str]
    transaction_id_index: int
    missing_pdfs: list[MissingPdf]
    unmatched_pdfs: list[UnmatchedPdf]


def reconcile_transactions(csv_path: Path, stamps_directory: Path) -> TransactionReconciliation:
    """Return CSV transactions without PDFs and PDFs without CSV transactions."""
    headers, transaction_id_index, transactions = _read_transactions(csv_path)
    if not stamps_directory.is_dir():
        raise RuntimeError(f"The selected stamp folder does not exist: {stamps_directory}")

    transaction_by_key = {
        transaction_id.casefold(): (transaction_id, values)
        for transaction_id, values in transactions
    }
    found_transaction_ids: set[str] = set()
    unmatched_pdfs: list[UnmatchedPdf] = []
    for pdf_path in sorted(
        (path for path in stamps_directory.rglob("*") if path.is_file() and path.suffix.casefold() == ".pdf"),
        key=lambda path: str(path).casefold(),
    ):
        transaction_id = _transaction_id_from_filename(pdf_path, transaction_by_key)
        key = transaction_id.casefold()
        if key in transaction_by_key:
            found_transaction_ids.add(key)
        else:
            unmatched_pdfs.append(UnmatchedPdf(pdf_path, transaction_id))

    missing_pdfs = [
        MissingPdf(transaction_id, values)
        for transaction_id, values in transactions
        if transaction_id.casefold() not in found_transaction_ids
    ]
    return TransactionReconciliation(headers, transaction_id_index, missing_pdfs, unmatched_pdfs)


def write_reconciliation_csv(output_path: Path, report: TransactionReconciliation) -> None:
    """Write both reconciliation result groups to one CSV with a Section column."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["Section", "PDF Path", *report.headers])
        for item in report.missing_pdfs:
            writer.writerow(["Transactions without PDF", "", *item.values])
        for unmatched_pdf in report.unmatched_pdfs:
            values = [""] * len(report.headers)
            values[report.transaction_id_index] = unmatched_pdf.transaction_id
            writer.writerow(["PDFs without CSV transaction", str(unmatched_pdf.path), *values])


def write_reconciliation_text(output_path: Path, report: TransactionReconciliation) -> None:
    """Write the same result groups as a plainly readable text report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "Transactions without PDF",
        "=" * 24,
        *(_format_missing_pdf(item, report.headers) for item in report.missing_pdfs),
        "",
        "PDFs without CSV transaction",
        "=" * 28,
        *(
            f"{unmatched_pdf.transaction_id or '(Transaction ID not found)'} | {unmatched_pdf.path}"
            for unmatched_pdf in report.unmatched_pdfs
        ),
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_transactions(csv_path: Path) -> tuple[list[str], int, list[tuple[str, list[str]]]]:
    if not csv_path.is_file():
        raise RuntimeError(f"The selected transaction CSV does not exist: {csv_path}")
    with csv_path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file)
        headers = next(reader, None)
        if not headers:
            raise RuntimeError("The selected transaction CSV has no header row.")
        transaction_id_index = _transaction_id_index(headers)
        transactions: list[tuple[str, list[str]]] = []
        seen: set[str] = set()
        for values in reader:
            if len(values) != len(headers):
                continue
            transaction_id = values[transaction_id_index].strip()
            key = transaction_id.casefold()
            if not transaction_id or key in seen:
                continue
            seen.add(key)
            transactions.append((transaction_id, values))
    return headers, transaction_id_index, transactions


def _transaction_id_from_filename(
    pdf_path: Path, transactions: dict[str, tuple[str, list[str]]]
) -> str:
    match = _DOWNLOADED_STAMP_NAME.match(pdf_path.stem)
    if match:
        return match.group("transaction_id")
    lowered_name = pdf_path.name.casefold()
    for transaction_id in sorted(transactions, key=len, reverse=True):
        if transaction_id in lowered_name:
            return transactions[transaction_id][0]
    return ""


def _transaction_id_index(headers: list[str]) -> int:
    for index, header in enumerate(headers):
        if " ".join(header.casefold().split()) == _TRANSACTION_ID_HEADER:
            return index
    raise RuntimeError("The selected transaction CSV does not include a Transaction ID column.")


def _format_missing_pdf(item: MissingPdf, headers: list[str]) -> str:
    values = "; ".join(
        f"{header}={value}" for header, value in zip(headers, item.values, strict=True) if value
    )
    return values or item.transaction_id
