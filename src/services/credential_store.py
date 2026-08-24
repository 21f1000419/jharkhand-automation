from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict

from core.models import Credentials

TARGET_NAME = "Compitcom/eStampAutomation/LoginCredentials"
TAB_TARGET_PREFIX = f"{TARGET_NAME}/tab-"
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

    def load(self, tab_id: int | None = None) -> Credentials | None:
        tab_id = _normalized_tab_id(tab_id)
        credentials = self._load_target(tab_target_name(tab_id))
        if credentials is not None or tab_id != 1:
            return credentials

        # Settings before tabs used one shared target. Read it once and copy it
        # into tab 1's target so subsequent reads are scoped.
        credentials = self._load_target(TARGET_NAME)
        if credentials is not None:
            self.save(credentials, tab_id=1)
        return credentials

    def _load_target(self, target_name: str) -> Credentials | None:
        api = _credential_api()
        pointer = ctypes.POINTER(CredentialRecord)()
        if not api.CredReadW(
            target_name,
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

    def save(self, credentials: Credentials, tab_id: int | None = None) -> None:
        target_name = tab_target_name(_normalized_tab_id(tab_id))
        api = _credential_api()
        payload = encode_credentials(credentials)
        blob = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        record = CredentialRecord()
        record.Type = CREDENTIAL_TYPE_GENERIC
        record.TargetName = target_name
        record.Comment = "Saved by Compitcom eStamp Automation"
        record.CredentialBlobSize = len(payload)
        record.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        record.Persist = CREDENTIAL_PERSIST_LOCAL_MACHINE
        record.UserName = "Saved portal logins"
        if not api.CredWriteW(ctypes.byref(record), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def clear(self, tab_id: int | None = None) -> None:
        normalized_tab_id = _normalized_tab_id(tab_id)
        target_names = [tab_target_name(normalized_tab_id)]
        if normalized_tab_id == 1:
            target_names.append(TARGET_NAME)
        api = _credential_api()
        for target_name in target_names:
            if api.CredDeleteW(target_name, CREDENTIAL_TYPE_GENERIC, 0):
                continue
            error = ctypes.get_last_error()
            if error != ERROR_NOT_FOUND:
                raise ctypes.WinError(error)


def _normalized_tab_id(tab_id: int | None) -> int:
    if tab_id is None:
        return 1
    if tab_id < 1:
        raise ValueError("tab_id must be a positive integer")
    return tab_id


def tab_target_name(tab_id: int) -> str:
    """Return the Windows Credential Manager target for a tab."""
    return f"{TAB_TARGET_PREFIX}{_normalized_tab_id(tab_id)}"


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
