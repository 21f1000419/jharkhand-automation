"""Export successful JEGRAS transactions from the registered-user finance portal."""

from __future__ import annotations

import asyncio
import csv
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TypedDict, cast
from urllib.parse import urlparse

from playwright.async_api import Page

from automation.browser import PortalBrowserSession
from core.controls import RunControls
from core.models import Credentials, PortalBrowser, WorkflowStopped
from services.captcha_ocr import CaptchaSolver, normalize_captcha
from services.estamp_transactions import (
    BatchTransactionExportSummary,
    TransactionExportSummary,
    parse_payment_amount,
)
from services.sms_otp_client import SmsOtpClient, SmsOtpServerError

FINANCE_LOGIN_URL = "https://finance.jharkhand.gov.in/jegras/Login.aspx"
FINANCE_HISTORY_URL = "https://finance.jharkhand.gov.in/jegras/frmTransactionHistory.aspx"
_PREFIX = "#ContentPlaceHolder1_"
_TABLE = f"{_PREFIX}grdtranshistory"
FINANCE_HEADERS = [
    "Sl. No.",
    "GRN NO",
    "REMITTER NAME",
    "ENTRY DATE",
    "TRANSACTION DATE",
    "CHEQUE/DD NO",
    "CIN NO",
    "BANK REFNO",
    "PAYMENT BY",
    "AMOUNT",
    "STATUS",
    "Extra Detail Link",
    "ACTION",
]
_REFERENCE_PATTERN = re.compile(
    r"\breference\s*(?:number|no\.?)?\s*(?:is\s*)?[:\-]?\s*([A-Z0-9-]+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FinanceExportFilters:
    name: str = ""
    date_from: date | None = None
    date_to: date | None = None
    amount: Decimal | None = None

    def validate(self) -> None:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Start date must be on or before end date.")
        if self.amount is not None and (not self.amount.is_finite() or self.amount < 0):
            raise ValueError("Amount must be a finite, non-negative number.")


@dataclass(frozen=True)
class FinanceExportTarget:
    name: str
    browser: PortalBrowser
    profile_path: Path
    credentials: Credentials
    sms_server_url: str = ""
    solver: CaptchaSolver | None = None


class _PagerLink(TypedDict):
    number: int
    href: str


class _HistorySnapshot(TypedDict):
    headers: list[str]
    rows: list[list[str]]
    number: int
    links: list[_PagerLink]


_READ_HISTORY = """() => {
    const table = document.querySelector('#ContentPlaceHolder1_grdtranshistory');
    if (!table) return null;
    const clean = el => (el.textContent || '').replace(/\\s+/g, ' ').trim();
    const headers = Array.from(table.rows[0]?.cells || []).map(clean);
    const rows = Array.from(table.rows)
        .filter(tr => !tr.querySelector('th') && !tr.classList.contains('GridPager')
            && tr.cells.length === headers.length)
        .map(tr => Array.from(tr.cells).map(td => {
            const text = clean(td);
            const urls = Array.from(td.querySelectorAll('a[href]'))
                .filter(a => !a.getAttribute('href').startsWith('javascript:'))
                .map(a => a.href);
            return urls.length ? [text, ...urls].filter(Boolean).join(' | ') : text;
        }));
    const pager = table.querySelector('tr.GridPager');
    const current = Array.from(pager?.querySelectorAll('span') || [])
        .map(clean).find(text => /^\\d+$/.test(text));
    const links = Array.from(pager?.querySelectorAll('a[href]') || []).flatMap(a => {
        const href = a.getAttribute('href');
        const match = href.match(/['"]Page\\$(\\d+)['"]/);
        return match ? [{number: Number(match[1]), href}] : [];
    });
    return {headers, rows, number: current ? Number(current) : 1, links};
}"""


def extract_finance_otp_reference(message: str) -> str:
    match = _REFERENCE_PATTERN.search(message)
    return match.group(1).upper() if match else ""


def unique_egras_targets(targets: Sequence[FinanceExportTarget]) -> list[FinanceExportTarget]:
    """Prefer complete credentials when several IDs share the same eGRAS username."""
    unique: dict[str, FinanceExportTarget] = {}
    for target in targets:
        key = target.credentials.egras_username.strip().lower()
        if key and (key not in unique or not unique[key].credentials.egras_password):
            unique[key] = target
    return list(unique.values())


async def _login(
    page: Page, target: FinanceExportTarget, controls: RunControls, report: Callable[[str], None]
) -> None:
    await controls.checkpoint()
    if await _is_authenticated(page):
        return
    await page.locator(f"{_PREFIX}txtLoginId").wait_for(state="visible", timeout=30_000)
    await page.locator(f"{_PREFIX}txtLoginId").fill(target.credentials.egras_username.strip())
    await page.locator(f"{_PREFIX}txtPassword").fill(target.credentials.egras_password)
    report("Credentials filled. Enter the CAPTCHA and click Login in the browser.")
    if target.solver is not None and target.credentials.egras_password:
        try:
            await target.solver.verify_ready()
            image = await page.locator("img.imgcaptcha").screenshot()
            captcha = normalize_captcha(await target.solver.solve_image(image)).upper()
            await controls.checkpoint()
            if captcha:
                await page.locator(f"{_PREFIX}txtcaptcha").fill(captcha)
                # Click the live control so its current CodePass nonce and WebForms state are used.
                await page.locator(f"{_PREFIX}btnSubmit").click()
        except WorkflowStopped:
            raise
        except Exception:
            report("CAPTCHA OCR could not complete login. Enter the CAPTCHA and click Login.")

    client = SmsOtpClient(target.sms_server_url)
    deadline = asyncio.get_running_loop().time() + 30 * 60
    announced_reference: str | None = None
    last_message = ""
    next_poll = 0.0
    attempted: set[tuple[str, str]] = set()
    used_otp: tuple[str, str] | None = None
    while asyncio.get_running_loop().time() < deadline:
        await controls.checkpoint()
        if page.is_closed():
            raise RuntimeError("The finance browser was closed before login completed.")
        if await _is_authenticated(page):
            if used_otp:
                with suppress(SmsOtpServerError):
                    await client.delete_egrass_otp_after_use(*used_otp)
            return
        if await page.locator(f"{_PREFIX}txtOTP").is_visible():
            message = (await page.locator(f"{_PREFIX}lblMsgs").text_content()) or ""
            reference = extract_finance_otp_reference(message)
            if reference != announced_reference:
                announced_reference = reference
                next_poll = 0.0
                report(message.strip() or "Enter the six-character OTP and click Verify OTP.")
                report("Checking the SMS server. You can also enter the OTP and click Verify OTP.")
            now = asyncio.get_running_loop().time()
            # A user's manual OTP always takes priority over the SMS lookup.
            if client.is_configured and reference and now >= next_poll:
                next_poll = now + 3
                field = page.locator(f"{_PREFIX}txtOTP")
                if not (await field.input_value()).strip():
                    try:
                        otp = await client.get_egrass_otp(reference)
                    except SmsOtpServerError:
                        report("SMS server unavailable. Enter the OTP and click Verify OTP.")
                        next_poll = now + 30
                    else:
                        if otp and re.fullmatch(r"[A-Za-z0-9]{6}", otp):
                            key = (reference, otp.upper())
                            # The user may have typed or resent an OTP during the network request.
                            if (
                                key not in attempted
                                and await field.is_visible()
                                and not (await field.input_value()).strip()
                            ):
                                latest = (await page.locator(f"{_PREFIX}lblMsgs").text_content()) or ""
                                if extract_finance_otp_reference(latest) == reference:
                                    await controls.checkpoint()
                                    attempted.add(key)
                                    await field.fill(otp.upper())
                                    used_otp = key
                                    await page.locator(f"{_PREFIX}btnOTPVerify").click()
        messages = []
        for suffix in ("lblMsg", "lblMsg_error"):
            label = page.locator(f"{_PREFIX}{suffix}")
            if await label.is_visible():
                messages.append((await label.text_content()) or "")
        error_message = " ".join(messages).strip()
        if error_message and error_message != last_message:
            last_message = error_message
            used_otp = None
            report(f"{error_message} Complete login in the browser, or resend the OTP if it expired.")
        await asyncio.sleep(0.5)
    raise TimeoutError("Finance login timed out after 30 minutes.")


async def _is_authenticated(page: Page) -> bool:
    url = urlparse(page.url)
    if url.hostname != "finance.jharkhand.gov.in" or not url.path.lower().startswith("/jegras/"):
        return False
    if await page.locator(f"{_PREFIX}txtLoginId").is_visible():
        return False
    if await page.locator(f"{_PREFIX}txtOTP").is_visible():
        return False
    return (
        not url.path.lower().endswith("/login.aspx")
        or await page.locator("a[href*='frmTransactionHistory.aspx']").is_visible()
    )


def filter_finance_rows(
    rows: Sequence[Sequence[str]], filters: FinanceExportFilters
) -> tuple[list[list[str]], bool]:
    """Use inclusive entry dates and stop at the first older row, before other filters."""
    selected: list[list[str]] = []
    name = filters.name.strip().lower()
    for row in rows:
        if len(row) != len(FINANCE_HEADERS):
            raise RuntimeError("The finance transaction table has an unexpected row layout.")
        entry_date: date | None = None
        if filters.date_from or filters.date_to:
            try:
                entry_date = datetime.strptime(row[3].split()[0], "%d-%b-%Y").date()
            except (ValueError, IndexError) as error:
                raise RuntimeError(f"Cannot read the entry date for GRN {row[1]}: {row[3]}") from error
        if filters.date_from and entry_date and entry_date < filters.date_from:
            return selected, True
        if filters.date_to and entry_date and entry_date > filters.date_to:
            continue
        if row[10].strip().upper() != "SUCCESS":
            continue
        if name and name not in row[2].lower():
            continue
        if filters.amount is not None and parse_payment_amount(row[9]) != filters.amount:
            continue
        selected.append(list(row))
    return selected, False


async def collect_finance_pages(
    page: Page, filters: FinanceExportFilters, controls: RunControls, report: Callable[[str], None]
) -> list[list[str]]:
    filters.validate()
    rows: list[list[str]] = []
    seen_pages: set[int] = set()
    seen_grns: set[str] = set()
    while True:
        await controls.checkpoint()
        snapshot = cast(_HistorySnapshot | None, await page.evaluate(_READ_HISTORY))
        if snapshot is None:
            raise RuntimeError("The finance transaction table disappeared. The session may have expired.")
        headers = [" ".join(value.lower().split()) for value in snapshot["headers"]]
        expected = [" ".join(value.lower().split()) for value in FINANCE_HEADERS]
        if headers != expected:
            raise RuntimeError("The finance transaction table has unexpected columns.")
        number = snapshot["number"]
        if number in seen_pages:
            raise RuntimeError(f"Pagination did not advance beyond finance page {number}.")
        seen_pages.add(number)
        matching, reached_start = filter_finance_rows(snapshot["rows"], filters)
        for row in matching:
            grn = row[1].strip()
            if grn and grn not in seen_grns:
                seen_grns.add(grn)
                rows.append(row)
        report(f"Page {number}: {len(rows)} matching successful transaction(s).")
        if reached_start:
            report("Reached a transaction older than the start date.")
            break
        next_links = [link for link in snapshot["links"] if link["number"] > number]
        if not next_links:
            break
        next_link = min(next_links, key=lambda link: link["number"])
        await controls.checkpoint()
        # WebForms replaces the document. Resolve every pager link from the current page,
        # including the '...' link that opens the next block of ten pages.
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000):
            await page.locator(f"{_TABLE} tr.GridPager a[href={_css_string(next_link['href'])}]").click()
        await page.locator(_TABLE).wait_for(state="visible", timeout=30_000)
    return rows


def _css_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


async def export_finance_transactions_batch(
    targets: Sequence[FinanceExportTarget],
    output_path: Path,
    filters: FinanceExportFilters,
    report_status: Callable[[str], None],
    controls: RunControls,
) -> BatchTransactionExportSummary:
    filters.validate()
    unique_targets = unique_egras_targets(targets)
    if not unique_targets:
        raise ValueError("Select at least one configured eGRAS account.")
    results: list[TransactionExportSummary] = []
    all_rows: list[list[str]] = []
    seen_grns: set[str] = set()
    for target in unique_targets:
        await controls.checkpoint()

        def report(message: str, name: str = target.name) -> None:
            report_status(f"{name}: {message}")

        session = PortalBrowserSession(target.browser, lambda _session: None, target.profile_path)
        try:
            report("Opening finance registered-user login...")
            page = await session.new_portal_page(FINANCE_LOGIN_URL)
            await _login(page, target, controls, report)
            await controls.checkpoint()
            await page.goto(FINANCE_HISTORY_URL, wait_until="domcontentloaded", timeout=60_000)
            await page.locator(f"{_PREFIX}ddlsearchoption").select_option("SUCCESS")
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000):
                await page.locator(f"{_PREFIX}btnSearch").click()
            if not await page.locator(_TABLE).count():
                body = (await page.locator("body").inner_text()).lower()
                if re.search(r"\bno\s+(?:records?|transactions?|data)\b", body):
                    account_rows: list[list[str]] = []
                else:
                    raise RuntimeError("Search did not return the finance transaction table.")
            else:
                account_rows = await collect_finance_pages(page, filters, controls, report)
            added = 0
            for row in account_rows:
                if row[1] not in seen_grns:
                    seen_grns.add(row[1])
                    all_rows.append([*row, target.credentials.egras_username.strip()])
                    added += 1
            results.append(
                TransactionExportSummary(
                    len(account_rows), added, len(account_rows) - added, output_path, target_name=target.name
                )
            )
        except WorkflowStopped:
            raise
        except Exception as error:
            report(f"Export failed: {error}")
            results.append(TransactionExportSummary(0, 0, 0, output_path, target.name, str(error)))
        finally:
            with suppress(Exception):
                await session.close()
    await controls.checkpoint()
    if all(result.error for result in results):
        raise RuntimeError(
            "No accounts could be exported.\n"
            + "\n".join(f"{result.target_name}: {result.error}" for result in results)
        )
    _write_finance_csv(output_path, all_rows)
    return BatchTransactionExportSummary(
        sum(result.scraped_rows for result in results),
        len(all_rows),
        sum(result.skipped_duplicates for result in results),
        output_path,
        results,
    )


def _write_finance_csv(output_path: Path, rows: Sequence[Sequence[str]]) -> None:
    """Replace the chosen file only after a complete CSV has been written."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8-sig",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            writer = csv.writer(file)
            writer.writerow([*FINANCE_HEADERS, "eGRAS Username"])
            writer.writerows(rows)
        os.replace(temporary, output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
