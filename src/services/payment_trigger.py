from __future__ import annotations

import asyncio
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


async def send_payment_trigger_request(url: str, method: str, timeout_seconds: float = 15) -> int:
    return await asyncio.to_thread(_send_payment_trigger_request, url, method, timeout_seconds)


def _send_payment_trigger_request(url: str, method: str, timeout_seconds: float) -> int:
    normalized_url = url.strip()
    scheme = urlsplit(normalized_url).scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ValueError("Payment trigger URL must use http:// or https://.")

    normalized_method = method.strip().upper()
    if normalized_method not in {"GET", "POST"}:
        raise ValueError("Payment trigger method must be GET or POST.")

    request = Request(
        normalized_url,
        data=b"" if normalized_method == "POST" else None,
        headers={"User-Agent": "Compitcom-eStamp-Automation/1.0"},
        method=normalized_method,
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        status = getattr(response, "status", 200)
        response.read(1)
    return int(status)
