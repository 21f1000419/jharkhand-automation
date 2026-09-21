from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock

from core.config import AppConfig, TabConfig
from services.config_package import (
    ConfigPackageError,
    TabExportData,
    apply_imported_package,
    create_export_package,
    decrypt_config_data,
    encrypt_config_data,
    export_package_to_file,
    import_package_from_file,
)


class ConfigPackageTests(TestCase):
    def test_encrypt_and_decrypt_roundtrip(self) -> None:
        payload = {
            "version": 1,
            "app_name": "eStampAutomation",
            "tabs": [
                {
                    "tab_config": {"tab_id": 1, "sms_user_id": "test-user"},
                    "credentials": {"citizen_username": "user123", "citizen_password": "pwd"},
                    "credentials_saved": True,
                }
            ],
        }
        encrypted = encrypt_config_data(payload)
        self.assertIsInstance(encrypted, bytes)
        self.assertTrue(encrypted.startswith(b"ESTAMPCFG\x01\x00"))

        decrypted = decrypt_config_data(encrypted)
        self.assertEqual(decrypted, payload)

    def test_decrypt_with_wrong_key_fails(self) -> None:
        payload = {"data": "secret_info"}
        encrypted = encrypt_config_data(payload, secret_key=b"KeyA")
        with self.assertRaises(ConfigPackageError):
            decrypt_config_data(encrypted, secret_key=b"KeyB")

    def test_tampered_ciphertext_fails_authentication(self) -> None:
        payload = {"data": "authentic_data"}
        encrypted = bytearray(encrypt_config_data(payload))
        # Flip a bit in the ciphertext portion
        encrypted[-1] ^= 0xFF
        with self.assertRaises(ConfigPackageError):
            decrypt_config_data(bytes(encrypted))

    def test_truncated_blob_fails(self) -> None:
        with self.assertRaises(ConfigPackageError):
            decrypt_config_data(b"ESTAMPCFG")

    def test_invalid_magic_fails(self) -> None:
        with self.assertRaises(ConfigPackageError):
            decrypt_config_data(b"BADMAGIC____" + b"\x00" * 100)

    def test_file_export_and_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "subfolder" / "test_config.estampcfg"
            payload = {"version": 1, "note": "hello world", "tabs": []}
            exported = export_package_to_file(file_path, payload)
            self.assertTrue(exported.is_file())

            imported = import_package_from_file(exported)
            self.assertEqual(imported, payload)

    def test_create_export_package(self) -> None:
        app_config = AppConfig(
            chrome_executable="C:\\chrome.exe",
            chrome_profile_path="C:\\profile",
            debug_port=9347,
        )
        tab_data = TabExportData(
            tab_config=TabConfig.new(1, 1).to_dict(),
            credentials={"citizen_username": "citizen_user", "citizen_password": "pass"},
            credentials_saved=True,
        )
        package = create_export_package(app_config, [tab_data])
        self.assertEqual(package["version"], 2)
        self.assertNotIn("global_config", package)
        self.assertEqual(len(package["tabs"]), 1)
        self.assertEqual(package["tabs"][0]["credentials"]["citizen_username"], "citizen_user")
        self.assertNotIn("tab_id", package["tabs"][0]["tab_config"])
        self.assertNotIn("portal_profile_path", package["tabs"][0]["tab_config"])
        self.assertNotIn("profile_number", package["tabs"][0]["tab_config"])

    def test_apply_imported_package_replace_mode(self) -> None:
        cred_store = MagicMock()
        app_config = AppConfig(tabs=[TabConfig.new(1, 1)])
        package = {
            "version": 1,
            "global_config": {
                "chrome_executable": "C:\\Custom\\chrome.exe",
                "chrome_profile_path": "C:\\Custom\\profile",
                "debug_port": 9999,
                "transaction_export_path": "C:\\tx.csv",
            },
            "tabs": [
                {
                    "tab_config": {
                        "tab_id": 99,
                        "profile_number": 1,
                        "sms_user_id": "sms1",
                        "last_mode": "continuous",
                    },
                    "credentials": {"citizen_username": "user1", "citizen_password": "pwd1"},
                    "credentials_saved": True,
                },
                {
                    "tab_config": {
                        "tab_id": 2,
                        "profile_number": 2,
                        "sms_user_id": "sms2",
                        "last_mode": "assisted",
                    },
                    "credentials": {"egras_username": "egras2", "egras_password": "pwd2"},
                    "credentials_saved": True,
                },
            ],
        }

        updated_config, imported_ids = apply_imported_package(package, app_config, cred_store, mode="replace")
        self.assertEqual(imported_ids, [1, 2])
        self.assertEqual(len(updated_config.tabs), 2)
        self.assertNotEqual(updated_config.chrome_executable, "C:\\Custom\\chrome.exe")
        self.assertEqual(updated_config.get_tab(1).sms_user_id, "sms1")
        self.assertEqual(updated_config.run_config.last_mode, "assisted")
        self.assertEqual(updated_config.get_tab(2).sms_user_id, "sms2")
        self.assertEqual(cred_store.save.call_count, 2)

    def test_old_package_profile_paths_are_ignored_and_local_paths_are_retained(self) -> None:
        cred_store = MagicMock()
        local_tab = TabConfig.new(1, 7)
        local_tab.portal_profile_path = "C:\\Local\\portal-profile-7"
        app_config = AppConfig(
            chrome_profile_path="C:\\Local\\ocr-profile",
            tabs=[local_tab],
        )
        package = {
            "version": 1,
            "global_config": {"chrome_profile_path": "C:\\Imported\\ocr-profile"},
            "tabs": [
                {
                    "tab_config": {
                        "tab_id": 1,
                        "profile_number": 99,
                        "portal_profile_path": "C:\\Imported\\portal-profile",
                        "sms_user_id": "imported-sms",
                    }
                }
            ],
        }

        updated_config, imported_ids = apply_imported_package(package, app_config, cred_store, mode="replace")

        self.assertEqual(imported_ids, [1])
        self.assertEqual(updated_config.chrome_profile_path, "C:\\Local\\ocr-profile")
        self.assertEqual(updated_config.get_tab(1).profile_number, 7)
        self.assertEqual(updated_config.get_tab(1).portal_profile_path, "C:\\Local\\portal-profile-7")
        self.assertEqual(updated_config.get_tab(1).sms_user_id, "imported-sms")

    def test_apply_imported_package_merge_mode(self) -> None:
        cred_store = MagicMock()
        existing_tab = TabConfig.new(1, 1)
        existing_tab.sms_user_id = "original-tab-1"
        app_config = AppConfig(tabs=[existing_tab])

        package = {
            "version": 1,
            "tabs": [
                {
                    "tab_config": {"tab_id": 1, "profile_number": 1, "sms_user_id": "imported-tab"},
                    "credentials": {"citizen_username": "imp_user", "citizen_password": "p"},
                    "credentials_saved": True,
                }
            ],
        }

        updated_config, imported_ids = apply_imported_package(package, app_config, cred_store, mode="merge")
        # Should merge as a new tab ID (2)
        self.assertEqual(imported_ids, [2])
        self.assertEqual(len(updated_config.tabs), 2)
        self.assertEqual(updated_config.get_tab(1).sms_user_id, "original-tab-1")
        self.assertEqual(updated_config.get_tab(2).sms_user_id, "imported-tab")
        cred_store.save.assert_called_once()
        saved_creds, saved_id = cred_store.save.call_args[0]
        self.assertEqual(saved_id, 2)
        self.assertEqual(saved_creds.citizen_username, "imp_user")

    def test_apply_imported_package_single_tab_mode(self) -> None:
        cred_store = MagicMock()
        tab1 = TabConfig.new(1, 1)
        tab2 = TabConfig.new(2, 2)
        tab2.sms_user_id = "old-sms"
        app_config = AppConfig(tabs=[tab1, tab2])

        package = {
            "version": 1,
            "tabs": [
                {
                    "tab_config": {"sms_user_id": "new-sms-applied", "last_mode": "continuous"},
                    "credentials": {"citizen_username": "new_citizen", "citizen_password": "np"},
                    "credentials_saved": True,
                }
            ],
        }

        updated_config, imported_ids = apply_imported_package(
            package, app_config, cred_store, mode="single_tab", target_tab_id=2
        )
        self.assertEqual(imported_ids, [2])
        self.assertEqual(updated_config.get_tab(2).sms_user_id, "new-sms-applied")
        self.assertEqual(updated_config.run_config.last_mode, "assisted")
        # Profile path and numbers preserved
        self.assertEqual(updated_config.get_tab(2).tab_id, 2)
        self.assertEqual(updated_config.get_tab(2).profile_number, 2)
        cred_store.save.assert_called_once()

    def test_apply_imported_package_validation_errors(self) -> None:
        cred_store = MagicMock()
        app_config = AppConfig()
        # Empty tabs in package
        with self.assertRaises(ConfigPackageError):
            apply_imported_package({"tabs": []}, app_config, cred_store, mode="replace")

        # Invalid mode
        with self.assertRaises(ConfigPackageError):
            apply_imported_package(
                {"tabs": [{"tab_config": {}}]}, app_config, cred_store, mode="invalid_mode"
            )

        # Single tab mode without target_tab_id
        with self.assertRaises(ConfigPackageError):
            apply_imported_package({"tabs": [{"tab_config": {}}]}, app_config, cred_store, mode="single_tab")

    def test_nonexistent_file_import_raises_error(self) -> None:
        with self.assertRaises(ConfigPackageError):
            import_package_from_file(Path("non_existent_file.estampcfg"))

    def test_tab_export_data_serialization(self) -> None:
        data = TabExportData(
            tab_config={"tab_id": 3, "last_mode": "assisted"},
            credentials={"egras_username": "egras_user"},
            credentials_saved=True,
        )
        as_dict = data.to_dict()
        reconstructed = TabExportData.from_dict(as_dict)
        self.assertEqual(reconstructed.tab_config, data.tab_config)
        self.assertEqual(reconstructed.credentials, data.credentials)
        self.assertTrue(reconstructed.credentials_saved)
