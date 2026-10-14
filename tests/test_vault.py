"""Encryption at rest: PCE credentials in config.yaml and IP addresses in tracker.db are AES-256-GCM
encrypted, and plain-text values from before encryption are rewritten encrypted.
Run with: python -m unittest discover tests"""
import base64
import os
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import pce  # noqa: E402
import vault  # noqa: E402

KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()


class VaultTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"TRACKER_SECRET_KEY": KEY})
        patcher.start()
        self.addCleanup(patcher.stop)
        vault._key = None
        self.addCleanup(setattr, vault, "_key", None)

    def test_round_trip_with_a_fresh_nonce_each_time(self):
        a, b = vault.encrypt("s3cret", "config.api_secret"), vault.encrypt("s3cret", "config.api_secret")
        self.assertTrue(a.startswith("enc:v1:"))
        self.assertNotEqual(a, b)
        self.assertNotIn("s3cret", a)
        self.assertEqual(vault.decrypt(a, "config.api_secret"), "s3cret")

    def test_value_is_bound_to_its_field_and_tampering_is_detected(self):
        sealed = vault.encrypt("s3cret", "config.api_secret")
        with self.assertRaises(vault.VaultError):
            vault.decrypt(sealed, "config.api_key")
        tampered = sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1]
        with self.assertRaises(vault.VaultError):
            vault.decrypt(tampered, "config.api_secret")
        self.assertEqual(vault.reveal(tampered, "config.api_secret"), "(encrypted)")

    def test_wrong_key_does_not_decrypt(self):
        sealed = vault.encrypt("s3cret", "config.api_secret")
        vault._key = bytes(32)
        with self.assertRaises(vault.VaultError):
            vault.decrypt(sealed, "config.api_secret")

    def test_only_ip_addresses_are_encrypted_in_hostnames(self):
        self.assertEqual(vault.encrypt_if_ip("web01.example.com", "workloads.hostname"), "web01.example.com")
        for ip in ("192.168.101.30", "10.0.0.1", "fe80::1"):
            sealed = vault.encrypt_if_ip(ip, "workloads.hostname")
            self.assertNotIn(ip, sealed)
            self.assertEqual(vault.decrypt(sealed, "workloads.hostname"), ip)

    def test_plain_text_passes_through(self):
        self.assertEqual(vault.decrypt("web01", "workloads.hostname"), "web01")
        self.assertEqual(vault.encrypt("", "config.api_key"), "")

    def test_config_credentials_are_encrypted_and_plain_files_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w") as fh:
                yaml.safe_dump({"default_pce": "lab", "lab": {"pce_fqdn": "10.1.2.3", "port": 8443, "org": 1,
                                                              "api_key": "api_123", "api_secret": "topsecret"}}, fh)
            with mock.patch.object(pce, "CONFIG_PATH", path):
                cfg = pce.load_config()
                self.assertEqual((cfg["lab"]["api_key"], cfg["lab"]["api_secret"], cfg["lab"]["pce_fqdn"]),
                                 ("api_123", "topsecret", "10.1.2.3"))
                with open(path) as fh:
                    text = fh.read()
                for plain in ("api_123", "topsecret", "10.1.2.3"):
                    self.assertNotIn(plain, text)
                self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
                self.assertEqual(pce.load_config(), cfg)

    def test_undecryptable_credentials_are_blank_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with mock.patch.object(pce, "CONFIG_PATH", path):
                pce.save_config({"default_pce": "lab", "lab": {"pce_fqdn": "pce.example.com", "api_key": "k", "api_secret": "s"}})
                vault._key = bytes(32)
                cfg = pce.load_config()
                self.assertEqual((cfg["lab"]["api_key"], cfg["lab"]["api_secret"]), ("", ""))
                with open(path) as fh:
                    self.assertIn("enc:v1:", fh.read())   # left as it was, not overwritten


if __name__ == "__main__":
    unittest.main()
