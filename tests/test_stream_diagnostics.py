import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import core, stream_diagnostics


def sse(chunks):
    return ("".join("data: " + json.dumps(chunk) + "\r\n\r\n" for chunk in chunks)
            + "data: [DONE]\r\n\r\n").encode()


def call(arguments):
    return {"id": "PRIVATE_ID", "type": "function", "function": {"name": "PRIVATE_TOOL", "arguments": arguments}}


class StreamDiagnosticTests(unittest.TestCase):
    def test_empty_delta_and_populated_message_fields_are_distinct(self):
        raw = sse([
            {"choices": [{"delta": {"tool_calls": [call("")]}}]},
            {"choices": [{"message": {"tool_calls": [call("PRIVATE_ARGUMENTS")]}, "finish_reason": "tool_calls"}]},
        ])
        metrics = stream_diagnostics.analyze(raw)
        self.assertEqual(metrics["wire_event_count"], 2)
        self.assertEqual(metrics["wire_done_markers"], 1)
        self.assertEqual(metrics["wire_delta_argument_fields"], 1)
        self.assertEqual(metrics["wire_delta_argument_chars"], 0)
        self.assertEqual(metrics["wire_message_argument_fields"], 1)
        self.assertEqual(metrics["wire_message_argument_chars"], len("PRIVATE_ARGUMENTS"))
        self.assertEqual(set(metrics), set(stream_diagnostics.METRIC_NAMES))
        self.assertTrue(all(type(value) is int for value in metrics.values()))
        self.assertNotIn("PRIVATE", str(metrics))

    def test_responses_locations_and_other_choices_are_observed_not_executed(self):
        item = {"type": "function_call", "arguments": "PRIVATE", "name": "PRIVATE"}
        raw = sse([
            {"type": "response.function_call_arguments.delta", "delta": "PRIVATE"},
            {"type": "response.function_call_arguments.done", "arguments": "PRIVATE"},
            {"type": "response.output_item.done", "item": item},
            {"type": "response.completed", "response": {"output": [item, {"type": "message", "content": "PRIVATE"}]}},
            {"choices": [{"delta": {}}, {"delta": {"tool_calls": [call("PRIVATE")]}}]},
            {"type": "PRIVATE_EVENT", "PRIVATE_KEY": "PRIVATE_VALUE"},
        ])
        metrics = stream_diagnostics.analyze(raw)
        self.assertEqual(metrics["wire_responses_events"], 4)
        for key in ("wire_responses_delta_chars", "wire_responses_done_chars",
                    "wire_responses_item_argument_chars", "wire_responses_completed_argument_chars",
                    "wire_other_choice_argument_chars"):
            self.assertEqual(metrics[key], 7)
        self.assertEqual(metrics["wire_unknown_events"], 1)
        self.assertEqual(metrics["wire_delta_argument_chars"], 0)
        self.assertNotIn("PRIVATE", str(metrics))

    def test_json_completion_uses_the_same_fixed_counters(self):
        raw = json.dumps({"choices": [{"message": {"tool_calls": [call("{}")]}}]}).encode()
        metrics = stream_diagnostics.analyze(raw)
        self.assertEqual(metrics["wire_chat_message_events"], 1)
        self.assertEqual(metrics["wire_message_argument_chars"], 2)
        self.assertEqual(metrics["wire_done_markers"], 0)

    def test_empty_stop_and_reasoning_only_completion_are_distinguishable(self):
        empty = stream_diagnostics.analyze(sse([
            {"choices": [{"delta": {"content": ""}, "finish_reason": "stop"}]},
        ]))
        reasoning = stream_diagnostics.analyze(sse([
            {"choices": [{"delta": {"content": "", "reasoning_items": [
                {"type": "reasoning", "id": "PRIVATE_ID", "encrypted_content": "PRIVATE_ENCRYPTED",
                 "summary": [{"type": "summary_text", "text": "PRIVATE_REASONING"}]}
            ]}, "finish_reason": "stop"}]},
        ]))
        for metrics in (empty, reasoning):
            self.assertEqual(metrics["wire_event_count"], 1)
            self.assertEqual(metrics["wire_finish_stop"], 1)
            self.assertEqual(metrics["wire_delta_content_chars"], 0)
            self.assertEqual(metrics["wire_delta_argument_chars"], 0)
            self.assertNotIn("PRIVATE", str(metrics))
        self.assertEqual(empty["wire_chat_reasoning_events"], 0)
        self.assertEqual(reasoning["wire_chat_reasoning_events"], 1)

    def test_content_reasoning_refusal_and_finish_reasons_are_fixed_counters(self):
        chunks = [
            {"choices": [{"delta": {"content": "PRIVATE", "reasoning_content": "PRIVATE_REASONING"}}]},
            {"choices": [{"message": {"content": [
                {"type": "output_text", "text": "PRIVATE"},
                {"type": "text", "text": "PRIVATE"},
                {"type": "PRIVATE_TYPE", "text": "PRIVATE_IGNORED"},
                {"type": "text", "text": {"PRIVATE": "IGNORED"}},
            ], "refusal": "PRIVATE_REFUSAL"}, "finish_reason": "tool_calls"}]},
        ]
        chunks.extend({"choices": [{"delta": {}, "finish_reason": reason}]}
                      for reason in ("length", "content_filter", "PRIVATE_REASON", {"PRIVATE": "PRIVATE"}))
        metrics = stream_diagnostics.analyze(sse(chunks))
        self.assertEqual(metrics["wire_delta_content_chars"], 7)
        self.assertEqual(metrics["wire_message_content_chars"], 14)
        self.assertEqual(metrics["wire_chat_reasoning_events"], 1)
        self.assertEqual(metrics["wire_chat_refusal_events"], 1)
        self.assertEqual(metrics["wire_finish_tool_calls"], 1)
        self.assertEqual(metrics["wire_finish_length"], 1)
        self.assertEqual(metrics["wire_finish_content_filter"], 1)
        self.assertEqual(metrics["wire_finish_other"], 2)
        self.assertTrue(all(type(value) is int for value in metrics.values()))
        self.assertNotIn("PRIVATE", str(metrics))

    def test_responses_text_snapshots_and_terminal_events_are_observed(self):
        metrics = stream_diagnostics.analyze(sse([
            {"type": "response.output_text.delta", "delta": "PRIVATE"},
            {"type": "response.output_text.done", "text": "PRIVATE_FINAL"},
            {"type": "response.completed", "response": {"output": [
                {"type": "message", "content": [{"type": "output_text", "text": "PRIVATE_FINAL"}]},
                {"type": "reasoning", "encrypted_content": "PRIVATE_ENCRYPTED"},
                None,
            ]}},
            {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "PRIVATE"}}},
            {"type": "response.failed", "error": {"message": "PRIVATE"}},
        ]))
        self.assertEqual(metrics["wire_responses_text_delta_chars"], 7)
        self.assertEqual(metrics["wire_responses_text_done_chars"], 13)
        self.assertEqual(metrics["wire_responses_completed_text_chars"], 13)
        self.assertEqual(metrics["wire_responses_completed_reasoning_items"], 1)
        self.assertEqual(metrics["wire_responses_incomplete_events"], 1)
        self.assertEqual(metrics["wire_responses_failed_events"], 1)
        self.assertEqual(metrics["wire_delta_content_chars"], 0)
        self.assertNotIn("PRIVATE", str(metrics))

    def test_malformed_or_unrecognized_data_is_never_echoed_or_raised(self):
        for raw in (b'data: PRIVATE\n\n', b'\xffPRIVATE', b'data: ["PRIVATE"]\n\n',
                    b'data: {"choices":[null,{"delta":1}]}\n\n', b'PRIVATE'):
            with self.subTest(raw=raw):
                metrics = stream_diagnostics.analyze(raw)
                self.assertEqual(set(metrics), set(stream_diagnostics.METRIC_NAMES))
                self.assertTrue(all(type(value) is int for value in metrics.values()))
                self.assertNotIn("PRIVATE", str(metrics))

    def test_non_string_arguments_are_not_stringified(self):
        raw = sse([{"choices": [{"delta": {"tool_calls": [call({"PRIVATE": "PRIVATE"}), call(None), call(3)]}}]}])
        metrics = stream_diagnostics.analyze(raw)
        self.assertEqual(metrics["wire_delta_argument_fields"], 0)
        self.assertEqual(metrics["wire_delta_argument_chars"], 0)

    def test_input_limit_and_invalid_types_are_bounded(self):
        metrics = stream_diagnostics.analyze(b"x" * (stream_diagnostics.MAX_SCAN_BYTES + 1))
        self.assertEqual(metrics["wire_scan_truncated"], 1)
        self.assertEqual(metrics["wire_event_count"], 0)
        for raw in (None, "PRIVATE", object(), {"PRIVATE": "PRIVATE"}):
            self.assertTrue(all(value == 0 for value in stream_diagnostics.analyze(raw).values()))

    def test_error_details_include_safe_counts_for_agent_only(self):
        raw = sse([{"choices": [{"message": {"tool_calls": [call("PRIVATE")]}}]}])
        error = core.ProtocolError("tool_arguments_missing")
        agent = core.response_error_details(200, "agent", error, raw)
        self.assertIn("wire_message_argument_chars: 7", agent["details"])
        self.assertNotIn("PRIVATE", str(agent))
        ordinary = core.response_error_details(200, "chat", error, raw)
        self.assertNotIn("wire_", ordinary["details"])


if __name__ == "__main__":
    unittest.main()
