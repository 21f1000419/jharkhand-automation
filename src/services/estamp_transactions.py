"""Collect eStamp payment transactions after a user completes Citizen login."""

from __future__ import annotations

import csv
import re
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from automation.browser import PortalBrowserSession
from automation.portal import CITIZEN_LOGIN_URL
from core.models import PortalBrowser

TRANSACTIONS_URL = "https://jharnibandhan.gov.in/JHWebService/gras_payment_entry_estamp"
_TABLE_SELECTOR = "#translist"
_PAGE_INFO_SELECTOR = "#translist_info"
_NEXT_PAGE_SELECTOR = "#translist_next"
_PAGE_SIZE_SELECTOR = "select[name='translist_length']"
_TRANSACTION_ID_HEADER = "transaction id"
_LOGIN_TIMEOUT_MS = 30 * 60 * 1_000


@dataclass(frozen=True)
class TransactionExportSummary:
    scraped_rows: int
    appended_rows: int
    skipped_duplicates: int
    output_path: Path


async def export_payment_transactions(
    browser: PortalBrowser,
    profile_path: Path,
    output_path: Path,
    report_status: Callable[[str], None],
) -> TransactionExportSummary:
    """Open Citizen login, wait for manual login, then export all transaction pages."""
    session = PortalBrowserSession(browser, lambda _session: None, profile_path)
    try:
        page = await session.new_portal_page(CITIZEN_LOGIN_URL)
        report_status("Complete Citizen login in the opened browser...")
        await page.wait_for_url(
            re.compile(r"/Citizenentry/welcome(?:[/?#]|$)", re.IGNORECASE),
            timeout=_LOGIN_TIMEOUT_MS,
        )

        report_status("Opening eStamp payment transactions...")
        await page.goto(TRANSACTIONS_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.locator(_TABLE_SELECTOR).wait_for(state="visible", timeout=120_000)
        headers, rows = await _collect_table_pages(page, report_status)
        appended_rows = append_unique_transactions(output_path, headers, rows)
        return TransactionExportSummary(
            scraped_rows=len(rows),
            appended_rows=appended_rows,
            skipped_duplicates=len(rows) - appended_rows,
            output_path=output_path,
        )
    finally:
        await session.close()


async def _collect_table_pages(
    page: Page, report_status: Callable[[str], None]
) -> tuple[list[str], list[list[str]]]:
    await _select_page_size(page)
    header_values = await page.locator(f"{_TABLE_SELECTOR} thead th").all_inner_texts()
    headers = [_clean_cell(value) for value in header_values]
    if not headers:
        raise RuntimeError("The payment transaction table has no column headers.")

    rows: list[list[str]] = []
    page_number = 1
    while True:
        current_rows = await _read_current_page(page, len(headers))
        rows.extend(current_rows)
        report_status(f"Collected {len(rows)} transaction row(s), page {page_number}...")

        next_page = page.locator(_NEXT_PAGE_SELECTOR)
        classes = (await next_page.get_attribute("class") or "").casefold()
        if "disabled" in classes:
            break

        previous_info = await _page_info(page)
        await next_page.locator("a").click()
        await _wait_for_page_change(page, previous_info)
        page_number += 1
    return headers, rows


async def _select_page_size(page: Page) -> None:
    selector = page.locator(_PAGE_SIZE_SELECTOR)
    await selector.wait_for(state="visible", timeout=30_000)
    if await selector.input_value() == "100":
        return
    previous_info = await _page_info(page)
    await selector.select_option("100")
    await _wait_for_page_change(page, previous_info)


async def _read_current_page(page: Page, header_count: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in await page.locator(f"{_TABLE_SELECTOR} tbody tr").all():
        cells = [_clean_cell(value) for value in await row.locator("td").all_inner_texts()]
        if len(cells) == header_count:
            rows.append(cells)
    return rows


async def _page_info(page: Page) -> str:
    return _clean_cell(await page.locator(_PAGE_INFO_SELECTOR).inner_text())


async def _wait_for_page_change(page: Page, previous_info: str) -> None:
    with suppress(PlaywrightTimeoutError):
        await page.wait_for_function(
            "previous => document.querySelector('#translist_info')?.textContent?.trim() !== previous",
            arg=previous_info,
            timeout=15_000,
        )
    await page.locator(f"{_TABLE_SELECTOR} tbody tr").first.wait_for(state="visible", timeout=30_000)


def append_unique_transactions(
    output_path: Path, headers: Sequence[str], rows: Sequence[Sequence[str]]
) -> int:
    """Append only rows whose Transaction ID is not already present in the CSV."""
    transaction_id_index = _transaction_id_index(headers)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids, existing_headers = _read_existing_transaction_ids(output_path, headers)
    if existing_headers is not None and list(headers) != existing_headers:
        raise RuntimeError("The selected CSV has different columns from the payment transaction table.")

    rows_to_append: list[list[str]] = []
    for row in rows:
        if len(row) != len(headers):
            continue
        transaction_id = row[transaction_id_index].strip()
        if not transaction_id or transaction_id in existing_ids:
            continue
        existing_ids.add(transaction_id)
        rows_to_append.append(list(row))

    if not rows_to_append:
        return 0
    is_new_file = not output_path.exists() or output_path.stat().st_size == 0
    with output_path.open("a", newline="", encoding="utf-8-sig" if is_new_file else "utf-8") as file:
        writer = csv.writer(file)
        if is_new_file:
            writer.writerow(headers)
        writer.writerows(rows_to_append)
    return len(rows_to_append)


def _read_existing_transaction_ids(
    output_path: Path, headers: Sequence[str]
) -> tuple[set[str], list[str] | None]:
    if not output_path.is_file() or output_path.stat().st_size == 0:
        return set(), None
    with output_path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file)
        existing_headers = next(reader, None)
        if existing_headers is None:
            return set(), None
        transaction_id_index = _transaction_id_index(existing_headers)
        return (
            {row[transaction_id_index].strip() for row in reader if len(row) > transaction_id_index},
            existing_headers,
        )


def _transaction_id_index(headers: Sequence[str]) -> int:
    for index, header in enumerate(headers):
        if _normalize_header(header) == _TRANSACTION_ID_HEADER:
            return index
    raise RuntimeError("The payment transaction table does not include a Transaction ID column.")


def _normalize_header(value: str) -> str:
    return " ".join(value.casefold().split())


def _clean_cell(value: str) -> str:
    return " ".join(value.split())
