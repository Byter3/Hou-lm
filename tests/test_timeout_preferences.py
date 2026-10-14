"""Local response-wait preferences and content-free timeout diagnostics."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import core


class TimeoutPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        patch = mock.patch.dict("os.environ", {"HOULM_CONFIG_DIR": self.temporary.name})
        patch.start()
        self.addCleanup(patch.stop)

    def test_valid_wait_round_trips_with_other_preferences_without_secrets(self):
        for minutes in (1, 10, 30):
            with self.subTest(minutes=minutes):
                core.save_preferences("https://studio.invalid/", "synthetic-model",
                                      auto_run_commands=False, response_wait_minutes=minutes)
                saved = json.loads(core.preferences_path().read_text(encoding="utf-8"))
                self.assertEqual(saved, {"server_url": "https://studio.invalid",
                                         "model_id": "synthetic-model", "auto_run_commands": False,
                                         "response_wait_minutes": minutes})
                self.assertEqual(core.load_preferences(), saved)

    def test_missing_wait_leaves_legacy_defaults_unchanged(self):
        self.assertEqual(core.load_preferences(), {"server_url": "", "model_id": ""})
        core.save_preferences("https://studio.invalid", "synthetic-model")
        self.assertNotIn("response_wait_minutes", core.load_preferences())
        core.save_preferences("https://studio.invalid", "synthetic-model", response_wait_minutes=None)
        self.assertNotIn("response_wait_minutes", core.load_preferences())

    def test_invalid_wait_cannot_replace_saved_preference(self):
        core.save_preferences("https://studio.invalid", "synthetic-model", response_wait_minutes=10)
        before = core.preferences_path().read_bytes()
        for value in (True, False, 0, -1, 31, 10.0, "10", [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    core.save_preferences("https://studio.invalid", "synthetic-model",
                                          response_wait_minutes=value)
                self.assertEqual(core.preferences_path().read_bytes(), before)

    def test_load_ignores_invalid_wait_and_unknown_secret_fields(self):
        for value in (True, False, 0, -1, 31, 10.0, "10", [], {}, None):
            with self.subTest(value=value):
                core.preferences_path().write_text(json.dumps({
                    "server_url": "https://studio.invalid", "model_id": "synthetic-model",
                    "auto_run_commands": True, "response_wait_minutes": value,
                    "api_key": "PRIVATE_CREDENTIAL", "history": "PRIVATE_SCENE",
                }), encoding="utf-8")
                self.assertEqual(core.load_preferences(), {"server_url": "https://studio.invalid",
                                                           "model_id": "synthetic-model",
                                                           "auto_run_commands": True})


class TimeoutDiagnosticTests(unittest.TestCase):
    def test_timeout_codes_and_numeric_timing_survive_without_response_content(self):
        for code in ("response_idle_timeout", "response_deadline_exceeded"):
            with self.subTest(code=code):
                error = core.ProtocolError(code, request_elapsed_ms=181000, idle_elapsed_ms=180001,
                                           idle_limit_ms=180000, response_limit_ms=600000,
                                           private_token="PRIVATE_CREDENTIAL", provider_text="PRIVATE_EXCEPTION")
                diagnostic = core.response_error_details(200, "agent", error, b"PRIVATE_SCENE")
                self.assertIn("[" + code + "]", diagnostic["message"])
                self.assertIn("Code: " + code, diagnostic["details"])
                for name, value in error.metrics.items():
                    self.assertIn("%s: %s" % (name, value), diagnostic["details"])
                self.assertEqual(set(error.metrics), {"request_elapsed_ms", "idle_elapsed_ms",
                                                      "idle_limit_ms", "response_limit_ms"})
                self.assertIn("No tools from the rejected response were executed.", diagnostic["details"])
                for private in ("PRIVATE_CREDENTIAL", "PRIVATE_EXCEPTION", "PRIVATE_SCENE"):
                    self.assertNotIn(private, str(diagnostic))

    def test_timing_diagnostics_reject_non_integer_and_bound_large_counters(self):
        error = core.ProtocolError("response_idle_timeout", request_elapsed_ms="PRIVATE_TIME",
                                   idle_elapsed_ms=True, idle_limit_ms=2 ** 80,
                                   response_limit_ms=-(2 ** 80))
        self.assertEqual(error.metrics, {"idle_limit_ms": 1_000_000_000,
                                        "response_limit_ms": -1_000_000_000})


if __name__ == "__main__":
    unittest.main()
