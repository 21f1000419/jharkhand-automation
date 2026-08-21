from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict

from core.models import Credentials

TARGET_NAME = "Compitcom/eStampAutomation/LoginCredentials"
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
    """Stores optional portal credentials in the current user's Windows vault."""

    def __init__(self, target_name: str = TARGET_NAME) -> None:
        self.target_name = target_name

    def load(self) -> Credentials | None:
        api = _credential_api()
        pointer = ctypes.POINTER(CredentialRecord)()
        if not api.CredReadW(
            self.target_name,
            CREDENTIAL_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        ):
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return None
            raise ctypes.WinError(error)
        try:
            record = pointer.contents
            payload = ctypes.string_at(record.CredentialBlob, record.CredentialBlobSize)
            return decode_credentials(payload)
        finally:
            api.CredFree(pointer)

    def save(self, credentials: Credentials) -> None:
        api = _credential_api()
        payload = encode_credentials(credentials)
        blob = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        record = CredentialRecord()
        record.Type = CREDENTIAL_TYPE_GENERIC
        record.TargetName = self.target_name
        record.Comment = "Saved by Compitcom eStamp Automation"
        record.CredentialBlobSize = len(payload)
        record.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        record.Persist = CREDENTIAL_PERSIST_LOCAL_MACHINE
        record.UserName = "Saved portal logins"
        if not api.CredWriteW(ctypes.byref(record), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def clear(self) -> None:
        api = _credential_api()
        if api.CredDeleteW(self.target_name, CREDENTIAL_TYPE_GENERIC, 0):
            return
        error = ctypes.get_last_error()
        if error != ERROR_NOT_FOUND:
            raise ctypes.WinError(error)


def encode_credentials(credentials: Credentials) -> bytes:
    return json.dumps(asdict(credentials), ensure_ascii=False).encode("utf-16-le")


def decode_credentials(payload: bytes) -> Credentials:
    values = json.loads(payload.decode("utf-16-le"))
    if not isinstance(values, dict):
        raise ValueError("Saved credentials have an invalid format.")
    allowed = {"citizen_username", "citizen_password", "egras_username", "egras_password"}
    cleaned = {key: str(value) for key, value in values.items() if key in allowed}
    return Credentials(**cleaned)


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
