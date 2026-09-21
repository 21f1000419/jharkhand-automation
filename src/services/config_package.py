from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import struct
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.config import AppConfig, TabConfig, default_portal_profile_path
from core.models import Credentials
from services.credential_store import CredentialStore

PACKAGE_MAGIC = b"ESTAMPCFG\x01\x00"
MAGIC_LEN = len(PACKAGE_MAGIC)
SALT_LEN = 16
MAC_LEN = 32
HEADER_LEN = MAGIC_LEN + SALT_LEN + MAC_LEN
DEFAULT_APP_SECRET = b"Compitcom::eStampAutomation::ConfigPackage::Key::v1"
PBKDF2_ROUNDS = 50_000

# These are the settings a user can fill in on an ID's automation form.  Profile
# identities intentionally do not travel with an export: they belong to this
# Windows installation and may contain an active signed-in browser session.
EXPORTED_TAB_CONFIG_FIELDS = frozenset(
    {
        "enabled",
        "sms_user_id",
        "browser_count",
    }
)


class ConfigPackageError(Exception):
    """Base exception for config package export and import errors."""


def _portable_tab_config(values: dict[str, Any]) -> dict[str, Any]:
    """Return only the form settings that may move between installations."""
    return {key: value for key, value in values.items() if key in EXPORTED_TAB_CONFIG_FIELDS}


@dataclass
class TabExportData:
    """Config and credentials for one exported tab/ID."""

    tab_config: dict[str, Any]
    credentials: dict[str, str] = field(default_factory=dict)
    credentials_saved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tab_config": self.tab_config,
            "credentials": self.credentials,
            "credentials_saved": self.credentials_saved,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TabExportData:
        return cls(
            tab_config=dict(data.get("tab_config") or {}),
            credentials=dict(data.get("credentials") or {}),
            credentials_saved=bool(data.get("credentials_saved", False)),
        )


def _derive_keys(secret: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Derive cipher and HMAC keys from the master secret and salt."""
    master = hashlib.pbkdf2_hmac("sha256", secret, salt, PBKDF2_ROUNDS, dklen=32)
    cipher_key = hashlib.sha256(master + b":cipher").digest()
    hmac_key = hashlib.sha256(master + b":hmac").digest()
    return cipher_key, hmac_key


def _xor_keystream(data: bytes, cipher_key: bytes, salt: bytes) -> bytes:
    """CTR-mode keystream XOR using HMAC-SHA256 counter blocks."""
    output = bytearray(len(data))
    block_size = 32
    num_blocks = (len(data) + block_size - 1) // block_size
    for block_idx in range(num_blocks):
        counter = struct.pack(">Q", block_idx)
        keystream = hmac.new(cipher_key, salt + counter, hashlib.sha256).digest()
        start = block_idx * block_size
        end = min(start + block_size, len(data))
        chunk_len = end - start
        for i in range(chunk_len):
            output[start + i] = data[start + i] ^ keystream[i]
    return bytes(output)


def encrypt_config_data(payload: dict[str, Any], secret_key: bytes | None = None) -> bytes:
    """Serialize, compress, encrypt, and authenticate configuration data."""
    secret = secret_key or DEFAULT_APP_SECRET
    salt = secrets.token_bytes(SALT_LEN)
    cipher_key, hmac_key = _derive_keys(secret, salt)

    json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    compressed = zlib.compress(json_bytes, level=9)
    ciphertext = _xor_keystream(compressed, cipher_key, salt)

    mac = hmac.new(hmac_key, PACKAGE_MAGIC + salt + ciphertext, hashlib.sha256).digest()
    return PACKAGE_MAGIC + salt + mac + ciphertext


def decrypt_config_data(blob: bytes, secret_key: bytes | None = None) -> dict[str, Any]:
    """Verify, decrypt, decompress, and deserialize configuration data."""
    if len(blob) < HEADER_LEN:
        raise ConfigPackageError("The file is too small to be a valid configuration package.")

    magic = blob[:MAGIC_LEN]
    if magic != PACKAGE_MAGIC:
        raise ConfigPackageError("The file is not a recognized configuration package or is incompatible.")

    salt = blob[MAGIC_LEN : MAGIC_LEN + SALT_LEN]
    mac = blob[MAGIC_LEN + SALT_LEN : HEADER_LEN]
    ciphertext = blob[HEADER_LEN:]

    secret = secret_key or DEFAULT_APP_SECRET
    cipher_key, hmac_key = _derive_keys(secret, salt)

    expected_mac = hmac.new(hmac_key, PACKAGE_MAGIC + salt + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ConfigPackageError(
            "Authentication failed: the package has been altered, corrupted, "
            "or was encoded with an invalid key."
        )

    try:
        compressed = _xor_keystream(ciphertext, cipher_key, salt)
        json_bytes = zlib.decompress(compressed)
        parsed = json.loads(json_bytes.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("Expected JSON dictionary at package root.")
        return parsed
    except Exception as error:
        raise ConfigPackageError(f"Could not parse configuration data: {error}") from error


def create_export_package(
    app_config: AppConfig,
    tabs_data: list[TabExportData],
    *,
    include_global_settings: bool = True,
) -> dict[str, Any]:
    """Build a portable, user-configurable export dictionary ready for encryption.

    ``include_global_settings`` remains accepted for callers from older releases,
    but global settings are intentionally local and are no longer exported.
    """
    package: dict[str, Any] = {
        "version": 2,
        "app_name": "eStampAutomation",
        "exported_at": datetime.now(UTC).isoformat(),
        "tabs": [
            TabExportData(
                tab_config=_portable_tab_config(tab.tab_config),
                credentials=tab.credentials,
                credentials_saved=tab.credentials_saved,
            ).to_dict()
            for tab in tabs_data
        ],
    }
    return package


def export_package_to_file(
    file_path: Path | str,
    payload: dict[str, Any],
    secret_key: bytes | None = None,
) -> Path:
    """Encrypt and write a configuration package to disk atomically."""
    target = Path(file_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encrypted = encrypt_config_data(payload, secret_key)

    temp_path = target.with_suffix(f"{target.suffix}.tmp-{secrets.token_hex(4)}")
    temp_path.write_bytes(encrypted)
    os.replace(temp_path, target)
    return target


def import_package_from_file(
    file_path: Path | str,
    secret_key: bytes | None = None,
) -> dict[str, Any]:
    """Read and decrypt a configuration package from disk."""
    target = Path(file_path).expanduser().resolve()
    if not target.is_file():
        raise ConfigPackageError(f"Configuration package file not found: {target}")
    try:
        blob = target.read_bytes()
    except OSError as error:
        raise ConfigPackageError(f"Could not read configuration package file: {error}") from error
    return decrypt_config_data(blob, secret_key)


def apply_imported_package(
    package: dict[str, Any],
    app_config: AppConfig,
    credential_store: CredentialStore,
    *,
    mode: str = "replace",  # "replace" | "merge" | "single_tab"
    target_tab_id: int | None = None,
) -> tuple[AppConfig, list[int]]:
    """Apply imported configuration and credentials into AppConfig and CredentialStore.

    Returns:
        tuple[AppConfig, list[int]]: The updated AppConfig and list of imported/updated tab IDs.
    """
    raw_tabs = package.get("tabs")
    if not isinstance(raw_tabs, list) or not raw_tabs:
        raise ConfigPackageError("No valid tab configurations found in the package.")

    imported_tabs_data: list[TabExportData] = []
    for raw in raw_tabs:
        if isinstance(raw, dict):
            imported_tabs_data.append(TabExportData.from_dict(raw))

    if not imported_tabs_data:
        raise ConfigPackageError("The configuration package contains no valid ID data.")

    imported_ids: list[int] = []

    if mode == "single_tab":
        # Apply first tab data to a specific existing tab
        if target_tab_id is None:
            raise ConfigPackageError("target_tab_id must be provided for single_tab mode.")
        tab_data = imported_tabs_data[0]
        tab = app_config.get_tab(target_tab_id)
        _apply_tab_dict_to_tab(tab_data.tab_config, tab)
        _apply_credentials(tab_data, tab.tab_id, credential_store)
        imported_ids.append(tab.tab_id)

    elif mode == "replace":
        # Global browser and profile settings are local.  Do not restore them
        # from either current or older package versions.
        local_profiles_by_id = {
            tab.tab_id: (tab.profile_number, tab.portal_profile_path) for tab in app_config.tabs
        }

        # Reconstruct the tabs, retaining the local browser profile for an ID
        # when it already exists.  Older packages may include profile fields;
        # they are intentionally ignored here.
        new_tabs: list[TabConfig] = []
        seen_profiles: set[int] = set()

        for position, tab_data in enumerate(imported_tabs_data, start=1):
            raw_cfg = _portable_tab_config(tab_data.tab_config)
            # IDs are local UI slots.  The package order determines the slot,
            # so legacy tab_id values are deliberately ignored.
            tab_id = position
            local_profile = local_profiles_by_id.get(tab_id)
            if local_profile is not None and local_profile[0] not in seen_profiles:
                profile_number, profile_path = local_profile
            else:
                profile_number = max(seen_profiles, default=0) + 1
                profile_path = str(default_portal_profile_path(profile_number))

            seen_profiles.add(profile_number)

            tab = TabConfig.from_dict(raw_cfg, tab_id=tab_id, profile_number=profile_number)
            tab.portal_profile_path = profile_path
            new_tabs.append(tab)
            _apply_credentials(tab_data, tab.tab_id, credential_store)
            imported_ids.append(tab.tab_id)

        app_config.tabs = new_tabs
        app_config._ensure_tab_one()
        app_config._ensure_unique_profiles()
        app_config._normalize_profile_counter()

    elif mode == "merge":
        # Add imported tabs alongside existing tabs
        used_ids = {tab.tab_id for tab in app_config.tabs}
        used_profiles = {tab.profile_number for tab in app_config.tabs}

        for tab_data in imported_tabs_data:
            raw_cfg = _portable_tab_config(tab_data.tab_config)
            # Allocate fresh unused tab_id
            next_id = 1
            while next_id in used_ids:
                next_id += 1
            used_ids.add(next_id)

            # Allocate fresh unused profile_number
            next_profile = next_id
            while next_profile in used_profiles:
                next_profile += 1
            used_profiles.add(next_profile)

            tab = TabConfig.from_dict(raw_cfg, tab_id=next_id, profile_number=next_profile)
            tab.portal_profile_path = str(default_portal_profile_path(next_profile))
            app_config.tabs.append(tab)
            _apply_credentials(tab_data, tab.tab_id, credential_store)
            imported_ids.append(tab.tab_id)

        app_config._ensure_unique_profiles()
        app_config._normalize_profile_counter()

    else:
        raise ConfigPackageError(f"Unknown import mode: {mode}")

    return app_config, imported_ids


def _apply_tab_dict_to_tab(values: dict[str, Any], target: TabConfig) -> None:
    """Update non-identity fields of a target tab from dictionary values."""
    for key, val in _portable_tab_config(values).items():
        setattr(target, key, val)


def _apply_credentials(tab_data: TabExportData, tab_id: int, credential_store: CredentialStore) -> None:
    """Save credentials into credential store if present."""
    creds_dict = tab_data.credentials
    if not creds_dict:
        return
    creds = Credentials(
        citizen_username=str(creds_dict.get("citizen_username", "")),
        citizen_password=str(creds_dict.get("citizen_password", "")),
        egras_username=str(creds_dict.get("egras_username", "")),
        egras_password=str(creds_dict.get("egras_password", "")),
    )
    if any((creds.citizen_username, creds.citizen_password, creds.egras_username, creds.egras_password)):
        with contextlib.suppress(Exception):
            credential_store.save(creds, tab_id)
