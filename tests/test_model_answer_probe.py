"""Scene-free, bounded two-turn model-answer diagnostic with no secret output."""
import importlib.util
import json
from pathlib import Path
import unittest
import urllib.error
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("model_answer_probe", ROOT / "scripts" / "diagnose_model_answer.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def response(message, finish="stop"):
    value = mock.MagicMock()
    value.__enter__.return_value = value
    value.status = 200
    value.read.return_value = json.dumps({"choices": [{"message": message,
                                                      "finish_reason": finish}]}).encode()
    return value


def request_message(arguments='{"a":5,"b":7}'):
    return {"content": "PRIVATE_PROGRESS", "tool_calls": [{"id": "private_call_id", "type": "function",
            "function": {"name": "diagnostic_add", "arguments": arguments}}],
            "reasoning_items": [{"id": "private_state_id", "type": "reasoning",
                                 "encrypted_content": "PRIVATE_STATE", "summary": []}]}


class ModelAnswerProbeTests(unittest.TestCase):
    def test_two_requests_preserve_tool_pair_and_state_without_reporting_contents(self):
        opener = mock.Mock()
        opener.open.side_effect = [response(request_message(), "tool_calls"), response({"content": "12"})]
        with mock.patch.object(probe.urllib.request, "build_opener", return_value=opener):
            report = probe.run("http://studio.example", "model-alias", "PRIVATE_KEY")
        self.assertTrue(report["passed"])
        self.assertEqual(report["tools_executed"], 0)
        self.assertEqual(len(report["turns"]), 2)
        self.assertEqual(opener.open.call_count, 2)
        request = opener.open.call_args_list[-1].args[0]
        body = json.loads(request.data)
        self.assertEqual(body["tool_choice"], "none")
        self.assertEqual(body["messages"][-1], {"role": "tool", "tool_call_id": "private_call_id", "content": '{"sum":12}'})
        self.assertEqual(body["messages"][-2]["reasoning_items"][0]["encrypted_content"], "PRIVATE_STATE")
        self.assertEqual(request.get_header("Authorization"), "Bearer PRIVATE_KEY")
        output = json.dumps(report)
        for secret in ("PRIVATE", "private_call_id", "private_state_id", '"sum"', '"a"'):
            self.assertNotIn(secret, output)

    def test_empty_second_response_is_identified_without_retry(self):
        opener = mock.Mock()
        empty = response({"content": None})
        empty.read.return_value = b'data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        opener.open.side_effect = [response(request_message(), "tool_calls"), empty]
        with mock.patch.object(probe.urllib.request, "build_opener", return_value=opener):
            report = probe.run("http://studio.example", "model", "key")
        self.assertFalse(report["passed"])
        self.assertEqual(report["turns"][-1]["error"], "empty_response")
        self.assertEqual(report["turns"][-1]["wire"]["wire_finish_stop"], 1)
        self.assertEqual(opener.open.call_count, 2)

    def test_unexpected_or_malformed_tool_request_does_not_get_canned_result(self):
        for message in (request_message('{"a":100,"b":7}'), request_message(""), {"content": "12"}):
            opener = mock.Mock()
            opener.open.return_value = response(message)
            with mock.patch.object(probe.urllib.request, "build_opener", return_value=opener):
                report = probe.run("http://studio.example", "model", "key")
            self.assertFalse(report["passed"])
            self.assertIn("error", report["turns"][0])
            self.assertEqual(opener.open.call_count, 1)

    def test_wrong_final_answer_is_not_success_or_printed(self):
        opener = mock.Mock()
        opener.open.side_effect = [response(request_message(), "tool_calls"), response({"content": "PRIVATE_BAD_ANSWER"})]
        with mock.patch.object(probe.urllib.request, "build_opener", return_value=opener):
            report = probe.run("http://studio.example", "model", "key")
        self.assertFalse(report["passed"])
        self.assertFalse(report["turns"][-1]["answer_matches_synthetic_result"])
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_http_error_never_prints_body_and_redirects_are_disabled(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError("http://private", 401, "PRIVATE_BODY", {}, None)
        with mock.patch.object(probe.urllib.request, "build_opener", return_value=opener):
            report = probe.run("http://studio.example", "model", "PRIVATE_KEY")
        self.assertEqual(report["turns"][0]["http_status"], 401)
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, "", {}, "http://other"))

    def test_main_repeats_only_requested_number_using_saved_connection(self):
        with mock.patch.object(probe.core, "load_preferences", return_value={"server_url": "http://studio.example", "model_id": "model"}), \
                mock.patch.object(probe.credentials, "load_key", return_value="PRIVATE_KEY"), \
                mock.patch.object(probe, "run", return_value={"passed": True}) as run, \
                mock.patch("builtins.print"):
            self.assertEqual(probe.main(["--repeat", "3"]), 0)
        self.assertEqual(run.call_count, 3)


if __name__ == "__main__":
    unittest.main()
