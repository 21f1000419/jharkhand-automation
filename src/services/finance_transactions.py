"""Export successful JEGRAS transactions from the registered-user finance portal."""

from __future__ import annotations

import asyncio
import csv
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from contextlib import nullcontext, suppress
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TypedDict, cast
from urllib.parse import urlparse

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from automation.browser import PortalBrowserSession
from automation.portal import EGRASS_OTP_PAGE_TIMEOUT_SECONDS, PortalAutomation
from core.controls import RunControls
from core.models import CaptchaCopyMode, Credentials, PortalBrowser, Stage, UiEvent, WorkflowStopped
from services.captcha_ocr import CaptchaSolver
from services.estamp_transactions import parse_payment_amount
from services.finance_checkpoint import (
    FINANCE_HEADERS,
    FinanceCheckpoint,
    FinanceCheckpointInfo,
    FinancePageUpdate,
)
from services.sms_otp_client import SmsOtpClient, SmsOtpServerError

FINANCE_LOGIN_URL = "https://finance.jharkhand.gov.in/jegras/Login.aspx"
FINANCE_HISTORY_URL = "https://finance.jharkhand.gov.in/jegras/frmTransactionHistory.aspx"
_PREFIX = "#ContentPlaceHolder1_"
_TABLE = f"{_PREFIX}grdtranshistory"
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
    captcha_copy_mode: CaptchaCopyMode = CaptchaCopyMode.DIRECT
    save_captcha_images: bool = False


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
        const label = clean(a);
        const number = match ? Number(match[1]) : /^\\d+$/.test(label) ? Number(label) : null;
        return number !== null ? [{number, href}] : [];
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
    page: Page,
    target: FinanceExportTarget,
    controls: RunControls,
    report: Callable[[str], None],
    captcha_lock: asyncio.Lock | None = None,
) -> None:
    await controls.checkpoint()
    if await _is_authenticated(page):
        return
    await page.locator(f"{_PREFIX}txtLoginId").wait_for(state="visible", timeout=30_000)
    await page.locator(f"{_PREFIX}txtLoginId").fill(target.credentials.egras_username.strip())
    await page.locator(f"{_PREFIX}txtPassword").fill(target.credentials.egras_password)
    client = SmsOtpClient(target.sms_server_url)
    await _complete_finance_captcha_login(page, target, client, controls, report, captcha_lock)
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


async def _complete_finance_captcha_login(
    page: Page,
    target: FinanceExportTarget,
    client: SmsOtpClient,
    controls: RunControls,
    report: Callable[[str], None],
    captcha_lock: asyncio.Lock | None = None,
) -> None:
    if not target.credentials.egras_username or not target.credentials.egras_password:
        report("Complete the credentials and CAPTCHA, then click Login in the browser.")
        return

    async def on_stage(_stage: Stage) -> None:
        pass

    def emit(event: UiEvent) -> None:
        if event.message:
            report(event.message)

    portal = PortalAutomation(
        page=page,
        solver=target.solver,
        controls=controls,
        on_stage=on_stage,
        emit=emit,
        sms_otp_client=client,
        sms_user_id="",
        captcha_copy_mode=target.captcha_copy_mode,
        save_captcha_images=target.save_captcha_images,
    )
    portal.stage = Stage.EGRAS_LOGIN
    attempt = 0
    while True:
        await controls.checkpoint()
        if await page.locator(f"{_PREFIX}txtOTP").is_visible() or await _is_authenticated(page):
            return
        attempt += 1
        report(f"Finance login attempt {attempt}: Refilling credentials and reading the CAPTCHA.")
        try:
            # CodePass transforms the password on submission, so every retry needs the original value.
            await page.locator(f"{_PREFIX}txtLoginId").fill(target.credentials.egras_username.strip())
            await page.locator(f"{_PREFIX}txtPassword").fill(target.credentials.egras_password)
            # Serialize only CAPTCHA capture/OCR, including clipboard mode and cancellation.
            # Manual-only logins and the rest of each account's fetch stay independent.
            async with captcha_lock if captcha_lock is not None and target.solver else nullcontext():
                await controls.checkpoint()
                manual = await portal._solve_captcha(
                    "img.imgcaptcha",
                    f"{_PREFIX}txtcaptcha",
                    expected_length=6,
                    refresh_selector=f"{_PREFIX}ImageButton1",
                )
            if await page.locator(f"{_PREFIX}txtOTP").is_visible() or await _is_authenticated(page):
                return
            field = page.locator(f"{_PREFIX}txtcaptcha")
            submitted_captcha = (await field.input_value()).strip()
            if not manual:
                report(f"Finance login attempt {attempt}: Clicking Login with the OCR CAPTCHA.")
                # Run the live control's CodePass and WebForms postback, including its fresh nonce.
                await page.locator(f"{_PREFIX}btnSubmit").click()
                outcome = await asyncio.wait_for(
                    _wait_finance_login_progress(page, controls), timeout=EGRASS_OTP_PAGE_TIMEOUT_SECONDS
                )
            else:
                report("Manual CAPTCHA entered. Click Login in the browser.")
                outcome = await _wait_finance_login_progress(page, controls)
            if outcome in ("otp", "success"):
                report(f"Finance login attempt {attempt}: CAPTCHA login accepted.")
                return
            if outcome == "login_error":
                report(
                    "The portal reported a login error. Check the credentials and complete login manually."
                )
                return
            report(
                f"Finance login attempt {attempt}: {outcome}. Retrying with fresh credentials and CAPTCHA."
            )
            # Keep a new manual value if the user has already replaced the rejected CAPTCHA.
            current_captcha = (await field.input_value()).strip()
            if current_captcha and current_captcha != submitted_captcha:
                report("New manual CAPTCHA detected. Click Login in the browser.")
                return
            await field.fill("")
            await portal._refresh_captcha(page.locator("img.imgcaptcha"), f"{_PREFIX}ImageButton1")
        except WorkflowStopped:
            raise
        except Exception as error:
            if page.is_closed():
                raise RuntimeError("The finance browser was closed during CAPTCHA login.") from error
            if await page.locator(f"{_PREFIX}txtOTP").is_visible() or await _is_authenticated(page):
                return
            report(f"CAPTCHA login could not continue automatically: {error}. Complete login in the browser.")
            return


async def _wait_finance_login_progress(page: Page, controls: RunControls) -> str:
    """Use the original eGRAS password-reset indicator with the registered-user selectors."""
    while True:
        await controls.checkpoint()
        if page.is_closed():
            raise RuntimeError("The finance browser was closed during login.")
        if await page.locator(f"{_PREFIX}txtOTP").is_visible():
            return "otp"
        if await _is_authenticated(page):
            return "success"
        label = page.locator(f"{_PREFIX}lblMsg")
        message = ((await label.text_content()) or "").strip() if await label.is_visible() else ""
        if message:
            return "captcha_failed" if "captcha" in message.lower() else "login_error"
        password = page.locator(f"{_PREFIX}txtPassword")
        if await password.is_visible() and not (await password.input_value()).strip():
            return "form_reset"
        await asyncio.sleep(0.25)


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
    page: Page,
    filters: FinanceExportFilters,
    controls: RunControls,
    report: Callable[[str], None],
    on_page: Callable[[int, list[list[str]]], None] | None = None,
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
        report(
            f"History snapshot: page={number}, rows={len(snapshot['rows'])}, "
            f"pager_targets={[link['number'] for link in snapshot['links']]}, "
            f"first_entry_date={snapshot['rows'][0][3] if snapshot['rows'] else 'none'}, "
            f"last_entry_date={snapshot['rows'][-1][3] if snapshot['rows'] else 'none'}."
        )
        matching, reached_start = filter_finance_rows(snapshot["rows"], filters)
        page_rows: list[list[str]] = []
        for row in matching:
            grn = row[1].strip()
            if grn and grn not in seen_grns:
                seen_grns.add(grn)
                rows.append(row)
                page_rows.append(row)
        if on_page is not None:
            on_page(number, page_rows)
        report(f"Page {number}: {len(rows)} matching successful transaction(s).")
        if reached_start:
            report("Reached a transaction older than the start date.")
            break
        next_links = [link for link in snapshot["links"] if link["number"] > number]
        if not next_links:
            report(f"Page {number}: No later page link found. Finishing collection.")
            break
        next_link = min(next_links, key=lambda link: link["number"])
        await controls.checkpoint()
        report(f"Moving from page {number} to page {next_link['number']}...")
        await _advance_finance_page(page, next_link, controls, report)
    return rows


async def _advance_finance_page(
    page: Page, link: _PagerLink, controls: RunControls, report: Callable[[str], None] | None = None
) -> None:
    report = report or (lambda _message: None)
    # Only the immediate parent cell matches, excluding the outer colspan pager cell.
    cell = page.locator(f"{_TABLE} tr.GridPager td:has(> a[href={_css_string(link['href'])}])")
    cell_count = await cell.count()
    report(f"Page {link['number']}: Found {cell_count} pager cell(s) matching href={link['href']!r}.")
    if cell_count > 1:
        # Some WebForms pagers use a shared href and put their target in onclick.
        cell = cell.filter(has_text=re.compile(rf"^\s*{link['number']}\s*$"))
        report(f"Page {link['number']}: After matching the page label, {await cell.count()} cell(s) remain.")
    report(f"Page {link['number']}: Clicking the pager cell...")
    await cell.click(timeout=30_000)
    report(f"Page {link['number']}: Cell click returned. Waiting for the selected-page marker.")
    deadline = asyncio.get_running_loop().time() + 60
    next_report = asyncio.get_running_loop().time() + 10
    condition = f"number => {{ const snapshot = ({_READ_HISTORY})(); return snapshot?.number === number; }}"
    while asyncio.get_running_loop().time() < deadline:
        await controls.checkpoint()
        if page.is_closed():
            raise RuntimeError("The finance browser was closed during pagination.")
        try:
            # Works for full postbacks and UpdatePanel changes. Short waits keep Stop responsive.
            await page.wait_for_function(condition, arg=link["number"], polling=250, timeout=1_000)
            report(f"Page {link['number']}: Selected-page marker confirmed.")
            return
        except PlaywrightTimeoutError:
            now = asyncio.get_running_loop().time()
            if now >= next_report:
                snapshot = cast(_HistorySnapshot | None, await page.evaluate(_READ_HISTORY))
                report(
                    f"Still waiting for page {link['number']}: "
                    f"current_page={snapshot['number'] if snapshot else 'no history table'}, "
                    f"path={urlparse(page.url).path}."
                )
                next_report = now + 10
            continue
    raise RuntimeError(
        f"Pagination did not advance to finance page {link['number']} after clicking its cell."
    )


def _css_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


async def fetch_finance_transactions_batch(
    targets: Sequence[FinanceExportTarget],
    checkpoint: FinanceCheckpoint,
    filters: FinanceExportFilters,
    report_status: Callable[[str], None],
    controls: RunControls,
    on_page: Callable[[FinancePageUpdate], None],
) -> FinanceCheckpointInfo:
    """Fetch accounts concurrently; each completed page is durable before moving on."""
    filters.validate()
    unique_targets = unique_egras_targets(targets)
    if not unique_targets:
        raise ValueError("Select at least one configured eGRAS account.")
    await controls.checkpoint()
    captcha_lock = asyncio.Lock()

    async def fetch_account(target: FinanceExportTarget) -> None:
        account = target.credentials.egras_username.strip()

        def report(message: str, name: str = target.name) -> None:
            report_status(f"{name}: {message}")

        def save_page(number: int, rows: list[list[str]]) -> None:
            update = checkpoint.save_page(account, number, rows)
            on_page(update)
            report(
                f"Page {number}: Checkpoint saved, {len(update.rows)} new row(s), "
                f"{update.total_rows} total unique transactions."
            )

        session: PortalBrowserSession | None = None
        try:
            await controls.checkpoint()
            checkpoint.set_account_status(account, "running")
            session = PortalBrowserSession(target.browser, lambda _session: None, target.profile_path)
            report("Opening finance registered-user login...")
            page = await session.new_portal_page(FINANCE_LOGIN_URL)
            await _login(page, target, controls, report, captcha_lock)
            report("Finance login completed. Opening transaction history...")
            await controls.checkpoint()
            await page.goto(FINANCE_HISTORY_URL, wait_until="domcontentloaded", timeout=60_000)
            await page.locator(f"{_PREFIX}ddlsearchoption").select_option("SUCCESS")
            report("Success selected. Clicking Search and waiting for the transaction table...")
            await page.locator(f"{_PREFIX}btnSearch").click(no_wait_after=True)
            try:
                await page.locator(_TABLE).wait_for(state="visible", timeout=60_000)
            except PlaywrightTimeoutError as error:
                body = (await page.locator("body").inner_text()).lower()
                if re.search(r"\bno\s+(?:records?|transactions?|data)\b", body):
                    report("Search returned no transactions.")
                else:
                    raise RuntimeError(
                        "The finance transaction table did not appear within 60 seconds."
                    ) from error
            else:
                await controls.checkpoint()
                report("Transaction table appeared. Collecting rows and following its pager...")
                await collect_finance_pages(page, filters, controls, report, on_page=save_page)
            checkpoint.set_account_status(account, "completed")
            report("Fetch completed. Saved pages are ready for Download CSV.")
        except (WorkflowStopped, asyncio.CancelledError):
            checkpoint.set_account_status(account, "stopped")
            raise
        except Exception as error:
            message = str(error)
            for item in unique_targets:
                if item.credentials.egras_password:
                    message = message.replace(item.credentials.egras_password, "[redacted]")
            message = re.sub(
                r"\bfill\((?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')\)",
                "fill('[redacted]')",
                message,
            )
            checkpoint.set_account_status(account, "failed", message)
            report(f"Fetch failed: {message}. Previously fetched pages remain in the checkpoint.")
        finally:
            if session is not None:
                with suppress(Exception):
                    await session.close()

    async def wait_for_stop() -> None:
        while not controls.stop_event.is_set():
            await asyncio.sleep(0.1)

    async def fetch_all() -> None:
        accounts = [asyncio.create_task(fetch_account(target)) for target in unique_targets]
        try:
            await asyncio.gather(*accounts)
        finally:
            for account_task in accounts:
                if not account_task.done():
                    account_task.cancel()
            await asyncio.gather(*accounts, return_exceptions=True)

    workers = asyncio.create_task(fetch_all())
    stop = asyncio.create_task(wait_for_stop())
    try:
        await asyncio.wait([workers, stop], return_when=asyncio.FIRST_COMPLETED)
        await controls.checkpoint()
        await workers
    finally:
        # Stop interrupts long Playwright waits, not just the boundaries between pages.
        if not workers.done():
            workers.cancel()
        stop.cancel()
        await asyncio.gather(workers, stop, return_exceptions=True)
    return checkpoint.info()


def export_finance_checkpoint(checkpoint: FinanceCheckpoint, output_path: Path) -> int:
    if output_path.suffix.lower() != ".csv":
        raise ValueError("Choose a CSV file path.")
    if output_path.resolve() == checkpoint.path:
        raise ValueError("The CSV destination must not replace the checkpoint.")
    rows = checkpoint.read_rows()
    _write_finance_csv(output_path, rows)
    return len(rows)


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
