"""Read receipt dates from downloaded eStamp PDFs."""

from __future__ import annotations

import datetime
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

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
    """Extract date/name/amount from the first page of one PDF."""
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

    Dates are streamed into min/max (no list buildup) and PDFs are read
    in parallel. ``progress_callback(done, total)`` is invoked from the
    calling thread; ``should_stop`` aborts early with partial results.
    """
    pdf_paths = _list_pdf_paths(folder)
    total = len(pdf_paths)
    if total == 0:
        return PdfFolderDefaults()

    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    first_party_name = ""
    receipt_amount = ""
    first_readable_index: int | None = None

    def _absorb(
        index: int,
        receipt_date: datetime.date | None,
        name: str,
        amount: str,
        has_text: bool,
    ) -> None:
        nonlocal date_from, date_to, first_party_name, receipt_amount, first_readable_index
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

    if total <= 16 or max_workers <= 1:
        for index, pdf_path in enumerate(pdf_paths):
            if should_stop is not None and should_stop():
                break
            receipt_date, name, amount, has_text = _scan_single_pdf(pdf_path)
            _absorb(index, receipt_date, name, amount, has_text)
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

    try:
        cpu_hint = (os.cpu_count() or 4) + 4
    except Exception:
        cpu_hint = 8
    workers = max(1, min(max_workers, total, cpu_hint, 8))
    done = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_index = {
                executor.submit(_scan_single_pdf, pdf_path): index
                for index, pdf_path in enumerate(pdf_paths)
            }
            for future in as_completed(future_to_index):
                if should_stop is not None and should_stop():
                    for pending in future_to_index:
                        pending.cancel()
                    break
                index = future_to_index[future]
                try:
                    receipt_date, name, amount, has_text = future.result()
                except Exception:
                    receipt_date, name, amount, has_text = None, "", "", False
                _absorb(index, receipt_date, name, amount, has_text)
                done += 1
                if progress_callback is not None:
                    try:
                        progress_callback(done, total)
                    except Exception:
                        pass
    except Exception:
        # Fall back to whatever partial results were collected.
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
    """Return text from the first page only (eStamps are single-page)."""
    try:
        with pdfium.PdfDocument(pdf_path) as document:
            if len(document) == 0:
                return ""
            page = document[0]
            try:
                text_page = page.get_textpage()
                try:
                    return text_page.get_text_range()
                finally:
                    text_page.close()
            finally:
                page.close()
    except Exception:
        return ""
