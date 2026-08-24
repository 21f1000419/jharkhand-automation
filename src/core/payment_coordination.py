from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass

from core.models import UiEvent

EventCallback = Callable[[UiEvent], None]
WaitCheck = Callable[[], Awaitable[None]]


@dataclass
class _WaitingPayment:
    emit: EventCallback


class PaymentLease:
    """The exclusive right to start and observe one UPI payment."""

    def __init__(self, coordinator: PaymentCoordinator, waiter: _WaitingPayment) -> None:
        self._coordinator = coordinator
        self._waiter = waiter
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._coordinator._release(self._waiter)


class PaymentCoordinator:
    """A process-wide FIFO gate for the short-lived SBI UPI QR flow."""

    def __init__(self) -> None:
        self._waiters: deque[_WaitingPayment] = deque()
        self._holder: _WaitingPayment | None = None
        self._changed = asyncio.Event()

    async def acquire(
        self,
        emit: EventCallback,
        *,
        wait_check: WaitCheck | None = None,
    ) -> PaymentLease:
        waiter = _WaitingPayment(emit)
        self._waiters.append(waiter)
        self._publish_positions()
        try:
            while True:
                if wait_check is not None:
                    await wait_check()
                self._changed.clear()
                if self._holder is None and self._waiters and self._waiters[0] is waiter:
                    self._holder = waiter
                    emit(UiEvent("payment_state", "Payment slot granted.", {"state": "slot_granted"}))
                    return PaymentLease(self, waiter)
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), timeout=0.1)
        except BaseException:
            self._remove(waiter)
            raise

    async def _release(self, waiter: _WaitingPayment) -> None:
        if self._holder is waiter:
            self._holder = None
        self._remove(waiter)
        waiter.emit(UiEvent("payment_state", "Payment slot released.", {"state": "slot_released"}))

    def _remove(self, waiter: _WaitingPayment) -> None:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            return
        self._changed.set()
        self._publish_positions()

    def _publish_positions(self) -> None:
        for position, waiter in enumerate(self._waiters, start=1):
            # A newly queued payment must not make the active holder look
            # queued again in the UI. Keep the absolute FIFO position for the
            # waiting entries so the next browser remains position 2 while
            # position 1 is being paid.
            if waiter is self._holder:
                continue
            waiter.emit(
                UiEvent(
                    "payment_queue",
                    f"Payment queue position: {position}.",
                    {"state": "queued", "position": position},
                )
            )


_DEFAULT_PAYMENT_COORDINATOR = PaymentCoordinator()


def default_payment_coordinator() -> PaymentCoordinator:
    """Return the coordinator shared by all workflows in this process."""
    return _DEFAULT_PAYMENT_COORDINATOR
