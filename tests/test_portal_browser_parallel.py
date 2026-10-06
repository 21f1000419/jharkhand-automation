from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from automation.browser import PortalBrowserSession
from core.models import BrowserEngine, PortalBrowser


class PortalParallelLaunchTests(unittest.TestCase):
    def test_window_identification_is_serialized_across_repeated_event_loops(self) -> None:
        active = 0
        peak = 0
        captures = 0

        async def capture(_session: PortalBrowserSession) -> None:
            nonlocal active, peak, captures
            active += 1
            peak = max(peak, active)
            captures += 1
            try:
                await asyncio.sleep(0.02)
            finally:
                active -= 1

        def context() -> MagicMock:
            result = MagicMock()
            result.close = AsyncMock()
            return result

        playwright = MagicMock()
        playwright.chromium.launch_persistent_context = AsyncMock(side_effect=[context() for _ in range(4)])
        playwright.stop = AsyncMock()
        factory = MagicMock()
        factory.start = AsyncMock(return_value=playwright)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("automation.browser.async_playwright", return_value=factory),
            patch.object(PortalBrowserSession, "_capture_window_handle", new=capture),
        ):
            choice = PortalBrowser("Test", Path(sys.executable), BrowserEngine.CHROMIUM)

            async def launch_pair(run: int) -> None:
                sessions = [
                    PortalBrowserSession(choice, lambda _: None, Path(directory) / f"run-{run}-{index}")
                    for index in range(2)
                ]
                try:
                    await asyncio.gather(*(session.start() for session in sessions))
                finally:
                    await asyncio.gather(*(session.close() for session in sessions))

            # The old asyncio lock bound itself during run one, then failed in run two.
            asyncio.run(launch_pair(1))
            asyncio.run(launch_pair(2))
        self.assertEqual(captures, 4)
        self.assertEqual(peak, 1)


if __name__ == "__main__":
    unittest.main()
