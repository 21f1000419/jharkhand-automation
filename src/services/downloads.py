from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import BrowserContext, Page


class EstampDownloader:
    def __init__(self, context: BrowserContext) -> None:
        self.context = context

    async def download(
        self,
        page: Page,
        href: str,
        output_directory: Path,
        row_number: int,
        sequence: int,
    ) -> tuple[Path, str]:
        url = urljoin(page.url, href)
        response = await self.context.request.get(url, timeout=60_000)
        if not response.ok:
            raise RuntimeError(f"eStamp download returned HTTP {response.status}.")
        data = await response.body()
        content_type = (response.headers.get("content-type") or "").lower()
        if len(data) < 500 or (not data.startswith(b"%PDF") and "pdf" not in content_type):
            raise RuntimeError("The eStamp download did not contain a valid PDF document.")

        reference = extract_reference(url) or f"unit-{sequence}"
        output_directory.mkdir(parents=True, exist_ok=True)
        destination = output_directory / f"eStamp_row-{row_number:03d}_unit-{sequence:03d}_{reference}.pdf"
        temporary = destination.with_suffix(".pdf.part")
        temporary.write_bytes(data)
        temporary.replace(destination)
        return destination, reference


def extract_reference(url: str) -> str:
    match = re.search(r"gras_estamp_download/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else ""
