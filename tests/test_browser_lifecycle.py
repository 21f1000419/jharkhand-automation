from __future__ import annotations

import subprocess
import sys
import unittest
from contextlib import suppress

from automation.browser import (
    _TRACKED_PROCESSES,
    cleanup_all_spawned_processes,
    register_process,
    terminate_process_tree_sync,
    unregister_process,
)


class BrowserLifecycleTests(unittest.TestCase):
    def test_register_and_unregister_process(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            register_process(proc)
            self.assertIn(proc, _TRACKED_PROCESSES)
            unregister_process(proc)
            self.assertNotIn(proc, _TRACKED_PROCESSES)
        finally:
            terminate_process_tree_sync(proc)
            with suppress(Exception):
                proc.wait(timeout=2)

    def test_cleanup_all_spawned_processes(self) -> None:
        proc1 = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc2 = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            register_process(proc1)
            register_process(proc2)
            self.assertIn(proc1, _TRACKED_PROCESSES)
            self.assertIn(proc2, _TRACKED_PROCESSES)

            cleanup_all_spawned_processes()

            self.assertEqual(len(_TRACKED_PROCESSES), 0)
            proc1.wait(timeout=3)
            proc2.wait(timeout=3)
            self.assertIsNotNone(proc1.poll())
            self.assertIsNotNone(proc2.poll())
        finally:
            terminate_process_tree_sync(proc1)
            terminate_process_tree_sync(proc2)


if __name__ == "__main__":
    unittest.main()
