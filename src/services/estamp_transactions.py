"""Collect eStamp payment transactions after a user completes Citizen login."""

from __future__ import annotations

import asyncio
import csv
import re
import ssl
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from automation.browser import PortalBrowserSession
from automation.portal import CITIZEN_LOGIN_URL, CITIZEN_WELCOME_URL, PortalAutomation
from core.config import DEFAULT_SMS_SERVER_URL
from core.controls import RunControls, WorkflowStopped
from core.models import CaptchaCopyMode, Credentials, PortalBrowser, Stage, UiEvent
from services.captcha_ocr import CaptchaSolver
from services.sms_otp_client import SmsOtpClient

if TYPE_CHECKING:
    from services.transaction_reconciliation import MissingPdf

TRANSACTIONS_URL = "https://jharnibandhan.gov.in/JHWebService/gras_payment_entry_estamp"
_TABLE_SELECTOR = "#translist"
_PAGE_INFO_SELECTOR = "#translist_info"
_NEXT_PAGE_SELECTOR = "#translist_next"
_PAGE_SIZE_SELECTOR = "select[name='translist_length']"
_TRANSACTION_ID_HEADER = "transaction id"
_LOGIN_TIMEOUT_MS = 30 * 60 * 1_000

_READ_ROWS_SCRIPT = """(args) => {
    const { expectedColCount, userId } = args;
    const table = document.querySelector('#translist');
    if (!table) return [];
    const rows = [];
    const trs = table.querySelectorAll('tbody tr');
    for (const tr of trs) {
        const tds = Array.from(tr.querySelectorAll('td'));
        if (tds.length !== expectedColCount) continue;
        const cells = tds.map(td => (td.textContent || '').trim().replace(/\\s+/g, ' '));
        
        let estampUrl = '';
        for (const a of tr.querySelectorAll('a')) {
            const href = a.getAttribute('href') || '';
            const text = (a.textContent || '').toLowerCase();
            if (href.includes('gras_estamp_download') || (text.includes('estamp') && !text.includes('payment_entry'))) {
                if (href) {
                    if (href.startsWith('http://') || href.startsWith('https://')) {
                        estampUrl = href;
                    } else {
                        const prefix = href.startsWith('/') ? '' : '/';
                        estampUrl = `https://jharnibandhan.gov.in${prefix}${href}`;
                    }
                    break;
                }
            }
        }
        cells.push(estampUrl);
        cells.push(userId || '');
        rows.push(cells);
    }
    return rows;
}"""


@dataclass(frozen=True)
class TransactionExportTarget:
    name: str
    browser: PortalBrowser
    profile_path: Path
    credentials: Credentials = field(default_factory=Credentials)
    sms_user_id: str = ""
    sms_server_url: str = ""
    solver: CaptchaSolver | None = None
    captcha_copy_mode: CaptchaCopyMode = CaptchaCopyMode.DIRECT
    window_accent: str = ""
    window_label: str = ""
    auto_login: bool = True
    payment_date_from: date | None = None
    payment_date_to: date | None = None


@dataclass(frozen=True)
class TransactionExportSummary:
    scraped_rows: int
    appended_rows: int
    skipped_duplicates: int
    output_path: Path
    target_name: str = ""
    error: str = ""


@dataclass(frozen=True)
class BatchTransactionExportSummary:
    scraped_rows: int
    appended_rows: int
    skipped_duplicates: int
    output_path: Path
    results: list[TransactionExportSummary] = field(default_factory=list)


@dataclass(frozen=True)
class MissingStampDownloadSummary:
    total: int
    downloaded: int
    failed: int
    skipped_existing: int
    output_directory: Path
    errors: list[str] = field(default_factory=list)


async def export_payment_transactions(
    browser: PortalBrowser,
    profile_path: Path,
    output_path: Path,
    report_status: Callable[[str], None],
    controls: RunControls | None = None,
) -> TransactionExportSummary:
    """Open Citizen login, wait for manual login, then export all transaction pages."""
    target = TransactionExportTarget(
        name="Citizen Portal",
        browser=browser,
        profile_path=profile_path,
        auto_login=False,
    )
    return await export_payment_transactions_for_target(
        target, output_path, report_status, controls=controls
    )


async def export_payment_transactions_for_target(
    target: TransactionExportTarget,
    output_path: Path,
    report_status: Callable[[str], None],
    controls: RunControls | None = None,
) -> TransactionExportSummary:
    """Open a browser for a target ID, sign in (automatically or manually), and export transactions."""
    controls = controls or RunControls(lambda _e: None)
    session = PortalBrowserSession(
        target.browser,
        lambda _session: None,
        target.profile_path,
        window_accent=target.window_accent,
        window_label=target.window_label,
    )
    user_id = target.credentials.citizen_username.strip() or target.name
    try:
        page = await session.new_portal_page(CITIZEN_LOGIN_URL)
        if (
            target.auto_login
            and target.credentials.citizen_username
            and target.credentials.citizen_password
        ):
            report_status(f"{target.name}: Logging into Citizen portal ({target.credentials.citizen_username})...")
            sms_client = SmsOtpClient(target.sms_server_url or DEFAULT_SMS_SERVER_URL)

            def emit_event(event: UiEvent) -> None:
                if event.message:
                    report_status(f"{target.name}: {event.message}")

            async def on_stage(_stage: Stage) -> None:
                pass

            portal = PortalAutomation(
                page=page,
                solver=target.solver,
                controls=controls,
                on_stage=on_stage,
                emit=emit_event,
                sms_otp_client=sms_client,
                sms_user_id=target.sms_user_id,
                captcha_copy_mode=target.captcha_copy_mode,
            )
            await portal.ensure_citizen_session(target.credentials)
        else:
            report_status(f"{target.name}: Complete Citizen login in the opened browser...")
            await _wait_for_manual_login(page, controls)

        report_status(f"{target.name}: Opening eStamp payment transactions...")
        await page.goto(TRANSACTIONS_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.locator(_TABLE_SELECTOR).wait_for(state="visible", timeout=120_000)
        headers, rows = await _collect_table_pages(
            page,
            lambda msg: report_status(f"{target.name}: {msg}"),
            user_id,
            controls,
            payment_date_from=target.payment_date_from,
            payment_date_to=target.payment_date_to,
        )
        appended_rows = append_unique_transactions(output_path, headers, rows)
        return TransactionExportSummary(
            scraped_rows=len(rows),
            appended_rows=appended_rows,
            skipped_duplicates=len(rows) - appended_rows,
            output_path=output_path,
            target_name=target.name,
        )
    finally:
        with suppress(Exception):
            await session.close()


async def _wait_for_manual_login(
    page: Page,
    controls: RunControls | None = None,
    timeout_ms: int = _LOGIN_TIMEOUT_MS,
) -> None:
    timeout_seconds = timeout_ms / 1000.0
    start = asyncio.get_running_loop().time()
    while True:
        if controls is not None:
            await controls.checkpoint()
        if page.is_closed():
            raise RuntimeError("The portal browser was closed before login completed.")
        if page.url.rstrip("/").casefold().startswith(CITIZEN_WELCOME_URL.casefold()):
            return
        if asyncio.get_running_loop().time() - start > timeout_seconds:
            raise TimeoutError("Citizen login timed out after 30 minutes.")
        await asyncio.sleep(0.5)


async def export_payment_transactions_batch(
    targets: Sequence[TransactionExportTarget],
    output_path: Path,
    report_status: Callable[[str], None],
    controls: RunControls | None = None,
    on_target_finished: Callable[[TransactionExportSummary], None] | None = None,
) -> BatchTransactionExportSummary:
    """Fetch transactions for each target ID sequentially, opening/closing the browser for each."""
    controls = controls or RunControls(lambda _e: None)
    results: list[TransactionExportSummary] = []
    total_scraped = 0
    total_appended = 0
    total_skipped = 0

    for index, target in enumerate(targets, start=1):
        await controls.checkpoint()
        prefix = f"[{index}/{len(targets)}] {target.name}"
        report_status(f"{prefix}: Starting transaction fetch...")
        try:
            summary = await export_payment_transactions_for_target(
                target, output_path, report_status, controls
            )
            results.append(summary)
            total_scraped += summary.scraped_rows
            total_appended += summary.appended_rows
            total_skipped += summary.skipped_duplicates
            report_status(
                f"{prefix}: Finished ({summary.scraped_rows} found, {summary.appended_rows} new)."
            )
            if on_target_finished is not None:
                with suppress(Exception):
                    on_target_finished(summary)
        except WorkflowStopped:
            raise
        except Exception as error:
            error_msg = str(error)
            results.append(
                TransactionExportSummary(
                    scraped_rows=0,
                    appended_rows=0,
                    skipped_duplicates=0,
                    output_path=output_path,
                    target_name=target.name,
                    error=error_msg,
                )
            )
            report_status(f"{prefix}: Error - {error_msg}")

        if index < len(targets):
            await asyncio.sleep(0.5)

    return BatchTransactionExportSummary(
        scraped_rows=total_scraped,
        appended_rows=total_appended,
        skipped_duplicates=total_skipped,
        output_path=output_path,
        results=results,
    )


async def _collect_table_pages(
    page: Page,
    report_status: Callable[[str], None],
    user_id: str = "",
    controls: RunControls | None = None,
    *,
    payment_date_from: date | None = None,
    payment_date_to: date | None = None,
) -> tuple[list[str], list[list[str]]]:
    if controls is not None:
        await controls.checkpoint()
    await _select_page_size(page)
    header_values = await page.locator(f"{_TABLE_SELECTOR} thead th").all_inner_texts()
    raw_headers = [_clean_cell(value) for value in header_values]
    if not raw_headers:
        raise RuntimeError("The payment transaction table has no column headers.")

    headers = [*raw_headers, "eStamp Download URL", "User ID"]

    rows: list[list[str]] = []
    page_number = 1
    while True:
        if controls is not None:
            await controls.checkpoint()
        current_rows = await _read_current_page(page, len(raw_headers), user_id)
        matching_rows = _successful_rows_in_payment_date_range(
            current_rows,
            raw_headers,
            payment_date_from=payment_date_from,
            payment_date_to=payment_date_to,
        )
        rows.extend(matching_rows)
        report_status(
            f"Collected {len(rows)} successful transaction(s), page {page_number} "
            f"({len(matching_rows)} matched on this page)..."
        )

        next_page = page.locator(_NEXT_PAGE_SELECTOR)
        classes = (await next_page.get_attribute("class") or "").casefold()
        if "disabled" in classes:
            break

        previous_info = await _page_info(page)
        await next_page.locator("a").click()
        await _wait_for_page_change(page, previous_info)
        page_number += 1
    return headers, rows


def _successful_rows_in_payment_date_range(
    rows: Sequence[Sequence[str]],
    headers: Sequence[str],
    *,
    payment_date_from: date | None,
    payment_date_to: date | None,
) -> list[list[str]]:
    """Keep only successful payments inside the optional inclusive date range."""
    status_index = _header_index(headers, "status")
    payment_date_index = _header_index(headers, "payment date")
    selected: list[list[str]] = []
    for row in rows:
        if len(row) <= max(status_index, payment_date_index):
            continue
        if _clean_cell(row[status_index]).casefold() != "success":
            continue
        payment_date = _parse_payment_date(row[payment_date_index])
        if payment_date is None:
            continue
        if payment_date_from is not None and payment_date < payment_date_from:
            continue
        if payment_date_to is not None and payment_date > payment_date_to:
            continue
        selected.append(list(row))
    return selected


def _header_index(headers: Sequence[str], expected_header: str) -> int:
    expected = _normalize_header(expected_header)
    for index, header in enumerate(headers):
        if _normalize_header(header) == expected:
            return index
    raise RuntimeError(
        f"The payment transaction table does not include a {expected_header.title()} column."
    )


def _parse_payment_date(value: str) -> date | None:
    cleaned = _clean_cell(value)
    if not cleaned:
        return None
    candidates = [cleaned, cleaned.split(" ", 1)[0]]
    for candidate in candidates:
        with suppress(ValueError):
            return date.fromisoformat(candidate)
        for date_format in (
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%d.%m.%Y",
            "%d-%b-%Y",
            "%d %b %Y",
        ):
            with suppress(ValueError):
                return datetime.strptime(candidate, date_format).date()
    return None


async def _select_page_size(page: Page) -> None:
    selector = page.locator(_PAGE_SIZE_SELECTOR)
    await selector.wait_for(state="visible", timeout=30_000)
    if await selector.input_value() == "100":
        return
    previous_info = await _page_info(page)
    await selector.select_option("100")
    await _wait_for_page_change(page, previous_info)


async def _read_current_page(page: Page, header_count: int, user_id: str) -> list[list[str]]:
    return await page.evaluate(
        _READ_ROWS_SCRIPT,
        {"expectedColCount": header_count, "userId": user_id},
    )


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
    existing_ids, existing_headers, existing_rows = _read_existing_transaction_ids_and_rows(output_path)
    if existing_headers is not None and list(headers) != existing_headers:
        if not _can_upgrade_headers(existing_headers, list(headers)):
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
    if is_new_file:
        with output_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(rows_to_append)
    else:
        if existing_headers == list(headers):
            with output_path.open("a", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerows(rows_to_append)
        elif existing_headers is not None:
            # Re-align existing rows with new headers to migrate seamlessly
            aligned_rows = _align_existing_rows_to_new_headers(
                existing_headers, existing_rows, list(headers)
            )
            aligned_rows.extend(rows_to_append)
            with output_path.open("w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(headers)
                writer.writerows(aligned_rows)
    return len(rows_to_append)


def _can_upgrade_headers(old_headers: list[str], new_headers: list[str]) -> bool:
    old_norm = [_normalize_header(h) for h in old_headers]
    new_norm = [_normalize_header(h) for h in new_headers]
    filtered_old = [h for h in old_norm if h not in ("estamp download url", "user id", "citizen id")]
    filtered_new = [h for h in new_norm if h not in ("estamp download url", "user id", "citizen id")]
    return bool(filtered_old and filtered_old == filtered_new)


def _read_existing_transaction_ids_and_rows(
    output_path: Path,
) -> tuple[set[str], list[str] | None, list[list[str]]]:
    if not output_path.is_file() or output_path.stat().st_size == 0:
        return set(), None, []
    with output_path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file)
        existing_headers = next(reader, None)
        if existing_headers is None:
            return set(), None, []
        try:
            transaction_id_index = _transaction_id_index(existing_headers)
        except RuntimeError:
            return set(), existing_headers, []
        existing_rows: list[list[str]] = []
        existing_ids: set[str] = set()
        for row in reader:
            existing_rows.append(row)
            if len(row) > transaction_id_index:
                tid = row[transaction_id_index].strip()
                if tid:
                    existing_ids.add(tid)
        return existing_ids, existing_headers, existing_rows


def _align_existing_rows_to_new_headers(
    old_headers: list[str], old_rows: list[list[str]], new_headers: list[str]
) -> list[list[str]]:
    header_map = {_normalize_header(h): i for i, h in enumerate(old_headers)}
    aligned: list[list[str]] = []
    for row in old_rows:
        new_row: list[str] = []
        for h in new_headers:
            idx = header_map.get(_normalize_header(h))
            if idx is not None and idx < len(row):
                new_row.append(row[idx])
            else:
                new_row.append("")
        aligned.append(new_row)
    return aligned


def _transaction_id_index(headers: Sequence[str]) -> int:
    for index, header in enumerate(headers):
        if _normalize_header(header) == _TRANSACTION_ID_HEADER:
            return index
    raise RuntimeError("The payment transaction table does not include a Transaction ID column.")


def _normalize_header(value: str) -> str:
    return " ".join(value.casefold().split())


def _clean_cell(value: str) -> str:
    return " ".join(value.split())


def download_missing_stamps(
    missing_items: Sequence[MissingPdf],
    output_directory: Path,
    report_status: Callable[[str], None],
    controls: RunControls | None = None,
) -> MissingStampDownloadSummary:
    """Download missing eStamp PDFs using their download URLs."""
    output_directory.mkdir(parents=True, exist_ok=True)
    total = len(missing_items)
    downloaded = 0
    failed = 0
    skipped_existing = 0
    errors: list[str] = []

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    for index, item in enumerate(missing_items, start=1):
        if controls is not None and controls.stop_event.is_set():
            report_status("Download stopped by user.")
            break

        tx_id = item.transaction_id.strip()
        if _is_stamp_already_downloaded(output_directory, tx_id):
            skipped_existing += 1
            report_status(f"[{index}/{total}] Already exists (skipped): {tx_id}")
            continue

        dest_file = output_directory / f"eStamp_{tx_id}.pdf"
        raw_url = item.estamp_url.strip() or f"https://jharnibandhan.gov.in/JHWebService/gras_estamp_download/{tx_id}"
        if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
            raw_url = f"https://jharnibandhan.gov.in/{raw_url.lstrip('/')}"

        report_status(f"[{index}/{total}] Downloading eStamp for {tx_id}...")
        try:
            req = urllib.request.Request(
                raw_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                    "Referer": "https://jharnibandhan.gov.in/JHWebService/gras_payment_entry_estamp",
                },
            )
            with urllib.request.urlopen(req, context=ssl_context, timeout=45) as response:
                content = response.read()
                content_type = response.headers.get_content_type()
                if len(content) >= 500 and (content.startswith(b"%PDF") or "pdf" in content_type.lower()):
                    temp_file = dest_file.with_suffix(".pdf.part")
                    temp_file.write_bytes(content)
                    temp_file.replace(dest_file)
                    downloaded += 1
                    report_status(f"[{index}/{total}] Successfully downloaded: {dest_file.name}")
                else:
                    failed += 1
                    snippet = content[:80].decode("utf-8", errors="ignore").strip()
                    err_msg = f"{tx_id}: Response is not a valid PDF ({snippet or f'size={len(content)} bytes'})"
                    errors.append(err_msg)
                    report_status(f"[{index}/{total}] Failed: {err_msg}")
        except Exception as exc:
            failed += 1
            err_msg = f"{tx_id}: {exc}"
            errors.append(err_msg)
            report_status(f"[{index}/{total}] Error: {err_msg}")

    return MissingStampDownloadSummary(
        total=total,
        downloaded=downloaded,
        failed=failed,
        skipped_existing=skipped_existing,
        output_directory=output_directory,
        errors=errors,
    )


def _is_stamp_already_downloaded(output_directory: Path, tx_id: str) -> bool:
    if not output_directory.is_dir() or not tx_id:
        return False
    direct = output_directory / f"eStamp_{tx_id}.pdf"
    if direct.is_file() and direct.stat().st_size >= 500:
        return True
    tx_key = tx_id.casefold()
    for path in output_directory.rglob("*.pdf"):
        if tx_key in path.stem.casefold() and path.stat().st_size >= 500:
            return True
    return False
