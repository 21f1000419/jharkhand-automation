from __future__ import annotations

import asyncio
import json
import ssl
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import certifi


class SmsOtpServerError(RuntimeError):
    """The SMS OTP server could not be reached or returned an invalid response."""


class SmsOtpClient:
    """Small HTTPS client for the local SMS OTP server."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        # The macOS framework Python installation can lack the system CA
        # bundle. certifi provides a current, portable bundle for the SMS
        # server without weakening TLS verification.
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url)

    def request_time(self) -> str:
        """Record the desktop's current time in UTC for an OTP freshness filter."""
        return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    async def get_main_otp(self, user_id: str, not_before: str) -> str | None:
        return await asyncio.to_thread(self._get_otp, self._main_endpoint(user_id, not_before))

    async def get_egrass_otp(self, reference_number: str) -> str | None:
        return await asyncio.to_thread(
            self._get_otp,
            self._egrass_endpoint(reference_number),
        )

    async def delete_main_otp_after_use(self, user_id: str, otp: str) -> bool:
        """Delete in a worker thread so cleanup never blocks browser automation."""
        return await asyncio.to_thread(self._delete_otp, self._main_endpoint(user_id), otp)

    async def delete_egrass_otp_after_use(self, reference_number: str, otp: str) -> bool:
        return await asyncio.to_thread(
            self._delete_otp,
            self._egrass_endpoint(reference_number),
            otp,
        )

    def _main_endpoint(self, user_id: str, not_before: str | None = None) -> str:
        query = f"?{urlencode({'notBefore': not_before})}" if not_before else ""
        return (
            f"{self.base_url}/api/users/{quote(user_id, safe='')}/otps/"
            f"main{query}"
        )

    def _egrass_endpoint(self, reference_number: str) -> str:
        return f"{self.base_url}/api/egrass/otps/{quote(reference_number, safe='')}"

    def _get_otp(self, endpoint: str) -> str | None:
        try:
            with urlopen(endpoint, timeout=8, context=self._ssl_context) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 404:
                return None
            raise SmsOtpServerError(f"SMS server returned HTTP {error.code}.") from error
        except (OSError, TimeoutError, URLError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SmsOtpServerError(f"Could not read the SMS server: {error}") from error

        otp = payload.get("otp")
        if payload.get("found") is True and isinstance(otp, str) and otp:
            return otp
        return None

    def _delete_otp(self, endpoint: str, otp: str) -> bool:
        data = json.dumps({"otp": otp}).encode("utf-8")
        request = Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="DELETE",
        )
        try:
            with urlopen(request, timeout=8, context=self._ssl_context) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            # The server keeps a newer OTP instead of deleting it. This is a
            # normal, safe outcome for detached cleanup.
            if error.code == 409:
                return False
            raise SmsOtpServerError(f"Could not remove used OTP from the SMS server: {error}") from error
        except (OSError, TimeoutError, URLError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SmsOtpServerError(f"Could not remove used OTP from the SMS server: {error}") from error
        return payload.get("deleted") is True
