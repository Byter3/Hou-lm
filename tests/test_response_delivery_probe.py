"""Response-delivery measurements remain bounded, synthetic and content-free."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("response_delivery_probe", ROOT / "scripts" / "diagnose_response_delivery.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ResponseDeliveryProbeTests(unittest.TestCase):
    def connection(self, chunks=(), status=200):
        connection = mock.Mock()
        response = connection.getresponse.return_value
        response.status = status
        response.read1.side_effect = list(chunks)
        return connection, response

    def test_complete_chunk_split_markers_and_no_private_content(self):
        connection, response = self.connection([
            b'data: {"choices":[{"delta":{"content":"PRIVATE_TEXT"},"finish_reason":"stop"}]}\r\n',
            b'\r\ndata: [DO', b'NE]\n\n', b'',
        ])
        with mock.patch.object(probe.http.client, "HTTPConnection", return_value=connection):
            report = probe.probe("http://private.example", "PRIVATE_KEY", b"PRIVATE_BODY", "chat")
        self.assertEqual(report["outcome"], "complete")
        self.assertEqual(report["terminal_kind"], "chat_finish")
        self.assertIsNotNone(report["headers_seconds"])
        self.assertIsNotNone(report["first_byte_seconds"])
        self.assertIsNotNone(report["done_seconds"])
        self.assertIsNotNone(report["eof_seconds"])
        self.assertEqual(report["wire"]["wire_done_markers"], 1)
        self.assertEqual(report["tools_executed"], 0)
        for private in ("PRIVATE", "private.example"):
            self.assertNotIn(private, json.dumps(report))
        connection.close.assert_called_once()
        response.close.assert_called_once()
        response.read.assert_not_called()
        self.assertTrue(all(0 < call.args[0] <= 45 for call in connection.sock.settimeout.call_args_list))

    def test_terminal_marker_does_not_hide_missing_eof(self):
        connection, _ = self.connection([b'data: [DONE]\n\n', TimeoutError("PRIVATE")])
        with mock.patch.object(probe.http.client, "HTTPConnection", return_value=connection):
            report = probe.probe("http://studio.example", "key", b"{}", "chat")
        self.assertEqual(report["outcome"], "deadline_exceeded")
        self.assertIsNotNone(report["terminal_seconds"])
        self.assertIsNone(report["eof_seconds"])
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_timeout_before_headers_is_distinguishable(self):
        connection, _ = self.connection()
        connection.getresponse.side_effect = TimeoutError("PRIVATE")
        with mock.patch.object(probe.http.client, "HTTPConnection", return_value=connection):
            report = probe.probe("http://studio.example", "key", b"{}", "chat")
        self.assertEqual(report["outcome"], "deadline_exceeded")
        self.assertEqual(report["http_status"], 0)
        self.assertIsNone(report["headers_seconds"])
        self.assertIsNone(report["first_byte_seconds"])

    def test_eof_without_terminal_is_not_success(self):
        connection, _ = self.connection([b'data: {"choices":[]}\n\n', b''])
        with mock.patch.object(probe.http.client, "HTTPConnection", return_value=connection):
            report = probe.probe("http://studio.example", "key", b"{}", "chat")
        self.assertEqual(report["outcome"], "eof_without_terminal")
        self.assertIsNotNone(report["eof_seconds"])

    def test_no_redirects_or_http_error_body_reads(self):
        for status, outcome in ((302, "redirect_rejected"), (401, "http_error")):
            connection, response = self.connection(status=status)
            with mock.patch.object(probe.http.client, "HTTPConnection", return_value=connection) as factory:
                report = probe.probe("http://studio.example", "key", b"{}", "chat")
            self.assertEqual(report["outcome"], outcome)
            self.assertEqual(report["http_status"], status)
            response.read1.assert_not_called()
            factory.assert_called_once()

    def test_total_deadline_decreases_across_reads(self):
        connection, response = self.connection([b':keepalive\n\n', b':keepalive\n\n'])
        # start, construction, pre-headers, headers elapsed, before read,
        # first-byte elapsed, next read -> deadline reached; final elapsed.
        with mock.patch.object(probe.http.client, "HTTPConnection", return_value=connection), \
                mock.patch.object(probe.time, "monotonic", side_effect=[0, 0, 1, 2, 3, 4, 46, 46]):
            report = probe.probe("http://studio.example", "key", b"{}", "chat")
        self.assertEqual(report["outcome"], "deadline_exceeded")
        self.assertEqual(response.read1.call_count, 1)
        self.assertEqual(connection.sock.settimeout.call_args_list[-1].args[0], 42)

    def test_main_uses_identical_synthetic_bytes_and_each_route_once(self):
        with mock.patch.object(probe.core, "load_preferences", return_value={"server_url": "https://studio.example", "model_id": "PRIVATE_MODEL"}), \
                mock.patch.object(probe.credentials, "load_key", return_value="PRIVATE_KEY"), \
                mock.patch.object(probe, "probe", return_value={"outcome": "complete"}) as run, \
                mock.patch("builtins.print") as output:
            self.assertEqual(probe.main([]), 0)
        self.assertEqual(run.call_count, 2)
        first, second = [call.args for call in run.call_args_list]
        self.assertEqual(first[2], second[2])
        self.assertEqual(json.loads(first[2]), probe.payload("PRIVATE_MODEL"))
        self.assertEqual([first[3], second[3]], ["chat", "passthrough"])
        self.assertEqual([first[4], second[4]], [45, 45])
        self.assertNotIn("PRIVATE", str(output.call_args_list))

    def test_https_and_optional_route(self):
        connection, _ = self.connection([b'data: [DONE]\n\n', b''])
        with mock.patch.object(probe.http.client, "HTTPSConnection", return_value=connection) as https:
            report = probe.probe("https://studio.example/base", "key", b"{}", "passthrough", 12)
        https.assert_called_once()
        self.assertEqual(connection.request.call_args.args[:2], ("POST", "/base/openai/chat/completions"))
        self.assertEqual(report["outcome"], "complete")
        with mock.patch.object(probe.core, "load_preferences", return_value={"server_url": "http://studio.example", "model_id": "model"}), \
                mock.patch.object(probe.credentials, "load_key", return_value="key"), \
                mock.patch.object(probe, "probe", return_value={"outcome": "complete"}) as run, \
                mock.patch("builtins.print"):
            self.assertEqual(probe.main(["--route", "passthrough", "--deadline", "12"]), 0)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[3:], ("passthrough", 12))

    def test_passthrough_strips_only_webui_controls(self):
        original = probe.payload("PRIVATE_MODEL")
        original.update(temperature=0.3, stream_options={"include_usage": True})
        body = json.dumps(original).encode()
        connection, _ = self.connection([b'data: [DONE]\n\n', b''])
        with mock.patch.object(probe.http.client, "HTTPConnection", return_value=connection):
            probe.probe("http://studio.example", "key", body, "passthrough")
        sent = json.loads(connection.request.call_args.kwargs["body"])
        self.assertEqual(sent, {key: value for key, value in original.items() if key not in ("params", "tool_ids")})
        self.assertEqual(probe.route_body(body, "chat"), body)
        self.assertIn("params", original)

    def test_full_schema_uses_real_tool_definitions_without_history(self):
        value = probe.diagnostic_payload("PRIVATE_MODEL", full_schema=True)
        self.assertEqual(value["tools"], probe.toolchat.TOOLS + [probe.toolchat.EDIT_TOOL, probe.toolchat.PYTHON_TOOL])
        self.assertEqual(len(value["messages"]), 2)
        self.assertEqual(value["messages"][1]["role"], "user")
        self.assertIn("synthetic", value["messages"][1]["content"])
        self.assertIn("never execute", value["messages"][1]["content"])
        self.assertGreater(len(json.dumps(value)), len(json.dumps(probe.payload("PRIVATE_MODEL"))))
        passthrough = json.loads(probe.route_body(json.dumps(value).encode(), "passthrough"))
        self.assertEqual(passthrough, {key: item for key, item in value.items() if key not in ("params", "tool_ids")})
        with mock.patch.object(probe.core, "load_preferences", return_value={"server_url": "http://studio.example", "model_id": "PRIVATE_MODEL"}), \
                mock.patch.object(probe.credentials, "load_key", return_value="key"), \
                mock.patch.object(probe, "probe", return_value={"outcome": "complete"}) as run, \
                mock.patch("builtins.print"):
            self.assertEqual(probe.main(["--full-schema", "--route", "chat"]), 0)
        self.assertEqual(json.loads(run.call_args.args[2]), value)


if __name__ == "__main__":
    unittest.main()
