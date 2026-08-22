from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import app_data_directory


class DailyActivityLog:
    """Append redacted diagnostics to one local UTF-8 log file per calendar day."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or app_data_directory() / "logs"
        self._lock = threading.Lock()

    def write(
        self,
        event: str,
        message: str = "",
        *,
        level: str = "INFO",
        data: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now().astimezone()
        payload = redact(data or {})
        suffix = f" | {json.dumps(payload, ensure_ascii=False, sort_keys=True)}" if payload else ""
        line = (
            f"{now.isoformat(timespec='seconds')} [{level.upper()}] "
            f"{event}: {redact_text(message)}{suffix}\n"
        )
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{now.date().isoformat()}.log"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)


def redact(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            "[redacted]"
            if any(secret in key.lower() for secret in ("password", "secret", "token", "otp"))
            else value
        )
        for key, value in data.items()
    }


def redact_text(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")
