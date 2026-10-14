"""Tests for the offline diagnostic's fixtures and verdicts (no LiteLLM needed)."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace as Obj
import unittest


_SPEC = importlib.util.spec_from_file_location(
    "houlm_litellm_probe", Path(__file__).resolve().parents[1] / "scripts" / "diagnose_litellm_stream.py"
)
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def chunk(index=0, name=None, arguments=None, finish=None, content=None):
    calls = [] if arguments is None else [Obj(index=index, function=Obj(name=name, arguments=arguments))]
    return Obj(choices=[Obj(finish_reason=finish, delta=Obj(tool_calls=calls, content=content))])


class LiteLLMStreamProbeTests(unittest.TestCase):
    def test_fixtures_cover_done_only_and_interleaved_with_terminal_event(self):
        cases = list(probe.fixtures())
        self.assertEqual(len(cases), 8)
        self.assertIn("interleaved_done_only", [name for name, _, _ in cases])
        for _, events, expected in cases:
            self.assertEqual(events[0]["type"], "response.created")
            self.assertEqual(events[-1]["type"], "response.completed")
            self.assertTrue(expected)

    def test_missing_arguments_are_reported_without_fabrication(self):
        result = probe.evaluate([chunk(name="probe_1", arguments=""), chunk(finish="tool_calls")],
                                {"probe_1": '{"a":5}'})
        self.assertFalse(result["passed"])
        self.assertEqual(result["argument_characters"], 0)
        self.assertTrue(result["terminal_finish_matches"])

    def test_interleaved_fragments_are_assembled_per_index(self):
        result = probe.evaluate([
            chunk(0, "probe_1", ""), chunk(1, "probe_3", ""),
            chunk(0, arguments='{"a":'), chunk(1, arguments='{"b":'),
            chunk(0, arguments='5}'), chunk(1, arguments='7}'), chunk(finish="tool_calls"),
        ], {"probe_1": '{"a":5}', "probe_3": '{"b":7}'})
        self.assertTrue(result["passed"])

    def test_duplicate_arguments_and_premature_finishes_fail(self):
        result = probe.evaluate([
            chunk(name="probe_1", arguments='{"a":5}'), chunk(finish="tool_calls"),
            chunk(name="probe_1", arguments='{"a":5}'), chunk(finish="tool_calls"),
        ], {"probe_1": '{"a":5}'})
        self.assertFalse(result["passed"])
        self.assertFalse(result["arguments_match"])
        self.assertFalse(result["terminal_finish_matches"])

    def test_exception_report_omits_exception_message(self):
        class Unavailable:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("sensitive exception detail")

        results = probe.probe(Unavailable, Unavailable)
        self.assertEqual(len(results), 2 * (len(list(probe.fixtures())) + len(list(probe.text_fixtures()))))
        self.assertTrue(all(r["exception_type"] == "RuntimeError" for r in results))
        self.assertNotIn("sensitive", str(results))

    def test_text_fixtures_cover_terminal_only_partial_and_repeated_snapshots(self):
        cases = list(probe.text_fixtures())
        names = {name for name, _, _ in cases}
        self.assertTrue({"text_done_only", "text_item_done_only", "text_completed_only",
                         "text_partial_delta_then_done", "text_repeated_snapshots",
                         "text_multiple_content_parts", "text_sparse_message_items"} <= names)
        for _, events, expected in cases:
            self.assertEqual(events[0]["type"], "response.created")
            self.assertEqual(events[-1]["type"], "response.completed")
            self.assertTrue(expected)

    def test_missing_and_duplicate_text_fail_without_exposing_content(self):
        for chunks in ([chunk(finish="stop")],
                       [chunk(content="PRIVATE_ANSWER"), chunk(content="PRIVATE_ANSWER", finish="stop")]):
            result = probe.evaluate_text(chunks, "PRIVATE_ANSWER")
            self.assertFalse(result["passed"])
            self.assertNotIn("PRIVATE", str(result))
            self.assertEqual(result["expected_text_characters"], len("PRIVATE_ANSWER"))

    def test_text_fragments_match_and_require_one_stop_and_no_tools(self):
        chunks = [chunk(content="Probe "), chunk(content="answer.", finish="stop")]
        self.assertTrue(probe.evaluate_text(chunks, "Probe answer.")["passed"])
        for extra in (chunk(finish="stop"), chunk(name="probe", arguments="{}")):
            self.assertFalse(probe.evaluate_text(chunks + [extra], "Probe answer.")["passed"])


if __name__ == "__main__":
    unittest.main()
