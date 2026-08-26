from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from core.models import Credentials
from services.credential_store import CredentialStore, credential_store_label, tab_target_name


class MacosCredentialStoreTests(unittest.TestCase):
    def test_save_uses_keychain_and_replaces_existing_tab_credential(self) -> None:
        credentials = Credentials("citizen", "secret", "egras", "egras-secret")
        success = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch("services.credential_store.sys.platform", "darwin"),
            patch("services.credential_store._run_security", return_value=success) as run,
        ):
            CredentialStore().save(credentials, tab_id=2)

        args = run.call_args.args
        self.assertEqual(args[:5], ("add-generic-password", "-a", "Saved portal logins", "-s", tab_target_name(2)))
        self.assertIn("-U", args)
        self.assertIn('"citizen_username": "citizen"', args[6])

    def test_load_reads_credentials_from_keychain(self) -> None:
        payload = '{"citizen_username": "citizen", "citizen_password": "secret"}'
        success = subprocess.CompletedProcess([], 0, payload, "")
        with (
            patch("services.credential_store.sys.platform", "darwin"),
            patch("services.credential_store._run_security", return_value=success),
        ):
            loaded = CredentialStore().load(tab_id=3)

        self.assertEqual(loaded, Credentials(citizen_username="citizen", citizen_password="secret"))

    def test_macos_store_label(self) -> None:
        with patch("services.credential_store.sys.platform", "darwin"):
            self.assertEqual(credential_store_label(), "macOS Keychain")


if __name__ == "__main__":
    unittest.main()
