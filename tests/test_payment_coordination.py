from __future__ import annotations

import asyncio
import unittest

from core.models import UiEvent
from core.payment_coordination import PaymentCoordinator


class PaymentCoordinatorTests(unittest.TestCase):
    def test_second_payment_waits_for_first_and_receives_fifo_position(self) -> None:
        async def run() -> tuple[list[UiEvent], list[UiEvent]]:
            coordinator = PaymentCoordinator()
            first_events: list[UiEvent] = []
            second_events: list[UiEvent] = []
            first = await coordinator.acquire(first_events.append)
            second_task = asyncio.create_task(coordinator.acquire(second_events.append))
            await asyncio.sleep(0)
            self.assertFalse(second_task.done())
            await first.release()
            second = await second_task
            await second.release()
            return first_events, second_events

        first_events, second_events = asyncio.run(run())

        self.assertTrue(
            any(event.data == {"state": "queued", "position": 1} for event in first_events)
        )
        self.assertTrue(
            any(event.data == {"state": "queued", "position": 2} for event in second_events)
        )
        self.assertTrue(
            any(event.data.get("state") == "slot_granted" for event in second_events)
        )

    def test_cancelled_waiter_is_removed(self) -> None:
        async def run() -> list[UiEvent]:
            coordinator = PaymentCoordinator()
            first = await coordinator.acquire(lambda _event: None)
            events: list[UiEvent] = []
            waiting = asyncio.create_task(coordinator.acquire(events.append))
            await asyncio.sleep(0)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
            await first.release()
            return events

        events = asyncio.run(run())
        self.assertTrue(any(event.data.get("position") == 2 for event in events))


if __name__ == "__main__":
    unittest.main()
