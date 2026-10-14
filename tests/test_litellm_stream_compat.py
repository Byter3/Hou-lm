"""Optional integration tests against the exact installed LiteLLM package.

Run using the disposable LiteLLM environment; the ordinary Houdini suite skips
these when LiteLLM is absent. No credentials, HTTP requests, or model inference.
"""

import asyncio
import copy
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compat = load("houlm_stream_compat", ROOT / "gateway" / "litellm_stream_compat.py")
probe = load("houlm_stream_compat_probe", ROOT / "scripts" / "diagnose_litellm_stream.py")


@unittest.skipUnless(importlib.util.find_spec("litellm"), "optional LiteLLM integration environment required")
class LiteLLMStreamCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
        cls.version, cls.converter, cls.config = compat.verify_installation()

    def adapter(self):
        return compat.ResponsesToolStreamRepair(self.converter(iter(()), sync_stream=True).chunk_parser)

    def test_all_diagnostic_fixtures(self):
        for name, events, expected in probe.fixtures():
            with self.subTest(name=name):
                adapter = self.adapter()
                result = probe.evaluate((adapter.parse(copy.deepcopy(e)) for e in events), expected)
                self.assertTrue(result["passed"], result)

    def test_done_only_without_opening_can_use_complete_item_identity(self):
        adapter = self.adapter()
        result = probe.evaluate([
            adapter.parse(probe._closed(3, '{"value":"beta"}')),
            adapter.parse({"type": "response.completed", "response": {"output": []}}),
        ], {"probe_3": '{"value":"beta"}'})
        self.assertTrue(result["passed"])

    def test_terminal_output_can_supply_missing_tail(self):
        _, events, expected = list(probe.fixtures())[0]
        events = [events[0], events[1], probe._delta(1, '{"value":'), events[-1]]
        adapter = self.adapter()
        self.assertTrue(probe.evaluate((adapter.parse(e) for e in events), expected)["passed"])

    def test_terminal_output_uses_call_id_when_array_indices_differ(self):
        _, events, expected = list(probe.fixtures())[5]
        events = [events[0], events[1], events[2], events[-1]]
        adapter = self.adapter()
        self.assertTrue(probe.evaluate((adapter.parse(e) for e in events), expected)["passed"])

    def test_conflicting_completion_is_rejected_without_argument_text(self):
        adapter = self.adapter()
        adapter.parse(probe._added(1))
        adapter.parse(probe._delta(1, '{"private_code":'))
        with self.assertRaises(compat.ToolStreamError) as caught:
            adapter.parse(probe._done(1, '{"different_private_code":5}'))
        self.assertNotIn("private_code", str(caught.exception))

    def test_incomplete_and_nonobject_final_arguments_rejected(self):
        for args in ('{"value":', '[]', 'null', ''):
            with self.subTest(args=args):
                adapter = self.adapter()
                adapter.parse(probe._added(1))
                with self.assertRaises(compat.ToolStreamError):
                    adapter.parse(probe._done(1, args))

    def test_changed_identity_and_reused_call_id_are_rejected(self):
        for event in [probe._closed(1, '{}'), probe._added(2)]:
            adapter = self.adapter()
            adapter.parse(probe._added(1))
            event["item"]["call_id"] = "call_houlm_probe_1" if event["output_index"] == 2 else "different"
            with self.assertRaises(compat.ToolStreamError):
                adapter.parse(event)

    def test_arguments_after_completion_rejected(self):
        adapter = self.adapter()
        adapter.parse(probe._added(1))
        adapter.parse(probe._done(1, '{}'))
        with self.assertRaises(compat.ToolStreamError):
            adapter.parse(probe._delta(1, 'extra'))

    def test_optional_response_item_id_can_be_omitted(self):
        adapter = self.adapter()
        added = probe._added(1)
        del added["item"]["id"]
        adapter.parse(added)
        result = adapter.parse(probe._done(1, '{"a":1}'))
        self.assertEqual(result.choices[0].delta.tool_calls[0].function.arguments, '{"a":1}')
        adapter.parse(probe._closed(1, '{"a":1}'))

    def test_argument_budgets_are_enforced(self):
        adapter = self.adapter()
        adapter.parse(probe._added(1))
        with mock.patch.object(compat, "MAX_ARGUMENT_CHARACTERS", 8):
            with self.assertRaises(compat.ToolStreamError):
                adapter.parse(probe._delta(1, '012345678'))

    def test_independent_streams_do_not_share_state(self):
        a, b = self.adapter(), self.adapter()
        a.parse(probe._added(1))
        b.parse(probe._added(1))
        a.parse(probe._done(1, '{"a":1}'))
        b.parse(probe._done(1, '{"b":2}'))
        self.assertEqual(a.calls[1]["arguments"], '{"a":1}')
        self.assertEqual(b.calls[1]["arguments"], '{"b":2}')

    def test_non_tool_content_passthrough_and_stable_response_id(self):
        adapter = self.adapter()
        created = adapter.parse({"type": "response.created", "response": {"id": "resp_probe"}})
        text = adapter.parse({"type": "response.output_text.delta", "output_index": 0,
                              "content_index": 0, "item_id": "msg_probe", "delta": "Hello"})
        done = adapter.parse({"type": "response.completed", "response": {"output": []}})
        self.assertEqual(text.choices[0].delta.content, "Hello")
        self.assertEqual(created.id, text.id)
        self.assertEqual(done.choices[0].finish_reason, "stop")

    def test_unrecognized_version_or_source_refuses_install(self):
        with mock.patch.object(compat.importlib.metadata, "version", return_value="0.0.unverified"):
            with self.assertRaises(compat.CompatibilityError):
                compat.install()
        with mock.patch.object(compat, "SUPPORTED", {self.version: ("incorrect",) * 3}):
            with self.assertRaises(compat.CompatibilityError):
                compat.install()

    def test_attachment_rejects_unknown_wrapper(self):
        with self.assertRaises(compat.CompatibilityError):
            compat.attach(SimpleNamespace(completion_stream=iter(())), self.converter)

    def test_attachment_is_idempotent_and_does_not_change_class_parser(self):
        original = self.converter.chunk_parser
        stream = SimpleNamespace(completion_stream=self.converter(iter(()), sync_stream=True))
        compat.attach(stream, self.converter)
        first = stream.completion_stream.chunk_parser
        compat.attach(stream, self.converter)
        self.assertEqual(stream.completion_stream.chunk_parser, first)
        self.assertIs(self.converter.chunk_parser, original)

    def _wrapper(self, source):
        from litellm.litellm_core_utils.litellm_logging import Logging
        from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
        logger = Logging(model="gpt-4o", messages=[], stream=True, call_type="completion",
                         start_time=datetime.now(), litellm_call_id="houlm-offline-probe",
                         function_id="houlm-offline-probe")
        return CustomStreamWrapper(completion_stream=source, model="gpt-4o", logging_obj=logger,
                                   custom_llm_provider="chatgpt")

    def test_full_wrapper_conflict_error_omits_argument_body(self):
        events = [probe._added(1), probe._delta(1, '{"PRIVATE_ARGUMENT_SENTINEL":'),
                  probe._done(1, '{"OTHER_PRIVATE_ARGUMENT_SENTINEL":5}')]
        source = self.converter(iter("data: " + json.dumps(e) for e in events), sync_stream=True)
        stream = compat.attach(self._wrapper(source), self.converter)
        with self.assertRaises(Exception) as caught:
            list(stream)
        self.assertNotIn("PRIVATE_ARGUMENT_SENTINEL", str(caught.exception))
        self.assertIn("completed_arguments_conflict", str(caught.exception))

    def test_full_wrapper_async_conflict_error_omits_argument_body(self):
        async def run():
            async def events():
                for event in [probe._added(1), probe._delta(1, '{"PRIVATE_ARGUMENT_SENTINEL":'),
                              probe._done(1, '{"OTHER_PRIVATE_ARGUMENT_SENTINEL":5}')]:
                    yield "data: " + json.dumps(event)
            source = self.converter(events(), sync_stream=False)
            stream = compat.attach(self._wrapper(source), self.converter)
            with self.assertRaises(Exception) as caught:
                [chunk async for chunk in stream]
            self.assertNotIn("PRIVATE_ARGUMENT_SENTINEL", str(caught.exception))
            self.assertIn("completed_arguments_conflict", str(caught.exception))
        asyncio.run(run())

    def test_full_wrapper_failed_incomplete_and_eof_cannot_finish_successfully(self):
        for kind in ("response.failed", "response.incomplete", "error", None):
            with self.subTest(kind=kind):
                events = [probe._added(1), probe._delta(1, '{"a":1}')]
                if kind:
                    events.append({"type": kind, "response": {"output": []}})
                source = self.converter(iter("data: " + json.dumps(e) for e in events), sync_stream=True)
                stream = compat.attach(self._wrapper(source), self.converter)
                with self.assertRaises(Exception) as caught:
                    list(stream)
                self.assertIn("Hou-lm tool stream rejected", str(caught.exception))

    def test_full_wrapper_async_failed_incomplete_and_eof_are_rejected(self):
        async def run():
            for kind in ("response.failed", "response.incomplete", "error", None):
                with self.subTest(kind=kind):
                    async def events():
                        raw = [probe._added(1), probe._delta(1, '{"a":1}')]
                        if kind:
                            raw.append({"type": kind, "response": {"output": []}})
                        for event in raw:
                            yield "data: " + json.dumps(event)
                    source = self.converter(events(), sync_stream=False)
                    stream = compat.attach(self._wrapper(source), self.converter)
                    with self.assertRaises(Exception) as caught:
                        [chunk async for chunk in stream]
                    self.assertIn("Hou-lm tool stream rejected", str(caught.exception))
        asyncio.run(run())

    def test_full_upstream_wrapper_sync_and_install_scope(self):
        original = self.config.post_stream_processing
        unchanged = self.converter.chunk_parser
        try:
            compat.install()
            compat.install()
            for name, events, expected in probe.fixtures():
                with self.subTest(name=name):
                    source = self.converter(iter("data: " + json.dumps(e) for e in events), sync_stream=True)
                    wrapper = self._wrapper(source)
                    stream = self.config.post_stream_processing(None, wrapper)
                    result = probe.evaluate(stream, expected)
                    self.assertTrue(result["passed"], result)
            self.assertIs(self.converter.chunk_parser, unchanged)
        finally:
            self.config.post_stream_processing = original

    def test_full_upstream_wrapper_async(self):
        async def run():
            for name, events, expected in probe.fixtures():
                async def raw_events():
                    for event in events:
                        yield "data: " + json.dumps(event)
                source = self.converter(raw_events(), sync_stream=False)
                stream = compat.attach(self._wrapper(source), self.converter)
                chunks = [chunk async for chunk in stream]
                self.assertTrue(probe.evaluate(chunks, expected)["passed"], name)
        asyncio.run(run())


def text_event(kind, text, output_index=2, content_index=0, item_id="msg_text_probe"):
    event = {"type": "response.output_text." + kind, "output_index": output_index,
             "content_index": content_index, "item_id": item_id}
    event["delta" if kind == "delta" else "text"] = text
    return event


def text_item(text, item_id="msg_text_probe"):
    return {"type": "message", "id": item_id, "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def text_completed(items=()):
    return {"type": "response.completed", "response": {"id": "resp_text_probe", "output": list(items)}}


@unittest.skipUnless(importlib.util.find_spec("litellm"), "optional LiteLLM integration environment required")
class LiteLLMTextStreamCompatTests(unittest.TestCase):
    setUpClass = classmethod(LiteLLMStreamCompatTests.setUpClass.__func__)
    adapter = LiteLLMStreamCompatTests.adapter
    _wrapper = LiteLLMStreamCompatTests._wrapper

    @staticmethod
    def visible(chunks):
        return "".join(chunk.choices[0].delta.content or "" for chunk in chunks)

    def converted(self, events):
        adapter = self.adapter()
        return [adapter.parse(copy.deepcopy(event)) for event in events]

    @staticmethod
    def done_variants(text):
        item = text_item(text)
        yield "text", [text_event("done", text), text_completed()]
        yield "part", [{"type": "response.content_part.done", "output_index": 2,
                        "content_index": 0, "item_id": item["id"], "part": item["content"][0]},
                       text_completed()]
        yield "item", [{"type": "response.output_item.done", "output_index": 2, "item": item},
                       text_completed()]
        yield "response", [text_completed([item])]

    def test_verified_original_loses_text_when_only_done_events_exist(self):
        # This is an offline reproduction against fingerprint-verified upstream,
        # not a claim about which events an artist's live provider sent.
        parser = self.converter(iter(()), sync_stream=True).chunk_parser
        for name, events in self.done_variants("Fresh authored values: 24 and 0.8."):
            with self.subTest(level=name):
                self.assertEqual(self.visible(parser(copy.deepcopy(e)) for e in events), "")

    def test_done_only_visible_text_is_preserved_at_every_completion_level(self):
        answer = "Steps: 24; Width: 0.8. \u0394 = 2 \U0001f30a"
        for name, events in self.done_variants(answer):
            with self.subTest(level=name):
                chunks = self.converted(events)
                self.assertEqual(self.visible(chunks), answer)
                self.assertEqual(chunks[-1].choices[0].finish_reason, "stop")

    def test_partial_delta_recovers_exact_suffix_at_every_completion_level(self):
        answer = "Repeated phrase, repeated phrase. \u00e1\u00e9\u0151"
        for name, events in self.done_variants(answer):
            with self.subTest(level=name):
                chunks = self.converted([text_event("delta", answer[:11])] + events)
                self.assertEqual(self.visible(chunks), answer)

    def test_normal_deltas_and_repeated_snapshots_never_duplicate_text(self):
        answer = "ABABAB \u03b1\u03b2"
        item = text_item(answer)
        events = [text_event("delta", answer[:4]), text_event("delta", answer[4:]),
                  text_event("done", answer), text_event("done", answer),
                  {"type": "response.content_part.done", "output_index": 2, "content_index": 0,
                   "item_id": item["id"], "part": item["content"][0]},
                  {"type": "response.output_item.done", "output_index": 2, "item": item},
                  text_completed([item])]
        chunks = self.converted(events)
        self.assertEqual(self.visible(chunks), answer)
        self.assertEqual([c.choices[0].delta.content for c in chunks[:2]], [answer[:4], answer[4:]])

    def test_terminal_lookup_uses_item_id_instead_of_compacted_output_position(self):
        item = text_item("Read two parameters.")
        events = [text_event("delta", "Read ", output_index=7), text_completed([item])]
        self.assertEqual(self.visible(self.converted(events)), "Read two parameters.")

    def test_multiple_sequential_content_parts_and_output_items_are_preserved(self):
        first = text_item("First. ")
        first["content"].append({"type": "output_text", "text": "Second. ", "annotations": []})
        second = text_item("Third.", "msg_second")
        events = [text_event("done", "First. "), text_event("done", "Second. ", content_index=1),
                  text_event("done", "Third.", output_index=5, item_id="msg_second"),
                  text_completed([first, second])]
        self.assertEqual(self.visible(self.converted(events)), "First. Second. Third.")

    def test_terminal_only_multiple_parts_are_emitted_in_content_order(self):
        item = text_item("First. ")
        item["content"].append({"type": "output_text", "text": "Second.", "annotations": []})
        self.assertEqual(self.visible(self.converted([text_completed([item])])), "First. Second.")

    def test_conflicting_text_prefix_and_identity_are_rejected_without_content(self):
        for conflicting in [text_event("done", "PRIVATE_REPLACEMENT"),
                            text_event("done", "PRIVATE_PREFIX tail", item_id="msg_wrong"),
                            text_event("delta", "tail", item_id="msg_wrong")]:
            with self.subTest(event=conflicting["type"]):
                adapter = self.adapter()
                adapter.parse(text_event("delta", "PRIVATE_PREFIX"))
                with self.assertRaises(compat.ToolStreamError) as caught:
                    adapter.parse(conflicting)
                self.assertNotIn("PRIVATE_PREFIX", str(caught.exception))
                self.assertNotIn("PRIVATE_REPLACEMENT", str(caught.exception))

    def test_completed_part_cannot_change_or_receive_more_text(self):
        for event in [text_event("done", "final tail"), text_event("delta", "tail")]:
            adapter = self.adapter()
            adapter.parse(text_event("done", "final"))
            with self.assertRaises(compat.ToolStreamError):
                adapter.parse(event)

    def test_completed_message_cannot_gain_a_new_content_part(self):
        adapter = self.adapter()
        adapter.parse({"type": "response.output_item.done", "output_index": 2, "item": text_item("final")})
        with self.assertRaises(compat.ToolStreamError):
            adapter.parse(text_event("done", "new part", content_index=1))

    def test_completed_stream_cannot_receive_more_text_or_another_terminal(self):
        for event in [text_event("delta", "extra"), text_completed()]:
            adapter = self.adapter()
            adapter.parse(text_completed([text_item("final")]))
            with self.assertRaises(compat.ToolStreamError):
                adapter.parse(event)

    def test_completed_event_cannot_hide_an_incomplete_response_or_message_status(self):
        for response_status, message_status in [("incomplete", "completed"), ("completed", "in_progress")]:
            item = text_item("Partial answer")
            item["status"] = message_status
            event = text_completed([item])
            event["response"]["status"] = response_status
            with self.assertRaises(compat.ToolStreamError):
                self.adapter().parse(event)

    def test_late_suffix_for_an_earlier_part_cannot_reorder_visible_answer(self):
        adapter = self.adapter()
        adapter.parse(text_event("delta", "First"))
        adapter.parse(text_event("done", "Second", content_index=1))
        with self.assertRaises(compat.ToolStreamError):
            adapter.parse(text_event("done", "First tail"))

    def test_invalid_text_indices_and_values_are_rejected(self):
        for field, value in [("output_index", -1), ("output_index", True), ("content_index", -1),
                             ("content_index", True), ("content_index", "0"), ("text", None)]:
            event = text_event("done", "answer")
            event[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(compat.ToolStreamError):
                self.adapter().parse(event)

    def test_reasoning_and_refusal_are_not_promoted_to_visible_output_text(self):
        reasoning = {"type": "reasoning", "id": "rs_probe", "summary": [
            {"type": "summary_text", "text": "PRIVATE_REASONING"}], "encrypted_content": "opaque"}
        refusal = text_item("unused")
        refusal["content"] = [{"type": "refusal", "refusal": "REFUSAL_SENTINEL"}]
        final = text_item("Actual visible answer", "msg_answer")
        events = [{"type": "response.reasoning_summary_text.delta", "summary_index": 0,
                   "output_index": 0, "item_id": "rs_probe", "delta": "PRIVATE_REASONING"},
                  {"type": "response.output_item.done", "output_index": 0, "item": reasoning},
                  text_completed([reasoning, refusal, final])]
        self.assertEqual(self.visible(self.converted(events)), "Actual visible answer")

    def test_non_assistant_message_is_not_promoted_to_visible_answer(self):
        user = text_item("UNTRUSTED_USER_MESSAGE", "msg_user")
        user["role"] = "user"
        answer = text_item("Visible answer")
        with self.assertRaises(compat.ToolStreamError) as caught:
            self.converted([text_completed([user, answer])])
        self.assertNotIn("UNTRUSTED_USER_MESSAGE", str(caught.exception))

    def test_text_and_tool_calls_can_share_the_same_completed_chunk(self):
        _, tool_events, expected = list(probe.fixtures())[0]
        terminal = copy.deepcopy(tool_events[-1])
        terminal["response"]["output"].append(text_item("I will inspect these values."))
        chunks = self.converted(tool_events[:2] + [text_event("delta", "I will ", output_index=4), terminal])
        self.assertTrue(probe.evaluate(chunks, expected)["passed"])
        self.assertEqual(self.visible(chunks), "I will inspect these values.")
        self.assertTrue(chunks[-1].choices[0].delta.tool_calls)
        self.assertEqual(chunks[-1].choices[0].delta.content, "inspect these values.")
        self.assertEqual(chunks[-1].choices[0].finish_reason, "tool_calls")

    def test_text_limits_bound_parts_per_part_and_aggregate_state(self):
        with mock.patch.object(compat, "MAX_TEXT_CHARACTERS", 8):
            with self.assertRaises(compat.ToolStreamError):
                self.adapter().parse(text_event("done", "123456789"))
            adapter = self.adapter()
            adapter.parse(text_event("delta", "12345"))
            with self.assertRaises(compat.ToolStreamError):
                adapter.parse(text_event("delta", "6789"))
        with mock.patch.object(compat, "MAX_TOTAL_TEXT_CHARACTERS", 8):
            adapter = self.adapter()
            adapter.parse(text_event("done", "12345"))
            with self.assertRaises(compat.ToolStreamError):
                adapter.parse(text_event("done", "6789", content_index=1))
        with mock.patch.object(compat, "MAX_TEXT_PARTS", 1):
            adapter = self.adapter()
            adapter.parse(text_event("done", "first"))
            with self.assertRaises(compat.ToolStreamError):
                adapter.parse(text_event("done", "second", content_index=1))

    def test_separate_streams_do_not_share_text_or_completion_state(self):
        a, b = self.adapter(), self.adapter()
        ac = [a.parse(text_event("delta", "A")), a.parse(text_event("done", "Alpha"))]
        bc = [b.parse(text_event("delta", "B")), b.parse(text_event("done", "Beta"))]
        self.assertEqual(self.visible(ac), "Alpha")
        self.assertEqual(self.visible(bc), "Beta")

    def test_full_wrapper_sync_recovers_done_only(self):
        for name, events in self.done_variants("Fresh values: 24 and 0.8."):
            with self.subTest(level=name):
                source = self.converter(iter("data: " + json.dumps(e) for e in events), sync_stream=True)
                chunks = list(compat.attach(self._wrapper(source), self.converter))
                self.assertEqual(self.visible(chunks), "Fresh values: 24 and 0.8.")

    @unittest.skipUnless((ROOT / "houdini" / "python3.11libs" / "houlm" / "toolchat.py").is_file(),
                         "panel parser is tested in the full checkout, not the server-only bundle")
    def test_panel_parses_repaired_final_answer(self):
        import sys
        sys.path.insert(0, str(ROOT / "houdini" / "python3.11libs"))
        from houlm import toolchat
        for name, events in self.done_variants("Fresh values: 24 and 0.8."):
            with self.subTest(level=name):
                source = self.converter(iter("data: " + json.dumps(e) for e in events), sync_stream=True)
                chunks = list(compat.attach(self._wrapper(source), self.converter))
                wire = "".join("data: " + c.model_dump_json() + "\n\n" for c in chunks) + "data: [DONE]\n\n"
                reply = toolchat.parse_reply(wire.encode("utf-8"))
                self.assertEqual(reply["content"], "Fresh values: 24 and 0.8.")
                self.assertEqual(reply["tool_calls"], [])

    def test_full_wrapper_async_recovers_completed_text(self):
        async def run():
            for name, fixture in self.done_variants("Fresh values: 24 and 0.8."):
                with self.subTest(level=name):
                    async def events():
                        for event in fixture:
                            yield "data: " + json.dumps(event)
                    source = self.converter(events(), sync_stream=False)
                    chunks = [c async for c in compat.attach(self._wrapper(source), self.converter)]
                    self.assertEqual(self.visible(chunks), "Fresh values: 24 and 0.8.")
        asyncio.run(run())

    def test_full_wrapper_text_failure_incomplete_and_eof_cannot_finish_successfully(self):
        for kind in ("response.failed", "response.incomplete", "error", None):
            with self.subTest(kind=kind):
                events = [text_event("delta", "Partial visible answer")]
                if kind:
                    events.append({"type": kind, "response": {"output": []}})
                source = self.converter(iter("data: " + json.dumps(e) for e in events), sync_stream=True)
                with self.assertRaises(Exception) as caught:
                    list(compat.attach(self._wrapper(source), self.converter))
                self.assertIn("Hou-lm tool stream rejected", str(caught.exception))

    def test_full_wrapper_async_text_failure_incomplete_and_eof_are_rejected(self):
        async def run():
            for kind in ("response.failed", "response.incomplete", "error", None):
                with self.subTest(kind=kind):
                    async def events():
                        yield "data: " + json.dumps(text_event("delta", "Partial visible answer"))
                        if kind:
                            yield "data: " + json.dumps({"type": kind, "response": {"output": []}})
                    source = self.converter(events(), sync_stream=False)
                    with self.assertRaises(Exception) as caught:
                        [c async for c in compat.attach(self._wrapper(source), self.converter)]
                    self.assertIn("Hou-lm tool stream rejected", str(caught.exception))
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
