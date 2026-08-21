from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

if TYPE_CHECKING:
    from playwright.sync_api import Frame

MAX_ASSET_BYTES = 10 * 1024 * 1024
URL_PATTERN = re.compile(r"url\(\s*['\"]?([^'\")\s]+)", re.IGNORECASE)
ATTRIBUTE_PATTERN = re.compile(
    r"<(?:script|img|iframe|source|video|audio|embed|object|link)\b[^>]*?\b(?:src|href)\s*=\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)


class ReferenceRecorder:
    """Save a navigated document and the browser resources it references."""

    def __init__(self, root: Path, log: Callable[[str], None]) -> None:
        self.root = root
        self.log = log
        self.sequence = 0
        self.captured_urls: set[tuple[str, str]] = set()

    def record(self, frame: Frame) -> None:
        url = frame.url
        if not url or url.startswith(("about:", "data:", "javascript:")):
            return
        frame_name = frame.name or "main"
        key = (frame_name, url)
        if key in self.captured_urls:
            return

        try:
            html = frame.content()
        except Exception as error:
            self.log(f"Reference capture skipped for {url}: {error}")
            return

        self.captured_urls.add(key)
        self.sequence += 1
        capture_directory = self._capture_directory(frame_name, url)
        capture_directory.mkdir(parents=True, exist_ok=True)
        (capture_directory / "index.html").write_text(html, encoding="utf-8")

        sources = self._source_urls(frame, html, url)
        assets_directory = capture_directory / "assets"
        assets_directory.mkdir(exist_ok=True)
        saved_assets: list[dict[str, str]] = []
        for index, source_url in enumerate(sources, start=1):
            asset = self._download_asset(frame, source_url, assets_directory, index)
            if asset is not None:
                saved_assets.append(asset)

        (capture_directory / "manifest.json").write_text(
            json.dumps(
                {
                    "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "frame_name": frame_name,
                    "url": url,
                    "asset_count": len(saved_assets),
                    "assets": saved_assets,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.log(f"Saved reference snapshot {capture_directory.name} ({len(saved_assets)} assets).")

    def _capture_directory(self, frame_name: str, url: str) -> Path:
        parsed = urlsplit(url)
        host = self._safe_name(parsed.netloc or "local")
        path = self._safe_name(Path(parsed.path).stem or "page")
        frame = self._safe_name(frame_name)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return self.root / f"{self.sequence:03d}-{timestamp}-{host}-{frame}-{path}"

    def _source_urls(self, frame: Frame, html: str, base_url: str) -> list[str]:
        candidates = set(ATTRIBUTE_PATTERN.findall(html))
        candidates.update(URL_PATTERN.findall(html))
        with suppress(Exception):
            candidates.update(
                frame.evaluate("() => performance.getEntriesByType('resource').map(entry => entry.name)")
            )

        urls = {
            urljoin(base_url, candidate.strip())
            for candidate in candidates
            if candidate and not candidate.startswith(("data:", "javascript:", "#"))
        }
        return sorted(url for url in urls if url.startswith(("http://", "https://")))

    def _download_asset(
        self, frame: Frame, source_url: str, assets_directory: Path, index: int
    ) -> dict[str, str] | None:
        try:
            response = frame.page.context.request.get(source_url, timeout=20_000)
            if not response.ok:
                return None
            body = response.body()
            if len(body) > MAX_ASSET_BYTES:
                return None
        except Exception:
            return None

        parsed = urlsplit(source_url)
        basename = self._safe_name(Path(parsed.path).name or "resource")
        relative_path = Path("assets") / f"{index:03d}-{basename}"
        (assets_directory / relative_path.name).write_bytes(body)
        return {"url": source_url, "file": relative_path.as_posix()}

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "resource"
