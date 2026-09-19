"""Read receipt dates from downloaded eStamp PDFs."""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium  # type: ignore[import-untyped]

_RECEIPT_DATE = re.compile(
    r"Receipt\s+Date\s*:\s*(?P<day>\d{1,2})-(?P<month>[A-Za-z]{3})-(?P<year>\d{4})",
    re.IGNORECASE,
)
_RECEIPT_AMOUNT = re.compile(
    r"Receipt\s+Amount\s*:\s*(?P<value>[\d,]+(?:\.\d+)?)\s*(?:/-)?",
    re.IGNORECASE,
)
_FIRST_PARTY_NAME = re.compile(
    r"First\s+Party\s+Name\s*:\s*(?P<value>[^\r\n]+)",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


@dataclass(frozen=True)
class PdfFolderDefaults:
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    first_party_name: str = ""
    receipt_amount: str = ""


def extract_receipt_date(text: str) -> datetime.date | None:
    """Return the receipt date embedded in eStamp text, if present."""
    match = _RECEIPT_DATE.search(text)
    if match is None:
        return None
    month = _MONTHS.get(match.group("month").casefold())
    if month is None:
        return None
    try:
        return datetime.date(int(match.group("year")), month, int(match.group("day")))
    except ValueError:
        return None


def extract_first_party_name(text: str) -> str:
    """Return the first-party name from eStamp text, if present."""
    match = _FIRST_PARTY_NAME.search(text)
    return " ".join(match.group("value").split()) if match else ""


def extract_receipt_amount(text: str) -> str:
    """Return the numeric receipt amount from eStamp text, if present."""
    match = _RECEIPT_AMOUNT.search(text)
    return match.group("value").replace(",", "") if match else ""


def find_receipt_date_range(folder: Path) -> tuple[datetime.date, datetime.date] | None:
    """Return the earliest and latest receipt dates found below a folder."""
    defaults = find_pdf_folder_defaults(folder)
    if defaults.date_from is None or defaults.date_to is None:
        return None
    return defaults.date_from, defaults.date_to


def _list_pdf_paths(folder: Path) -> list[Path]:
    """List PDFs below a folder, cheapest glob first."""
    try:
        paths = [
            path
            for path in folder.rglob("*.[pP][dD][fF]")
            if path.is_file() and path.suffix.casefold() == ".pdf"
        ]
    except Exception:
        return []
    try:
        return sorted(paths, key=lambda path: str(path).casefold())
    except Exception:
        return sorted(paths)


def _scan_single_pdf(
    pdf_path: Path,
) -> tuple[datetime.date | None, str, str, bool]:
    """Extract date/name/amount from one PDF (first page, rest as fallback)."""
    text = _read_pdf_text(pdf_path)
    if not text:
        return None, "", "", False
    return (
        extract_receipt_date(text),
        extract_first_party_name(text),
        extract_receipt_amount(text),
        True,
    )


def find_pdf_folder_defaults(
    folder: Path,
    *,
    max_workers: int = 8,
    progress_callback: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> PdfFolderDefaults:
    """Read dates from all PDFs and filters from the first readable PDF.

    Dates are streamed into min/max (no list buildup). PDFs are read
    sequentially because pypdfium2/PDFium is not thread-safe: reading
    documents from a thread pool hangs and silently drops files, which
    previously produced a wrong range (e.g. 17-19 instead of 13-19).
    ``max_workers`` is accepted for API compatibility but ignored.
    ``progress_callback(done, total)`` reports progress; ``should_stop``
    aborts early with partial results.
    """
    _ = max_workers
    pdf_paths = _list_pdf_paths(folder)
    total = len(pdf_paths)
    if total == 0:
        return PdfFolderDefaults()

    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    first_party_name = ""
    receipt_amount = ""
    first_readable_index: int | None = None

    for index, pdf_path in enumerate(pdf_paths):
        if should_stop is not None and should_stop():
            break
        receipt_date, name, amount, has_text = _scan_single_pdf(pdf_path)
        if receipt_date is not None:
            if date_from is None or receipt_date < date_from:
                date_from = receipt_date
            if date_to is None or receipt_date > date_to:
                date_to = receipt_date
        if has_text and (
            first_readable_index is None or index < first_readable_index
        ):
            first_readable_index = index
            first_party_name = name
            receipt_amount = amount
        if progress_callback is not None:
            try:
                progress_callback(index + 1, total)
            except Exception:
                pass
    return PdfFolderDefaults(
        date_from=date_from,
        date_to=date_to,
        first_party_name=first_party_name,
        receipt_amount=receipt_amount,
    )


def _read_receipt_date(pdf_path: Path) -> datetime.date | None:
    text = _read_pdf_text(pdf_path)
    return extract_receipt_date(text) if text else None


def _read_pdf_text(pdf_path: Path) -> str:
    """Return text from a PDF, first page first.

    eStamps are single-page, so the first page is read first. If the
    first page yields no text (or the document has more pages and the
    receipt fields were not found there), the remaining pages are read
    as a fallback so multi-page PDFs are not silently skipped.
    """
    try:
        with pdfium.PdfDocument(pdf_path) as document:
            if len(document) == 0:
                return ""
            first = _read_page_text(document, 0)
            if len(document) == 1:
                return first
            if (
                extract_receipt_date(first) is not None
                and extract_first_party_name(first)
                and extract_receipt_amount(first)
            ):
                return first
            rest = [_read_page_text(document, index) for index in range(1, len(document))]
            return "\n".join([first, *rest])
    except Exception:
        return ""


def _read_page_text(document: Any, page_index: int) -> str:
    """Return the text of one PDF page, closing native handles."""
    page = document[page_index]
    try:
        text_page = page.get_textpage()
        try:
            return str(text_page.get_text_range())
        finally:
            text_page.close()
    finally:
        page.close()
