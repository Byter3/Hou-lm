"""Execution consent is an explicit boolean in local workstation settings."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import core


class CommandPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        patch = mock.patch.dict("os.environ", {"HOULM_CONFIG_DIR": self.temporary.name})
        patch.start()
        self.addCleanup(patch.stop)

    def test_boolean_consent_round_trips_with_connection_changes_and_no_secrets(self):
        for enabled in (False, True, False):
            with self.subTest(enabled=enabled):
                core.save_preferences("https://studio.invalid/", "synthetic-model", auto_run_commands=enabled)
                saved = json.loads(core.preferences_path().read_text(encoding="utf-8"))
                self.assertEqual(saved, {"server_url": "https://studio.invalid", "model_id": "synthetic-model",
                                         "auto_run_commands": enabled})
                self.assertEqual(core.load_preferences(), saved)
        core.save_preferences("https://other.invalid", "another-model", auto_run_commands=True)
        self.assertIs(core.load_preferences()["auto_run_commands"], True)

    def test_truthy_strings_numbers_and_containers_cannot_enable_execution(self):
        for value in ("true", "false", 1, 0, [], {}, None):
            with self.subTest(value=value):
                core.preferences_path().write_text(json.dumps({"server_url": "https://studio.invalid",
                    "model_id": "synthetic-model", "auto_run_commands": value, "api_key": "not-a-real-secret"}), encoding="utf-8")
                loaded = core.load_preferences()
                self.assertNotIn("auto_run_commands", loaded)
                self.assertNotIn("api_key", loaded)
                if value is not None:
                    with self.assertRaises(ValueError):
                        core.save_preferences("https://studio.invalid", "synthetic-model", auto_run_commands=value)

    def test_legacy_preferences_do_not_acquire_automatic_permission(self):
        core.save_preferences("https://studio.invalid", "synthetic-model")
        self.assertNotIn("auto_run_commands", core.load_preferences())


if __name__ == "__main__":
    unittest.main()
