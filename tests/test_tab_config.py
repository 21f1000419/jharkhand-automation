from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import ConfigStore, TabConfig
from core.models import UiEvent
from services.credential_store import TARGET_NAME, tab_target_name


class TabConfigTests(unittest.TestCase):
    def test_browser_count_is_persisted_and_clamped(self) -> None:
        tab = TabConfig.from_dict(
            {"browser_count": 200}, tab_id=1, profile_number=1
        )
        self.assertEqual(tab.browser_count, 20)

        invalid = TabConfig.from_dict(
            {"browser_count": "invalid"}, tab_id=1, profile_number=1
        )
        self.assertEqual(invalid.browser_count, 1)

    def test_legacy_settings_migrate_to_tab_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "chrome_executable": "chrome.exe",
                        "last_article": "AFFIDAVIT",
                        "last_mode": "continuous",
                    }
                ),
                encoding="utf-8",
            )

            config = ConfigStore(path).load()

            self.assertEqual(config.get_tab(1).display_name, "ID 1")
            self.assertEqual(config.get_tab(1).last_article, "AFFIDAVIT")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("tabs", saved)
            self.assertNotIn("last_article", saved)

    def test_new_tab_has_blank_inputs_and_monotonic_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "settings.json")
            config = store.load()
            config.get_tab(1).last_article = "old"
            first = store.create_tab(config)
            second = config.create_tab()

            self.assertEqual(first.display_name, "ID 2")
            self.assertEqual(second.display_name, "ID 3")
            self.assertEqual((first.profile_number, second.profile_number), (2, 3))
            self.assertEqual(second.last_article, "")
            self.assertEqual(second.last_csv_path, "")
            self.assertTrue(second.portal_profile_path)

    def test_copy_run_settings_preserves_target_profile_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigStore(Path(directory) / "settings.json").load()
            source = config.get_tab(1)
            source.last_article = "LEASE"
            source.last_csv_path = "source.csv"
            source.sms_user_id = "citizen-1"
            source.payment_trigger_method = "POST"
            target = config.create_tab()
            target.enabled = False
            identity = (target.tab_id, target.profile_number, target.portal_profile_path)

            target.copy_run_settings_from(source)

            self.assertEqual(target.last_article, "LEASE")
            self.assertEqual(target.last_csv_path, "source.csv")
            self.assertEqual(target.sms_user_id, "citizen-1")
            self.assertEqual(target.payment_trigger_method, "POST")
            self.assertFalse(target.enabled)
            self.assertEqual(
                (target.tab_id, target.profile_number, target.portal_profile_path),
                identity,
            )

    def test_reset_portal_profile_repairs_an_imported_path_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            imported_path = root / "imported-profile"
            imported_path.mkdir()
            expected_path = root / "portal-profiles" / "profile-3"
            tab = TabConfig.new(3, 3)
            tab.portal_profile_path = str(imported_path)

            with (
                patch("core.config.app_data_directory", return_value=root),
                patch("core.config.default_portal_profile_path", return_value=expected_path),
            ):
                profile_path, result = tab.reset_portal_profile()

            self.assertEqual(profile_path, expected_path)
            self.assertEqual(result, "path_reset")
            self.assertEqual(tab.portal_profile_path, str(expected_path))
            self.assertTrue(imported_path.is_dir())

    def test_reset_portal_profile_deletes_the_correct_local_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected_path = Path(directory) / "portal-profiles" / "profile-3"
            expected_path.mkdir(parents=True)
            (expected_path / "browser-data").write_text("temporary profile", encoding="utf-8")
            tab = TabConfig.new(3, 3)
            tab.portal_profile_path = str(expected_path)

            with (
                patch("core.config.app_data_directory", return_value=Path(directory)),
                patch("core.config.default_portal_profile_path", return_value=expected_path),
            ):
                profile_path, result = tab.reset_portal_profile()

            self.assertEqual(profile_path, expected_path)
            self.assertEqual(result, "deleted")
            self.assertFalse(expected_path.exists())

    def test_tab_settings_are_the_persisted_source_of_truth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "settings.json")
            config = store.load()
            config.get_tab(1).last_article = "LEASE"

            store.save(config)

            self.assertEqual(store.load().get_tab(1).last_article, "LEASE")

    def test_transaction_export_path_is_persisted_for_all_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "settings.json")
            config = store.load()
            config.transaction_export_path = str(Path(directory) / "transactions.csv")

            store.save(config)

            self.assertEqual(store.load().transaction_export_path, config.transaction_export_path)

    def test_removed_id_and_its_profile_are_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigStore(Path(directory) / "settings.json").load()
            removed = config.create_tab()
            config.tabs.remove(removed)

            replacement = config.create_tab()

            self.assertEqual(replacement.tab_id, removed.tab_id)
            self.assertEqual(replacement.profile_number, removed.profile_number)
            self.assertEqual(replacement.portal_profile_path, removed.portal_profile_path)

    def test_duplicate_loaded_profiles_are_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            shared_profile = str(Path(directory) / "shared-profile")
            settings.write_text(
                json.dumps(
                    {
                        "tabs": [
                            {
                                "tab_id": 1,
                                "profile_number": 1,
                                "portal_profile_path": shared_profile,
                            },
                            {
                                "tab_id": 2,
                                "profile_number": 1,
                                "portal_profile_path": shared_profile,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            config = ConfigStore(settings).load()

            first, second = config.tabs
            self.assertNotEqual(first.profile_number, second.profile_number)
            self.assertNotEqual(first.portal_profile_path, second.portal_profile_path)

    def test_existing_event_constructor_still_accepts_data_as_third_argument(self) -> None:
        event = UiEvent("log", "message", {"level": "INFO"})
        self.assertEqual(event.run_id, "")
        self.assertEqual(event.data["level"], "INFO")

    def test_credential_targets_are_scoped_and_legacy_target_is_stable(self) -> None:
        self.assertEqual(tab_target_name(2), f"{TARGET_NAME}/tab-2")


if __name__ == "__main__":
    unittest.main()
