from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict

try:
    from .models import Credentials
except ImportError:
    from models import Credentials


TARGET_NAME = "Compitcom/eStampAutomation/ChandigarhLoginCredentials"
CREDENTIAL_TYPE_GENERIC = 1
CREDENTIAL_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class CredentialRecord(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialStore:
    """Stores the Chandigarh portal login in the current user's Windows vault."""

    def load(self) -> Credentials | None:
        api = _credential_api()
        pointer = ctypes.POINTER(CredentialRecord)()
        if not api.CredReadW(TARGET_NAME, CREDENTIAL_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return None
            raise ctypes.WinError(error)
        try:
            record = pointer.contents
            payload = ctypes.string_at(record.CredentialBlob, record.CredentialBlobSize)
            values = json.loads(payload.decode("utf-16-le"))
            if not isinstance(values, dict):
                raise ValueError("Saved credentials have an invalid format.")
            return Credentials(
                citizen_username=str(values.get("citizen_username", "")),
                citizen_password=str(values.get("citizen_password", "")),
            )
        finally:
            api.CredFree(pointer)

    def save(self, credentials: Credentials) -> None:
        api = _credential_api()
        payload = json.dumps(asdict(credentials), ensure_ascii=False).encode("utf-16-le")
        blob = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        record = CredentialRecord()
        record.Type = CREDENTIAL_TYPE_GENERIC
        record.TargetName = TARGET_NAME
        record.Comment = "Saved by Compitcom Chandigarh eStamp Automation"
        record.CredentialBlobSize = len(payload)
        record.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        record.Persist = CREDENTIAL_PERSIST_LOCAL_MACHINE
        record.UserName = "Saved Chandigarh portal login"
        if not api.CredWriteW(ctypes.byref(record), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def clear(self) -> None:
        api = _credential_api()
        if api.CredDeleteW(TARGET_NAME, CREDENTIAL_TYPE_GENERIC, 0):
            return
        error = ctypes.get_last_error()
        if error != ERROR_NOT_FOUND:
            raise ctypes.WinError(error)


def _credential_api() -> ctypes.WinDLL:
    if os.name != "nt":
        raise RuntimeError("Saved credentials require Windows Credential Manager.")
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(CredentialRecord), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CredentialRecord)),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api
