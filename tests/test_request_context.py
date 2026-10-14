import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import core, request_context, toolchat


def batch(number, size=50 * 1024, *, name="houlm_inspect_node", result=None, reasoning=False):
    identifier = "call_%d" % number
    arguments = {"path": "/obj/test_%d" % number}
    if name == "houlm_propose_python":
        arguments = {"summary": "Create wave controls", "target_paths": ["/obj/test"],
                     "code": "result = 'EXACT_SOURCE_%d'" % number}
    call = {"id": identifier, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments)}}
    assistant = {"role": "assistant", "content": "Checking step %d" % number, "tool_calls": [call]}
    if reasoning:
        assistant["reasoning_items"] = [{"id": "rs_%d" % number, "type": "reasoning",
                                         "encrypted_content": "exact opaque bytes+/=", "summary": []}]
    data = result if result is not None else {"ok": True, "result": {
        "operation": "inspect_node", "node": {"path": "/obj/test_%d" % number,
        "parameters": [{"name": "snippet", "value": "x" * size}]}}}
    return [assistant, {"role": "tool", "tool_call_id": identifier, "content": json.dumps(data)}]


def build(window, notice):
    return toolchat.build_payload("studio-chatgpt", window, context_notice=notice)


def notice_record(payload):
    text = payload["messages"][0]["content"]
    marker = '{"kind":"archived_context"'
    return json.loads(text[text.index(marker):])


class RequestContextTests(unittest.TestCase):
    def test_small_request_and_provider_state_stay_exact(self):
        history = [{"role": "user", "content": "Inspect my selected node."}] + batch(1, 50, reasoning=True)
        original = copy.deepcopy(history)
        payload, stats = request_context.fit(history, build)
        self.assertEqual(payload["messages"][1:], history)
        self.assertEqual(history, original)
        self.assertEqual(stats, {"omitted_messages": 0, "compacted_tool_results": 0})

    def test_long_single_task_fits_and_keeps_latest_batch_exact(self):
        history = [{"role": "user", "content": "Build and inspect the complete network."}]
        for index in range(12):
            history += batch(index, reasoning=True)
        original = copy.deepcopy(history)
        payload, stats = request_context.fit(history, build)
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=True).encode()), request_context.WORKING_REQUEST_BYTES)
        self.assertEqual(payload["messages"][1], history[0])
        self.assertEqual(payload["messages"][-2:], history[-2:])
        self.assertEqual(history, original)
        self.assertGreater(stats["compacted_tool_results"], 0)
        self.assertIn("Do not replay or resubmit", payload["messages"][0]["content"])

    def test_small_long_history_is_proactively_reduced(self):
        history = [{"role": "user", "content": "Inspect a few things."}]
        for index in range(10):
            history += batch(index, size=10)
        payload, stats = request_context.fit(history, build)
        self.assertEqual(stats["omitted_messages"], 8)
        self.assertEqual(stats["compacted_tool_results"], 4)
        self.assertEqual(payload["messages"][2:], history[9:])
        self.assertLessEqual(sum(message["role"] == "assistant" for message in payload["messages"]), 6)

    def test_single_huge_parallel_batch_excerpts_outputs_and_preserves_provider_state(self):
        pieces = [batch(index, 64 * 1024, reasoning=True) for index in range(8)]
        assistant = copy.deepcopy(pieces[0][0])
        assistant["tool_calls"] = [piece[0]["tool_calls"][0] for piece in pieces]
        history = [{"role": "user", "content": "Inspect eight nodes."}, assistant]
        history += [piece[1] for piece in pieces]
        original = copy.deepcopy(history)
        payload, stats = request_context.fit(history, build)
        self.assertLessEqual(len(json.dumps(payload).encode()), request_context.WORKING_REQUEST_BYTES)
        self.assertEqual(payload["messages"][2], assistant)
        self.assertEqual(stats["compacted_tool_results"], 8)
        for result in payload["messages"][3:]:
            self.assertTrue(json.loads(result["content"])["houlm_context_compaction"]["result_omitted"])
        self.assertEqual(history, original)

    def test_action_failure_and_rollback_facts_remain_in_notice(self):
        outcome = {"success": False, "status": "failed", "execution_started": True,
                   "manual_undo_needed": True, "rollback": "not_attempted",
                   "completed_operations": 2, "error": {"code": "python_error", "message": "bad argument"}}
        history = [{"role": "user", "content": "Create a wave network."}]
        history += batch(0, name="houlm_propose_python", result=outcome)
        for index in range(1, 12):
            history += batch(index)
        payload, _ = request_context.fit(history, build)
        receipt = notice_record(payload)["mutation_receipts"][0]
        for key in request_context._OUTCOMES:
            if key in outcome:
                self.assertEqual(receipt["result"][key], outcome[key])
        self.assertEqual(receipt["target_paths"], ["/obj/test"])
        self.assertNotIn("EXACT_SOURCE_0", payload["messages"][0]["content"])

    def test_continue_keeps_previous_goal_and_action_receipt(self):
        history = [{"role": "user", "content": "Build the Solaris solar panel field with editable controls."}]
        history += batch(0, name="houlm_propose_python", result={"success": True, "execution_started": True})
        for index in range(1, 12):
            history += batch(index)
        history += [{"role": "user", "content": "Continue the previous task."}]
        payload, stats = request_context.fit(history, build)
        record = notice_record(payload)
        self.assertIn("Solaris solar panel field", record["recent_archived_user_requests"][-1]["user_request_excerpt"])
        self.assertTrue(record["mutation_receipts"][0]["result"]["success"])
        self.assertEqual(payload["messages"][-1], history[-1])
        self.assertEqual(stats["omitted_messages"], len(history) - 1)

    def test_last_batch_provider_state_is_removed_only_with_entire_batch(self):
        history = [{"role": "user", "content": "Finish the task."}] + batch(1, 100, reasoning=True)
        history[1]["reasoning_items"][0]["encrypted_content"] = "opaque" * 30000
        payload, stats = request_context.fit(history, build)
        self.assertEqual(payload["messages"][1:], [history[0]])
        self.assertEqual(stats["omitted_messages"], 2)
        self.assertNotIn("opaque", str(payload))
        self.assertEqual(history[1]["reasoning_items"][0]["encrypted_content"], "opaque" * 30000)

    def test_long_artist_prompt_may_exceed_soft_target_without_truncation(self):
        history = [{"role": "user", "content": "a" * 160000}]
        payload, stats = request_context.fit(history, build)
        self.assertEqual(payload["messages"][1:], history)
        self.assertEqual(stats["omitted_messages"], 0)

    def test_oversized_current_artist_prompt_is_never_truncated(self):
        history = [{"role": "user", "content": "a" * core.MAX_REQUEST_BYTES}]
        original = copy.deepcopy(history)
        with self.assertRaisesRegex(core.RequestSizeError, "current prompt"):
            request_context.fit(history, build)
        self.assertEqual(history, original)

    def test_escaped_unicode_final_payload_obeys_actual_size(self):
        history = [{"role": "user", "content": "Inspect nodes."}]
        for index in range(10):
            history += batch(index, result={"ok": True, "result": {"value": "\\\"\nő🦀" * 16000}})
        payload, _ = request_context.fit(history, build)
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=True).encode()), request_context.WORKING_REQUEST_BYTES)

    def test_malformed_archived_tool_pair_is_not_hidden_by_recent_turn(self):
        previous = [{"role": "user", "content": "Old task"}] + batch(1)
        previous.pop()
        history = previous + [{"role": "user", "content": "Continue."}]
        with self.assertRaisesRegex(ValueError, "complete assistant batch"):
            request_context.fit(history, build, max_messages=1)

    def test_reused_ids_or_invalid_arguments_are_not_hidden_by_omission(self):
        duplicate = [{"role": "user", "content": "Old task"}] + batch(1) + batch(1)
        invalid = [{"role": "user", "content": "Old task"}] + batch(1)
        invalid[1]["tool_calls"][0]["function"]["arguments"] = "not json"
        for history in (duplicate, invalid):
            history += [{"role": "user", "content": "Latest task."}]
            with self.subTest(history=history), self.assertRaises(ValueError):
                request_context.fit(history, build, max_messages=1)

    def test_builder_non_size_failure_propagates(self):
        history = [{"role": "user", "content": "Hello."}]
        builder = mock.Mock(side_effect=ValueError("invalid service model"))
        with self.assertRaisesRegex(ValueError, "invalid service model"):
            request_context.fit(history, builder)
        self.assertEqual(builder.call_count, 1)

    def test_repeated_reduction_is_deterministic_and_preserves_full_input(self):
        history = [{"role": "user", "content": "Inspect the scene."}]
        for index in range(12):
            history += batch(index)
        original = copy.deepcopy(history)
        first = request_context.fit(history, build)
        self.assertEqual(request_context.fit(history, build), first)
        self.assertEqual(history, original)

    def test_plain_chat_uses_same_complete_turn_policy(self):
        history = []
        for index in range(12):
            history += [{"role": "user", "content": "Question %d" % index},
                        {"role": "assistant", "content": "Answer %d" % index}]
        history += [{"role": "user", "content": "Continue."}]
        payload, stats = request_context.fit(history, lambda window, notice:
            core.build_chat_payload("studio-chatgpt", window, context_notice=notice), max_messages=98)
        self.assertEqual(payload["messages"][-1], history[-1])
        self.assertGreater(stats["omitted_messages"], 0)
        self.assertEqual(payload["messages"][1:], history[-13:])

    def test_eight_short_plain_turns_keep_six_recent_answers(self):
        history = []
        for index in range(8):
            history += [{"role": "user", "content": "Question %d" % index},
                        {"role": "assistant", "content": "Answer %d" % index}]
        history += [{"role": "user", "content": "Use the previous answer."}]
        payload, stats = request_context.fit(history, lambda window, notice:
            core.build_chat_payload("studio-chatgpt", window, context_notice=notice), max_messages=98)
        self.assertEqual(payload["messages"][1:], history[4:])
        self.assertEqual(stats["omitted_messages"], 4)
        self.assertEqual(stats["compacted_tool_results"], 0)

    def test_mixed_tool_turns_keep_recent_complete_turns(self):
        history = []
        turns = []
        for index in range(4):
            turn = [{"role": "user", "content": "Inspect task %d" % index}]
            turn += batch(index, size=8, reasoning=True)
            turn += [{"role": "assistant", "content": "Task %d results" % index}]
            turns.append(turn)
            history += turn
        history += [{"role": "user", "content": "Continue the previous task."}]
        payload, stats = request_context.fit(history, build)
        self.assertEqual(payload["messages"][1:], history[len(turns[0]):])
        self.assertEqual(stats["omitted_messages"], len(turns[0]))
        self.assertEqual(payload["messages"][-4]["reasoning_items"], turns[-1][1]["reasoning_items"])

    def test_byte_pressure_drops_oldest_turn_incrementally(self):
        history = []
        for index in range(3):
            history += [{"role": "user", "content": "Question %d" % index},
                        {"role": "assistant", "content": str(index) * 45000}]
        history += [{"role": "user", "content": "Use the previous answer."}]
        payload, stats = request_context.fit(history, lambda window, notice:
            core.build_chat_payload("studio-chatgpt", window, context_notice=notice), max_messages=98)
        self.assertEqual(payload["messages"][1:], history[2:])
        self.assertEqual(stats["omitted_messages"], 2)

    def test_message_limit_archives_complete_batches(self):
        history = [{"role": "user", "content": "Inspect the scene."}]
        for index in range(5):
            history += batch(index, 1)
        payload, _ = request_context.fit(history, build, max_messages=5)
        self.assertLessEqual(len(payload["messages"]) - 1, 5)
        self.assertEqual(payload["messages"][-2:], history[-2:])


if __name__ == "__main__":
    unittest.main()
