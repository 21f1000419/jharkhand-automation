from __future__ import annotations

import ctypes
import os
from collections.abc import Iterator
from contextlib import contextmanager


class _ThreadPowerThrottlingState(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_ulong),
        ("ControlMask", ctypes.c_ulong),
        ("StateMask", ctypes.c_ulong),
    ]


_THREAD_POWER_THROTTLING = 3
_THREAD_POWER_THROTTLING_CURRENT_VERSION = 1
_THREAD_POWER_THROTTLING_EXECUTION_SPEED = 0x1


def _set_execution_speed_policy(control_mask: int, state_mask: int) -> bool:
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_thread = kernel32.GetCurrentThread
        get_current_thread.restype = ctypes.c_void_p
        set_thread_information = kernel32.SetThreadInformation
        set_thread_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        set_thread_information.restype = ctypes.c_int
        state = _ThreadPowerThrottlingState(
            Version=_THREAD_POWER_THROTTLING_CURRENT_VERSION,
            ControlMask=control_mask,
            StateMask=state_mask,
        )
        return bool(
            set_thread_information(
                get_current_thread(),
                _THREAD_POWER_THROTTLING,
                ctypes.byref(state),
                ctypes.sizeof(state),
            )
        )
    except (AttributeError, OSError):
        # Older Windows builds and Wine may not expose this API. OCR still
        # works there, but Windows retains control of the worker's QoS.
        return False


@contextmanager
def high_performance_thread() -> Iterator[None]:
    """Keep short CPU-heavy inference work out of Windows EcoQoS."""
    policy_changed = _set_execution_speed_policy(
        _THREAD_POWER_THROTTLING_EXECUTION_SPEED,
        0,
    )
    try:
        yield
    finally:
        if policy_changed:
            # Return the pooled worker thread to Windows-managed QoS after
            # inference instead of leaving every future task at HighQoS.
            _set_execution_speed_policy(0, 0)
