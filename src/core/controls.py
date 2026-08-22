from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable

from core.models import UiEvent, WorkflowStopped


class RunControls:
    def __init__(self, emit: Callable[[UiEvent], None]) -> None:
        self.emit = emit
        self.run_gate = threading.Event()
        self.stop_event = threading.Event()
        self.decisions: queue.Queue[str] = queue.Queue()
        self.stop_reason = "Stopped by user"
        self.run_gate.set()

    def reset(self) -> None:
        self.stop_event.clear()
        self.stop_reason = "Stopped by user"
        self.run_gate.set()
        while not self.decisions.empty():
            try:
                self.decisions.get_nowait()
            except queue.Empty:
                break

    def pause(self) -> None:
        self.run_gate.clear()

    def resume(self) -> None:
        self.run_gate.set()

    def stop(self, reason: str = "Stopped by user") -> None:
        self.stop_reason = reason
        self.stop_event.set()
        self.run_gate.set()

    def decide(self, action: str) -> None:
        self.decisions.put(action)

    async def checkpoint(self) -> None:
        if self.stop_event.is_set():
            raise WorkflowStopped
        while not self.run_gate.is_set():
            if self.stop_event.is_set():
                raise WorkflowStopped
            await asyncio.sleep(0.1)

    async def ensure_not_stopped(self) -> None:
        """Check cancellation without requiring a paused manual checkpoint to resume."""
        if self.stop_event.is_set():
            raise WorkflowStopped

    async def manual_checkpoint(self, kind: str, message: str) -> None:
        self.run_gate.clear()
        self.emit(UiEvent("manual_checkpoint", message, {"checkpoint": kind}))
        await self.checkpoint()

    async def wait_for_decision(self) -> str:
        while True:
            if self.stop_event.is_set():
                raise WorkflowStopped
            try:
                return self.decisions.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.1)
