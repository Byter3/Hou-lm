"""Synthetic HTTP probe must report structure without leaking body contents."""
import importlib.util
import json
from pathlib import Path
import unittest
import urllib.error
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("model_tools_probe", ROOT / "scripts" / "diagnose_model_tools.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def test_missing_arguments_report_without_body_or_credentials(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = json.dumps({"choices": [{"message": {
            "content": "PRIVATE_BODY", "tool_calls": [{"id": "private_call_id", "type": "function",
                "function": {"name": "diagnostic_echo", "arguments": ""}}]},
            "finish_reason": "tool_calls"}]}).encode()
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(probe.urllib.request, "build_opener", return_value=opener):
            result = probe.run("http://studio.example", "model-alias", "PRIVATE_KEY")
        self.assertEqual(result["error"], "tool_arguments_missing")
        self.assertEqual(result["tools_executed"], 0)
        output = json.dumps(result)
        self.assertNotIn("PRIVATE", output)
        self.assertNotIn("private_call_id", output)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer PRIVATE_KEY")
        self.assertNotIn("PRIVATE_KEY", request.data.decode())

    def test_synthetic_valid_call_is_checked_without_execution(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = json.dumps({"choices": [{"message": {
            "tool_calls": [{"id": "call_1", "type": "function", "function": {
                "name": "diagnostic_add", "arguments": '{"a":5,"b":7}'}}]},
            "finish_reason": "tool_calls"}]}).encode()
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(probe.urllib.request, "build_opener", return_value=opener):
            result = probe.run("http://studio.example", "model-alias", "test-key")
        self.assertTrue(result["arguments_match_synthetic_request"])
        self.assertEqual(result["parsed_tool_calls"], 1)
        self.assertEqual(result["tools_executed"], 0)

    def test_http_error_details_and_redirect_destination_are_not_exposed(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError("http://private", 401, "PRIVATE_BODY", {}, None)
        with mock.patch.object(probe.urllib.request, "build_opener", return_value=opener):
            result = probe.run("http://studio.example", "model-alias", "test-key")
        self.assertEqual(result["http_status"], 401)
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, "", {}, "http://other"))


if __name__ == "__main__":
    unittest.main()
