"""Read receipt dates from downloaded eStamp PDFs."""

from __future__ import annotations

import datetime
import re
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


def find_pdf_folder_defaults(folder: Path) -> PdfFolderDefaults:
    """Read dates from all PDFs and filters from the first readable PDF."""
    dates: list[datetime.date] = []
    first_party_name = ""
    receipt_amount = ""
    read_first_pdf = False
    try:
        pdf_paths = sorted(
            path
            for path in folder.rglob("*")
            if path.is_file() and path.suffix.casefold() == ".pdf"
        )
    except Exception:
        return PdfFolderDefaults()
    for pdf_path in pdf_paths:
        text = _read_pdf_text(pdf_path)
        if not text:
            continue
        if not read_first_pdf:
            first_party_name = extract_first_party_name(text)
            receipt_amount = extract_receipt_amount(text)
            read_first_pdf = True
        receipt_date = extract_receipt_date(text)
        if receipt_date is not None:
            dates.append(receipt_date)
    if not dates:
        return PdfFolderDefaults(
            first_party_name=first_party_name,
            receipt_amount=receipt_amount,
        )
    return PdfFolderDefaults(
        date_from=min(dates),
        date_to=max(dates),
        first_party_name=first_party_name,
        receipt_amount=receipt_amount,
    )


def _read_receipt_date(pdf_path: Path) -> datetime.date | None:
    text = _read_pdf_text(pdf_path)
    return extract_receipt_date(text) if text else None


def _read_pdf_text(pdf_path: Path) -> str:
    try:
        pages: list[str] = []
        with pdfium.PdfDocument(pdf_path) as document:
            for page_index in range(len(document)):
                page = document[page_index]
                try:
                    text_page = page.get_textpage()
                    try:
                        pages.append(text_page.get_text_range())
                    finally:
                        text_page.close()
                finally:
                    page.close()
        return "\n".join(pages)
    except Exception:
        return ""
