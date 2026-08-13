from __future__ import annotations

import asyncio
import json
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass(frozen=True)
class ReceivedOtp:
    value: str
    received_at: float
    sequence: int


class WifiOtpReceiver:
    """Receives token-authenticated OTP values from the paired Android app on the LAN."""

    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(24)
        self._condition = threading.Condition()
        self._latest: ReceivedOtp | None = None
        self._sequence = 0
        self._last_pairing = 0.0
        self._server = ThreadingHTTPServer(("0.0.0.0", 0), self._handler())
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="otp-wifi-receiver", daemon=True
        )
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._server.server_port)

    @property
    def server_address(self) -> str:
        return f"http://{local_network_address()}:{self.port}"

    @property
    def sequence(self) -> int:
        with self._condition:
            return self._sequence

    @property
    def is_paired(self) -> bool:
        with self._condition:
            return time.monotonic() - self._last_pairing < 120

    async def wait_for_new(self, after_sequence: int, timeout_seconds: float = 90) -> str | None:
        return await asyncio.to_thread(self._wait_for_new, after_sequence, timeout_seconds)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def _wait_for_new(self, after_sequence: int, timeout_seconds: float) -> str | None:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while self._latest is None or self._latest.sequence <= after_sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._latest.value

    def _accept(self, value: str) -> None:
        if not value.isdigit() or not 4 <= len(value) <= 8:
            return
        with self._condition:
            self._sequence += 1
            self._latest = ReceivedOtp(value, time.time(), self._sequence)
            self._condition.notify_all()

    def _pair(self) -> None:
        with self._condition:
            self._last_pairing = time.monotonic()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        receiver = self

        class OtpHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if (
                    self.path not in {"/otp", "/pair"}
                    or self.headers.get("X-Compitcom-Token") != receiver.token
                ):
                    self.send_error(HTTPStatus.UNAUTHORIZED)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    value = str(payload["otp"]) if self.path == "/otp" else ""
                except (KeyError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
                    self.send_error(HTTPStatus.BAD_REQUEST)
                    return
                if self.path == "/otp" and (not value.isdigit() or not 4 <= len(value) <= 8):
                    self.send_error(HTTPStatus.BAD_REQUEST)
                    return
                receiver._pair()
                if self.path == "/otp":
                    receiver._accept(value)
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        return OtpHandler


def local_network_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"
