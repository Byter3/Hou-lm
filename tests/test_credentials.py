import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import credentials


SERVER = "https://studio.example.test:8088"
OTHER = "https://other.example.test:8088"
KEY = "synthetic-test-api-key-do-not-use"


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Never read or write the logged-in user's real credential directory.
        self.directory = mock.patch.object(credentials, "_credentials_dir", return_value=self.root)
        self.directory.start()
        self.addCleanup(self.directory.stop)

    def fake_crypto(self):
        store = {}

        def crypt(data, entropy, decrypt=False):
            if decrypt:
                try:
                    return store[(data, entropy)]
                except KeyError:
                    raise OSError(credentials._STORAGE_ERROR) from None
            ciphertext = ("synthetic-ciphertext-%d" % len(store)).encode()
            store[(ciphertext, entropy)] = data
            return ciphertext
        return mock.patch.object(credentials, "_crypt", side_effect=crypt)

    def test_missing_and_delete_missing_are_harmless(self):
        self.assertEqual(credentials.load_key(SERVER), "")
        self.assertIsNone(credentials.delete_key(SERVER))

    def test_atomic_roundtrip_update_delete_and_exact_server_scope(self):
        with self.fake_crypto():
            credentials.save_key(SERVER + "/", KEY)
            self.assertEqual(credentials.load_key(" " + SERVER + "/ "), KEY)
            self.assertEqual(credentials.load_key(OTHER), "")
            self.assertEqual(credentials.load_key(SERVER + "/other"), "")
            self.assertEqual(credentials.load_key(SERVER.replace("https", "http")), "")
            self.assertNotIn(KEY.encode(), credentials._path(SERVER).read_bytes())
            self.assertEqual(len(list(self.root.iterdir())), 1)
            credentials.save_key(SERVER, "replacement-synthetic-key")
            self.assertEqual(credentials.load_key(SERVER), "replacement-synthetic-key")
            credentials.delete_key(SERVER)
            self.assertEqual(credentials.load_key(SERVER), "")

    def test_encrypted_blob_cannot_be_copied_to_another_server(self):
        with self.fake_crypto():
            credentials.save_key(SERVER, KEY)
            credentials._path(OTHER).write_bytes(credentials._path(SERVER).read_bytes())
            with self.assertRaisesRegex(OSError, "securely"):
                credentials.load_key(OTHER)

    def test_decrypted_envelope_must_match_server_and_schema(self):
        credentials._path(SERVER).write_bytes(credentials._MAGIC + b"ciphertext")
        for envelope in (
            {"version": 1, "server": OTHER, "key": KEY},
            {"version": True, "server": SERVER, "key": KEY},
            {"version": 1, "server": SERVER, "key": KEY, "extra": KEY},
            {"version": 1, "server": SERVER, "key": "bad\nkey"},
            [KEY],
        ):
            with mock.patch.object(credentials, "_crypt", return_value=json.dumps(envelope).encode()):
                with self.assertRaises(ValueError) as caught:
                    credentials.load_key(SERVER)
                self.assertEqual(str(caught.exception), credentials._INVALID_ERROR)

    def test_corrupt_and_oversized_storage_never_reaches_decryption(self):
        for data in (b"", b"plaintext-" + KEY.encode(), b"x" * (credentials._MAX_FILE_BYTES + 1)):
            credentials._path(SERVER).write_bytes(data)
            with mock.patch.object(credentials, "_crypt") as crypt:
                with self.assertRaises(ValueError) as caught:
                    credentials.load_key(SERVER)
                self.assertNotIn(KEY, str(caught.exception))
                crypt.assert_not_called()

    def test_failure_does_not_replace_existing_login_or_leave_temp_files(self):
        with self.fake_crypto():
            credentials.save_key(SERVER, KEY)
            before = credentials._path(SERVER).read_bytes()
            with mock.patch.object(credentials.os, "replace", side_effect=OSError(KEY)):
                with self.assertRaises(OSError) as caught:
                    credentials.save_key(SERVER, "replacement-synthetic-key")
                self.assertEqual(str(caught.exception), credentials._STORAGE_ERROR)
            self.assertEqual(credentials._path(SERVER).read_bytes(), before)
            self.assertEqual(list(self.root.glob("*.tmp")), [])
            self.assertEqual(credentials.load_key(SERVER), KEY)

    def test_input_validation_never_echoes_credentials(self):
        for key in ("", "bad key", "bad\nkey", "x" * (credentials._MAX_KEY_CHARS + 1), None):
            with self.assertRaises(ValueError):
                credentials.save_key(SERVER, key)
        for server in ("https://user:" + KEY + "@example.test", SERVER + "?key=" + KEY, None):
            for operation in (credentials.load_key, credentials.delete_key):
                with self.assertRaises(ValueError) as caught:
                    operation(server)
                self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_os_errors_are_fixed_without_path_or_secret_text(self):
        for operation in (credentials.load_key, credentials.delete_key):
            with mock.patch.object(credentials, "_path", side_effect=OSError(KEY)):
                with self.assertRaises(OSError) as caught:
                    operation(SERVER)
                self.assertEqual(str(caught.exception), credentials._STORAGE_ERROR)

    def test_non_windows_has_no_plaintext_fallback(self):
        with mock.patch.object(credentials.sys, "platform", "linux"):
            with self.assertRaises(OSError):
                credentials.save_key(SERVER, KEY)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_storage_location_ignores_project_preferences_override(self):
        self.directory.stop()
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.root), "HOULM_CONFIG_DIR": str(self.root / "project")}):
            self.assertEqual(credentials._credentials_dir(), self.root / "Hou-lm" / "credentials")
        self.directory.start()

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI only")
    def test_real_current_user_dpapi_roundtrip_and_entropy_binding(self):
        credentials.save_key(SERVER, KEY)
        self.assertEqual(credentials.load_key(SERVER), KEY)
        encrypted = credentials._path(SERVER).read_bytes()
        self.assertNotIn(KEY.encode(), encrypted)
        self.assertNotIn(SERVER.encode(), encrypted)
        credentials._path(OTHER).write_bytes(encrypted)
        with self.assertRaises(OSError):
            credentials.load_key(OTHER)
        credentials.delete_key(SERVER)
        self.assertEqual(credentials.load_key(SERVER), "")


if __name__ == "__main__":
    unittest.main()
