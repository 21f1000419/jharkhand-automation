from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import Download, Locator, Page
from playwright.async_api import Error as PlaywrightError

DOWNLOAD_ATTEMPTS = 3
MINIMUM_PDF_BYTES = 500

FETCH_PDF_SCRIPT = """async url => {
    const response = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        cache: 'no-store',
        headers: {Accept: 'application/pdf,application/octet-stream;q=0.9,*/*;q=0.8'}
    });
    const bytes = new Uint8Array(await response.arrayBuffer());
    let binary = '';
    const chunkSize = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
    }
    return {
        status: response.status,
        contentType: response.headers.get('content-type') || '',
        body: btoa(binary)
    };
}"""


class EstampDownloader:
    async def download(
        self,
        page: Page,
        link: Locator,
        output_directory: Path,
        row_number: int,
        sequence: int,
    ) -> tuple[Path, str]:
        href = await link.get_attribute("href") or ""
        url = urljoin(page.url, href)
        reference = extract_reference(url) or f"unit-{sequence}"
        output_directory.mkdir(parents=True, exist_ok=True)
        destination = output_directory / f"eStamp_{reference}.pdf"
        temporary = destination.with_suffix(".pdf.part")
        try:
            data = await self._fetch_pdf(page, url)
            temporary.write_bytes(data)
        except RuntimeError as fetch_error:
            try:
                await self._download_by_click(page, link, temporary)
            except Exception as click_error:
                raise RuntimeError(
                    f"{fetch_error} Clicking the eStamp button also failed: {click_error}"
                ) from click_error
        temporary.replace(destination)
        return destination, reference

    async def _download_by_click(self, page: Page, link: Locator, destination: Path) -> None:
        """Use the portal's eStamp button and capture Chrome/Firefox's native download."""
        download_task = asyncio.create_task(page.wait_for_event("download", timeout=60_000))
        try:
            await link.click()
            download = await download_task
        finally:
            if not download_task.done():
                download_task.cancel()
                await asyncio.gather(download_task, return_exceptions=True)
        if not isinstance(download, Download):
            raise RuntimeError("The eStamp button did not produce a browser download.")
        failure = await download.failure()
        if failure:
            raise RuntimeError(f"The browser reported a failed eStamp download: {failure}")
        await download.save_as(destination)
        data = destination.read_bytes()
        if len(data) < MINIMUM_PDF_BYTES or not data.startswith(b"%PDF"):
            destination.unlink(missing_ok=True)
            raise RuntimeError("The file downloaded by the eStamp button was not a valid PDF.")

    async def _fetch_pdf(self, page: Page, url: str) -> bytes:
        """Fetch through the live result page so cookies, referrer, and browser identity are retained."""
        last_error = "The eStamp download did not contain a valid PDF document."
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                result = await page.evaluate(FETCH_PDF_SCRIPT, url)
            except PlaywrightError as error:
                if page.is_closed():
                    raise
                result = None
                last_error = f"The live browser could not request the eStamp PDF: {error}"
            if not isinstance(result, dict):
                if result is not None:
                    last_error = "The eStamp portal returned an invalid download response."
            else:
                status = result.get("status")
                content_type = str(result.get("contentType", "")).lower()
                encoded = result.get("body")
                if isinstance(encoded, str):
                    try:
                        data = base64.b64decode(encoded, validate=True)
                    except ValueError:
                        data = b""
                else:
                    data = b""
                if status == 200 and len(data) >= MINIMUM_PDF_BYTES and (
                    data.startswith(b"%PDF") or "pdf" in content_type
                ):
                    return data
                if isinstance(status, int) and status != 200:
                    last_error = f"eStamp download returned HTTP {status}."
                else:
                    last_error = "The eStamp download did not contain a valid PDF document."
            if attempt < DOWNLOAD_ATTEMPTS:
                await asyncio.sleep(attempt)
        raise RuntimeError(f"{last_error} Retried {DOWNLOAD_ATTEMPTS} times in the live browser session.")


def extract_reference(url: str) -> str:
    match = re.search(r"gras_estamp_download/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else ""
