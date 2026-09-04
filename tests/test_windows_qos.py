from __future__ import annotations

import unittest
from unittest.mock import call, patch

from services.windows_qos import high_performance_thread


class WindowsQosTests(unittest.TestCase):
    def test_high_performance_policy_is_scoped_to_the_work(self) -> None:
        with (
            patch(
                "services.windows_qos._set_execution_speed_policy", return_value=True
            ) as set_policy,
            high_performance_thread(),
        ):
            pass

        self.assertEqual(set_policy.call_args_list, [call(1, 0), call(0, 0)])


if __name__ == "__main__":
    unittest.main()
