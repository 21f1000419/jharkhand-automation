"""Read receipt dates from downloaded eStamp PDFs."""

from __future__ import annotations

import datetime
import os
import re
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from contextlib import suppress
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

    PDFium is not thread-safe, so parallel scans use separate processes.
    Worker failures and blank reads are retried in the calling process.
    ``progress_callback(done, total)`` reports progress; ``should_stop``
    aborts early with partial results.
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
    completed_indexes: set[int] = set()
    done_count = 0

    def absorb(
        index: int,
        result: tuple[datetime.date | None, str, str, bool],
    ) -> None:
        nonlocal date_from, date_to, first_party_name, receipt_amount, first_readable_index
        nonlocal done_count
        receipt_date, name, amount, has_text = result
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
        completed_indexes.add(index)
        done_count += 1
        if progress_callback is not None:
            with suppress(Exception):
                progress_callback(done_count, total)

    def scan_sequentially(indexes: list[int]) -> None:
        for index in indexes:
            if should_stop is not None and should_stop():
                return
            absorb(index, _scan_single_pdf(pdf_paths[index]))

    if total <= 16 or max_workers <= 1:
        scan_sequentially(list(range(total)))
        return PdfFolderDefaults(
            date_from=date_from,
            date_to=date_to,
            first_party_name=first_party_name,
            receipt_amount=receipt_amount,
        )

    workers = max(1, min(max_workers, total, os.cpu_count() or 1, 8))
    executor: ProcessPoolExecutor | None = None
    pending: dict[
        Future[tuple[datetime.date | None, str, str, bool]], tuple[int, Path]
    ] = {}
    next_index = 0
    stopped = False

    def submit_until_full() -> None:
        nonlocal next_index
        assert executor is not None
        while next_index < total and len(pending) < workers * 2:
            pdf_path = pdf_paths[next_index]
            pending[executor.submit(_scan_single_pdf, pdf_path)] = (next_index, pdf_path)
            next_index += 1

    try:
        executor = ProcessPoolExecutor(max_workers=workers)
        submit_until_full()
        while pending:
            if should_stop is not None and should_stop():
                stopped = True
                break
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                index, pdf_path = pending.pop(future)
                try:
                    result = future.result()
                except Exception:
                    result = _scan_single_pdf(pdf_path)
                else:
                    if not result[3]:
                        result = _scan_single_pdf(pdf_path)
                absorb(index, result)
            submit_until_full()
    except Exception:
        # A process may fail to start in a restricted or frozen runtime.
        # Complete every unprocessed file in the caller rather than return
        # a plausible-looking but incomplete range.
        pass
    finally:
        for future in pending:
            future.cancel()
        if executor is not None:
            executor.shutdown(wait=not stopped, cancel_futures=True)

    if not stopped:
        remaining = [index for index in range(total) if index not in completed_indexes]
        scan_sequentially(remaining)
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
