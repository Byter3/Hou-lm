import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import core


class CoreTests(unittest.TestCase):
    def test_context_notice_is_counted_before_request_size_validation(self):
        messages = [{"role": "user", "content": "Continue"}]
        baseline = core.build_chat_payload("central", messages)
        limit = len(json.dumps(baseline, ensure_ascii=True).encode("utf-8")) + 16
        with patch.object(core, "MAX_REQUEST_BYTES", limit), self.assertRaises(core.RequestSizeError):
            core.build_chat_payload("central", messages, context_notice="é" * 100)

    def test_malformed_history_is_not_a_request_size_error(self):
        with self.assertRaises(ValueError) as caught:
            core.build_chat_payload("central", [{"role": "user", "content": ["not text"]}])
        self.assertNotIsInstance(caught.exception, core.RequestSizeError)

    def test_protocol_errors_use_fixed_codes_and_bounded_integer_metrics(self):
        error = core.ProtocolError("too_many_tool_calls", tool_call_count=12, tool_call_limit=8,
                                   stream_index=10**20, event_number=-10**20,
                                   done_seen=True, finish_seen=1, argument_limit="SECRET",
                                   api_key="PRIVATE_KEY", model="PRIVATE_MODEL")
        self.assertIsInstance(error, ValueError)
        self.assertEqual(error.code, "too_many_tool_calls")
        self.assertEqual(error.metrics, {"tool_call_count": 12, "tool_call_limit": 8,
                                        "stream_index": 10**9, "event_number": -10**9, "finish_seen": 1})
        self.assertNotIn("SECRET", str(error))
        self.assertNotIn("PRIVATE", str(error))
        for value in ("PRIVATE_UNKNOWN_CODE", None, [], {"secret": "PRIVATE"}):
            error = core.ProtocolError(value, response_bytes=123)
            self.assertEqual(error.code, "unexpected_response")
            self.assertNotIn("PRIVATE", str(error))

    def test_all_required_protocol_codes_have_fixed_messages(self):
        codes = """response_too_large invalid_text generation_error invalid_completion invalid_message
            invalid_choices invalid_content malformed_sse stream_error invalid_tool_calls too_many_tool_calls
            invalid_tool_identity invalid_function invalid_tool_name invalid_tool_arguments tool_arguments_too_large
            tool_arguments_missing tool_arguments_invalid_json tool_arguments_not_object
            unsupported_tool_type invalid_tool_index interrupted_response incomplete_stream empty_response
            invalid_provider_state provider_state_too_large conflicting_tool_metadata trailing_stream_data
            unexpected_response""".split()
        for code in codes:
            with self.subTest(code=code):
                error = core.ProtocolError(code)
                self.assertEqual(error.code, code)
                diagnostic = core.response_error_details(200, "agent", error, b"data: [DONE]\n\n")
                self.assertIn("[" + code + "]", diagnostic["message"])
                self.assertIn("Code: " + code, diagnostic["details"])

    def test_tool_argument_diagnostics_expose_only_fixed_messages_and_numeric_locations(self):
        messages = {
            "invalid_tool_arguments": "A tool call arrived with invalid arguments.",
            "tool_arguments_missing": "A tool call arrived without JSON arguments.",
            "tool_arguments_invalid_json": "A tool call arrived with malformed JSON arguments.",
            "tool_arguments_not_object": "Tool arguments must be a JSON object.",
        }
        self.assertEqual(core.TOOL_ARGUMENT_ERROR_CODES, frozenset(messages))
        raw = b'data: {"arguments":"PRIVATE_SCENE_PATH PRIVATE_KEY"}\n\n'
        for code, message in messages.items():
            with self.subTest(code=code):
                error = core.ProtocolError(
                    code, tool_call_number=2, json_error_position=17, json_error_line=3,
                    json_error_column=8, argument_fragment_count=5, argument_characters=24,
                    tool_call_count=4, arguments="PRIVATE_ARGUMENTS", json_error_text="PRIVATE_ERROR",
                )
                self.assertEqual(str(error), message)
                result = core.response_error_details(200, "agent", error, raw)
                for field, number in (("tool_call_number", 2), ("json_error_position", 17), ("json_error_line", 3),
                                      ("json_error_column", 8), ("argument_fragment_count", 5),
                                      ("argument_characters", 24), ("tool_call_count", 4)):
                    self.assertIn("%s: %d" % (field, number), result["details"])
                self.assertNotIn("PRIVATE", str(result))
        error = core.ProtocolError("tool_arguments_invalid_json", tool_call_number="PRIVATE_CALL",
                                   json_error_position=True, json_error_line=10**20,
                                   json_error_column=-10**20, argument_fragment_count=1.5)
        self.assertEqual(error.metrics, {"json_error_line": 10**9, "json_error_column": -(10**9)})

    def test_response_diagnostics_report_only_safe_metadata(self):
        raw = b'data: {"error":{"message":"PRIVATE_SCENE_AND_API_KEY"}}\n\ndata: [DONE]\n\n'
        result = core.response_error_details(200, "agent", core.ProtocolError("stream_error", event_number=3), raw)
        self.assertEqual(set(result), {"message", "details"})
        self.assertIn("HTTP status: 200", result["details"])
        self.assertIn("Request kind: agent", result["details"])
        self.assertIn("Response format: sse", result["details"])
        self.assertIn("Response bytes: " + str(len(raw)), result["details"])
        self.assertRegex(result["details"], r"Time \(UTC\): \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
        self.assertIn("event_number: 3", result["details"])
        self.assertIn("No tools from the rejected response were executed.", result["details"])
        self.assertNotIn("PRIVATE", str(result))

    def test_delivery_phase_diagnostics_accept_only_bounded_integer_counters(self):
        error = core.ProtocolError("response_idle_timeout", request_bytes=80000,
                                   request_uploaded_bytes=-1, response_headers_ms=125,
                                   response_first_byte_ms=-1, headers="PRIVATE_HEADERS")
        result = core.response_error_details(200, "agent", error, b"")
        for field, value in (("request_bytes", 80000), ("request_uploaded_bytes", -1),
                             ("response_headers_ms", 125), ("response_first_byte_ms", -1)):
            self.assertIn("%s: %d" % (field, value), result["details"])
        self.assertNotIn("PRIVATE", str(result))
        invalid = core.ProtocolError("response_idle_timeout", request_bytes="PRIVATE_BODY",
                                     request_uploaded_bytes=True, response_headers_ms=10**20,
                                     response_first_byte_ms=1.5)
        self.assertEqual(invalid.metrics, {"response_headers_ms": 10**9})

    def test_response_diagnostics_do_not_echo_arbitrary_exception_or_request_kind(self):
        class PrivateException(ValueError):
            def __str__(self):
                raise AssertionError("Exception strings must not be inspected")
        for raw in (b'{"error":{"message":"PRIVATE_API_KEY"}}',
                    b'<html><body>PRIVATE_SCENE_DATA</body></html>',
                    b'\xffPRIVATE_HEADERS', b'data: PRIVATE_PROMPT\n\n'):
            result = core.response_error_details("PRIVATE_STATUS", "PRIVATE_REQUEST_KIND", PrivateException(), raw)
            self.assertIn("unexpected_response", result["message"])
            self.assertIn("HTTP status: unknown", result["details"])
            self.assertIn("Request kind: unknown", result["details"])
            self.assertNotIn("PRIVATE", str(result))
            self.assertNotIn("No tools from the rejected", result["details"])

    def test_response_diagnostics_revalidate_mutated_protocol_exceptions(self):
        error = core.ProtocolError("invalid_tool_index", stream_index=9)
        error.args = ("PRIVATE_EXCEPTION_MESSAGE",)
        error.metrics.update({"stream_index": "PRIVATE_INDEX", "api_key": "PRIVATE_KEY", "done_seen": False})
        result = core.response_error_details(200, "chat", error, b"PRIVATE_BODY")
        self.assertIn("invalid_tool_index", result["message"])
        self.assertNotIn("PRIVATE", str(result))
        self.assertNotIn("stream_index:", result["details"])
        self.assertNotIn("No tools from the rejected", result["details"])
        error.code = "PRIVATE_CODE"
        self.assertIn("unexpected_response", core.response_error_details(200, "chat", error, b"")["message"])

    def test_response_format_sniffing_handles_bom_sse_html_json_and_other(self):
        samples = ((b'\xef\xbb\xbf {"secret":"PRIVATE"}', "json"),
                   (b': keepalive\n\nevent: message\ndata: {"secret":"PRIVATE"}\n\n', "sse"),
                   (b'  <!DOCTYPE HTML><html>PRIVATE</html>', "html"),
                   (b'PRIVATE unstructured error', "other"))
        for raw, expected in samples:
            with self.subTest(expected=expected):
                result = core.response_error_details(200, "models", ValueError("PRIVATE_ERROR"), raw)
                self.assertIn("Response format: " + expected, result["details"])
                self.assertNotIn("PRIVATE", str(result))

    def test_protocol_diagnostics_recognize_known_json_and_sse_errors(self):
        error = {"error": {"message": "context_length_exceeded PRIVATE_PROMPT_AND_KEY"},
                 "request": {"content": "PRIVATE_SCENE"}}
        payload = json.dumps(error).encode()
        for raw, code in ((payload, "generation_error"), (b"data: " + payload + b"\n\ndata: [DONE]\n\n", "stream_error")):
            result = core.response_error_details(200, "agent", core.ProtocolError(code), raw)
            self.assertIn("model's context limit", result["message"])
            self.assertIn("Recognized server error:", result["details"])
            self.assertNotIn("HTTP 502", str(result))
            self.assertNotIn("PRIVATE", str(result))

    def test_protocol_error_classification_ignores_content_and_oversized_error_frames(self):
        text_chunk = b'data: {"choices":[{"delta":{"content":"context_length_exceeded PRIVATE_SCENE"}}]}\n\n'
        unknown_error = b'data: {"error":{"message":"PRIVATE_UNKNOWN_FAILURE"}}\n\n'
        oversized = b'data: {"error":{"message":"context_length_exceeded ' + b'x' * 65536 + b'"}}\n\n'
        beyond_scan = b": " + b"x" * core.MAX_RESPONSE_BYTES + b'\n\ndata: {"error":{"message":"context_length_exceeded"}}\n\n'
        for raw in (text_chunk + unknown_error, oversized, beyond_scan):
            result = core.response_error_details(200, "agent", core.ProtocolError("stream_error"), raw)
            self.assertNotIn("context limit", str(result))
            self.assertNotIn("Recognized server error:", result["details"])
            self.assertNotIn("PRIVATE", str(result))

    def test_url_requires_explicit_safe_base(self):
        self.assertEqual(core.normalize_server_url(" http://studio.test:8088/ "), "http://studio.test:8088")
        self.assertEqual(core.normalize_server_url("https://studio.test/chat/"), "https://studio.test/chat")
        for value in ("studio.test", "file:///tmp/key", "https://user:secret@studio.test", "https://studio.test/?key=x",
                      "https://studio.test/#key", "https://studio.test\\@evil.test", "https://studio.test/../api",
                      "https://studio.test:99999", "http://studio.test:0", "https://stu\ndio.test"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                core.normalize_server_url(value)

    def test_preferences_only_contain_non_secrets(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"HOULM_CONFIG_DIR": temp}):
            self.assertEqual(core.load_preferences(), {"server_url": "", "model_id": ""})
            core.save_preferences("https://studio.test/", "permitted-model")
            data = json.loads(core.preferences_path().read_text())
            self.assertEqual(set(data), {"server_url", "model_id"})
            self.assertEqual(core.load_preferences(), data)
            core.preferences_path().write_text('{"token": "do-not-load", "server_url": "javascript:bad"}')
            self.assertEqual(core.load_preferences()["server_url"], "")

    def test_models_keep_server_ids_and_remove_duplicates(self):
        data = {"data": [{"id": "central", "name": "Studio"}, {"id": "central"}, {"id": None}, "bad"]}
        self.assertEqual(core.parse_models(data), [{"id": "central", "name": "Studio"}])

    def test_text_only_bounded_payload(self):
        payload = core.build_chat_payload("central", [{"role": "user", "content": "Hello"}])
        self.assertEqual(payload["model"], "central")
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["tool_ids"], [])
        self.assertEqual(payload["messages"][-1]["content"], "Hello")
        for message in ({"role": "user", "content": [{"type": "image_url"}]}, {"role": "tool", "content": "x"},
                        {"role": "user", "content": "x" * core.MAX_REQUEST_BYTES}):
            with self.assertRaises(ValueError):
                core.build_chat_payload("central", [message])

    def test_json_and_sse(self):
        payload = {"choices": [{"message": {"content": "OK"}}]}
        self.assertEqual(core.parse_chat_body(json.dumps(payload).encode()), "OK")
        events = [
            {"choices": [{"delta": {"role": "assistant", "content": "O"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "K"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"total_tokens": 2}},
        ]
        stream = "".join("data: " + json.dumps(item) + "\r\n\r\n" for item in events) + "data: [DONE]\r\n\r\n"
        self.assertEqual(core.parse_chat_body(stream.encode()), "OK")

    def test_partial_stream_is_not_reported_as_success(self):
        partial = b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
        with self.assertRaisesRegex(ValueError, "completion marker"):
            core.parse_chat_body(partial)

    def test_errors_do_not_echo_server_secrets(self):
        with self.assertRaises(ValueError) as caught:
            core.parse_chat_body(b'{"error":{"message":"Authorization: Bearer secret"}}')
        self.assertNotIn("secret", str(caught.exception))
        with self.assertRaises(ValueError):
            core.parse_chat_response({"choices": [{"message": {"tool_calls": [{"id": "t1"}]}}]})

    def test_http_error_recognizes_missing_annotations_without_echoing_body(self):
        upstream = {"error": {
            "message": "Required property 'annotations' is missing. Authorization: Bearer private-token",
            "param": "input[2].content[0]",
            "type": "invalid_request_error",
        }, "input": "PRIVATE_SCENE_DATA", "request_url": "https://studio.test/?token=private-token"}
        for wrapper in (upstream, {"detail": upstream}, {"detail": json.dumps(upstream)}):
            with self.subTest(wrapper_type=type(wrapper.get("detail")).__name__):
                result = core.sanitized_http_error(400, json.dumps(wrapper).encode(), secrets=("private-token",))
                self.assertIn("annotations", result.lower())
                self.assertIn("400", result)
                for sensitive in ("private-token", "PRIVATE_SCENE_DATA", "Authorization", "https://studio.test"):
                    self.assertNotIn(sensitive, result)

    def test_http_error_explains_content_type_validation(self):
        body = {"error": {"message": "Invalid value: 'input_text'. Supported values are: 'output_text' and 'refusal'."}}
        result = core.sanitized_http_error(400, json.dumps(body).encode())
        self.assertIn("input_text", result)
        self.assertIn("output_text", result)

    def test_http_error_unknown_and_malformed_bodies_stay_private(self):
        for raw in (
            b'{"detail":"SERVER_SECRET_MUST_NOT_APPEAR Authorization: Bearer private-token"}',
            b'<html>SERVER_SECRET_MUST_NOT_APPEAR</html>',
            b'\xff\xfeSERVER_SECRET_MUST_NOT_APPEAR',
            json.dumps({"detail": [{"msg": "Unrecognized validation error", "input": "PRIVATE_SCENE_DATA"}]}).encode(),
        ):
            with self.subTest(raw=raw[:16]):
                result = core.sanitized_http_error(400, raw, secrets=("private-token",))
                self.assertIn("400", result)
                for sensitive in ("SERVER_SECRET", "private-token", "PRIVATE_SCENE_DATA", "Authorization"):
                    self.assertNotIn(sensitive, result)

    def test_http_error_does_not_classify_echoed_request_content(self):
        raw = json.dumps({"error": {"message": "Unrecognized error"}, "input": "Required property 'annotations' is missing"}).encode()
        self.assertNotIn("annotations", core.sanitized_http_error(400, raw))

    def test_empty_responses_output_is_classified_without_exposing_server_dump(self):
        raw = json.dumps({"detail": "litellm.APIConnectionError: ChatgptException - "
                          "Unknown items in responses API response: []. "
                          "Received Model Group=PRIVATE_MODEL "
                          "Authorization: Bearer private-token"}).encode()
        for status in (400, 500):
            result = core.server_error_details(status, raw, secrets=("private-token",))
            self.assertIn("LiteLLM could not assemble", result)
            self.assertIn("streaming", result)
            for secret in ("PRIVATE_MODEL", "private-token", "Authorization"):
                self.assertNotIn(secret, result)

    def test_error_details_never_echo_unknown_exception_text(self):
        raw = b'{"detail":"PRIVATE_SCENE_DATA and arbitrary server secrets"}'
        result = core.server_error_details(400, raw)
        self.assertIn("HTTP 400", result)
        self.assertNotIn("PRIVATE_SCENE_DATA", result)
        self.assertNotIn("arbitrary server secrets", result)


if __name__ == "__main__":
    unittest.main()
