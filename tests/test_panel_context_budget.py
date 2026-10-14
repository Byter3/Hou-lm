"""Exercise bounded outgoing context without trimming the artist's saved chat.

Uses fake HOM, temporary HIP/JSON paths and the existing loopback HTTP server.
No model or real Houdini scene is contacted by these tests.
"""

import copy
import json
import re
import unittest
from unittest import mock

import test_panel as qt_support
import test_panel_history as history_support

if qt_support._qt_available:
    panel = qt_support.panel


@unittest.skipUnless(qt_support._qt_available, qt_support._qt_reason)
class PanelContextBudgetTests(unittest.TestCase):
    setUpClass = classmethod(qt_support.PanelTests.setUpClass.__func__)
    tearDownClass = classmethod(qt_support.PanelTests.tearDownClass.__func__)
    setUp = history_support.PanelHistoryTests.setUp
    tearDown = history_support.PanelHistoryTests.tearDown
    destroy_widget = history_support.PanelHistoryTests.destroy_widget
    saved_active = history_support.PanelHistoryTests.saved_active
    wait_for = qt_support.PanelTests.wait_for
    connect = qt_support.PanelTests.connect
    tool_call = staticmethod(qt_support.PanelTests.tool_call)

    @staticmethod
    def state(identifier):
        return [{"id": "rs_" + identifier, "type": "reasoning", "summary": [],
                 "encrypted_content": "opaque-provider-state:" + identifier}]

    def read_batch(self, identifier, *, count=1, characters=50 * 1024):
        calls = [self.tool_call(identifier + "_" + str(index), "houlm_inspect_node",
                                {"path": "/obj/context_test/node_%d" % index})
                 for index in range(count)]
        assistant = {"role": "assistant", "content": "Reading authored parameters.",
                     "tool_calls": calls, "reasoning_items": self.state(identifier)}
        results = [{"role": "tool", "tool_call_id": call["id"], "content": json.dumps({
            "ok": True, "result": {"path": "/obj/context_test/node_%d" % index,
                                    "parameters": [{"name": "snippet", "value": "x" * characters}],
                                    "errors": [], "warnings": [], "truncated": False},
        })} for index, call in enumerate(calls)]
        return [assistant] + results

    def large_current_turn(self):
        messages = [{"role": "user", "content":
                     "Inspect my existing network, repair its missing controls, and keep my animation."}]
        call = self.tool_call("attempted_python", "houlm_propose_python", {
            "summary": "Add the requested controls", "target_paths": ["/obj/context_test"],
            "code": "node = hou.node('/obj/context_test')\nresult = node.path()",
        })
        failed_result = {"success": False, "status": "execution_failed", "execution_started": True,
                         "manual_undo_needed": True, "error": "Synthetic callback failed",
                         "changed_paths": ["/obj/context_test"]}
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [call],
             "reasoning_items": self.state("attempted_python")},
            {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(failed_result)},
        ])
        for index in range(12):
            messages.extend(self.read_batch("old_read_%d" % index))
        messages.extend(self.read_batch("fresh_read", characters=1500))
        return messages, failed_result

    def arm_continuation(self, messages):
        self.widget._messages = []
        self.widget._agent_history = copy.deepcopy(messages)
        self.widget._agent_model_id = self.widget.models.currentData()
        self.widget._pending_prompt = messages[0]["content"]
        self.widget._agent_rounds = 3
        self.widget._agent_tool_count = 14
        self.widget._proposal_attempted = True
        self.widget._turn_has_edits = True
        self.widget._checkpoint()

    def assert_request_bounded(self, payload):
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=True).encode("utf-8")),
                             panel.core.MAX_REQUEST_BYTES)
        self.assertTrue(payload["tools"], "Measure the actual request including all tool definitions")
        self.assertTrue(payload["messages"][0]["content"], "Measure the system/context instructions too")

    def assert_paired(self, messages):
        pending = set()
        for message in messages:
            if message["role"] == "tool":
                self.assertIn(message["tool_call_id"], pending)
                pending.remove(message["tool_call_id"])
            else:
                self.assertFalse(pending)
                pending = {call["id"] for call in message.get("tool_calls", [])}
        self.assertFalse(pending)

    def archive_receipt(self, payload):
        for message in payload["messages"]:
            content = message.get("content", "")
            match = re.search(r'\{"kind"\s*:\s*"archived_context"', content)
            if match:
                return json.JSONDecoder().raw_decode(content[match.start():])[0]
        self.fail("The reduced request must explain its archived historical context")

    def test_same_turn_compaction_preserves_user_latest_state_and_full_sidecar(self):
        self.connect(scene_access=True)
        original, failed_result = self.large_current_turn()
        self.arm_continuation(original)
        before = copy.deepcopy(self.widget._agent_history)
        with mock.patch.object(panel.python_actions, "apply") as python, mock.patch.object(
                panel.edits, "apply") as edits, mock.patch.object(panel.toolchat, "execute_calls") as reads:
            payload = self.widget._build_payload(self.widget._agent_model_id,
                                                 self.widget._agent_history, agent=True)
            python.assert_not_called()
            edits.assert_not_called()
            reads.assert_not_called()
        self.assert_request_bounded(payload)
        self.assert_paired(payload["messages"])
        self.assertIn(original[0], payload["messages"])
        self.assertEqual(payload["messages"][-2:], original[-2:],
                         "The newest complete batch and opaque provider state should remain exact")
        receipt = self.archive_receipt(payload)["mutation_receipts"][0]
        self.assertTrue(receipt["result"]["manual_undo_needed"])
        self.assertFalse(receipt["result"]["success"])
        self.assertEqual(receipt["result"]["status"], "execution_failed")
        self.assertIn("/obj/context_test", receipt["target_paths"])
        self.assertEqual(self.widget._agent_history, before)
        self.assertEqual(self.widget._messages, [])
        self.widget._checkpoint()
        saved = self.saved_active()
        self.assertEqual(saved["messages"], original)
        self.assertEqual(json.loads(saved["messages"][2]["content"]), failed_result)

    def test_large_same_turn_continues_over_http_without_replaying_commands(self):
        self.connect(scene_access=True)
        original, unused_failure = self.large_current_turn()
        self.arm_continuation(original)
        qt_support.StudioHandler.chat_responses.append((200, {"choices": [{"message": {
            "role": "assistant", "content": "The earlier action failed; inspect its partial changes first."
        }, "finish_reason": "stop"}]}))
        with mock.patch.object(panel.python_actions, "apply") as python, mock.patch.object(
                panel.edits, "apply") as edits, mock.patch.object(panel.toolchat, "execute_calls") as reads:
            self.widget._next_agent_round(self.widget._generation)
            self.wait_for(lambda: self.widget._agent_history is None and self.widget._reply is None)
            python.assert_not_called()
            edits.assert_not_called()
            reads.assert_not_called()
        requests = [entry[3] for entry in qt_support.StudioHandler.requests if entry[0] == "POST"]
        self.assertEqual(len(requests), 1)
        self.assert_request_bounded(requests[0])
        self.assertEqual(self.widget._messages[:-1], original)
        self.assertEqual(self.saved_active()["messages"], self.widget._messages)
        self.assertNotIn("Request failed", self.widget.transcript.toPlainText())
        self.assertRegex(self.widget.history_status.toolTip().lower(), r"context|retained|condens|archiv")

    def test_oversized_latest_parallel_batch_keeps_calls_results_and_provider_state_matched(self):
        self.connect(scene_access=True)
        original = [{"role": "user", "content": "Inspect these eight nodes; do not change the scene."}]
        original.extend(self.read_batch("wide_batch", count=8, characters=64 * 1024))
        # Non-ASCII makes escaped wire bytes larger than character counts.
        for result in original[2:]:
            result["content"] = result["content"].replace("x" * 256, "ő" * 256, 1)
        unchanged = copy.deepcopy(original)
        with mock.patch.object(panel.python_actions, "apply") as execute:
            payload = self.widget._build_payload("permitted-local", original, agent=True)
            execute.assert_not_called()
        self.assert_request_bounded(payload)
        self.assert_paired(payload["messages"])
        self.assertIn(original[0], payload["messages"])
        self.assertIn(original[1], payload["messages"])
        retained = [message for message in payload["messages"] if message["role"] == "tool"]
        self.assertEqual([message["tool_call_id"] for message in retained],
                         [message["tool_call_id"] for message in original[2:]])
        self.assertTrue(any(message["content"] != old["content"]
                            for message, old in zip(retained, original[2:])))
        for message in retained:
            self.assertIsInstance(json.loads(message["content"]), dict)
        self.assertEqual(original, unchanged)

    def test_older_turn_is_archived_without_changing_current_user_or_saved_messages(self):
        self.connect(scene_access=True)
        original = [{"role": "user", "content": "Old scene snapshot: " + "p" * (550 * 1024)},
                    {"role": "assistant", "content": "I inspected the previous scene."},
                    {"role": "user", "content": "Continue by inspecting @/obj/context_test."}]
        original.extend(self.read_batch("current", characters=1024))
        self.widget._messages = copy.deepcopy(original)
        payload = self.widget._build_payload("permitted-local", self.widget._messages, agent=True)
        self.assert_request_bounded(payload)
        self.assert_paired(payload["messages"])
        self.assertIn(original[2], payload["messages"])
        self.assertEqual(payload["messages"][-2:], original[-2:])
        self.assertEqual(self.widget._messages, original)
        self.widget._checkpoint()
        self.assertEqual(self.saved_active()["messages"], original)

    def test_continue_after_large_failed_turn_keeps_original_goal_and_partial_action_receipt(self):
        self.connect(scene_access=True)
        original, unused_failure = self.large_current_turn()
        original.append({"role": "user", "content": "Continue the previous task."})
        self.widget._messages = copy.deepcopy(original)
        payload = self.widget._build_payload("permitted-local", self.widget._messages, agent=True)
        self.assert_request_bounded(payload)
        self.assert_paired(payload["messages"])
        archive = self.archive_receipt(payload)
        self.assertIn(original[0]["content"], json.dumps(archive["recent_archived_user_requests"]))
        self.assertIn(original[-1], payload["messages"])
        receipt = archive["mutation_receipts"][0]
        self.assertEqual(receipt["result"]["status"], "execution_failed")
        self.assertTrue(receipt["result"]["manual_undo_needed"])
        self.assertIn("/obj/context_test", receipt["target_paths"])
        self.assertEqual(self.widget._messages, original)
        self.widget._checkpoint()
        self.assertEqual(self.saved_active()["messages"], original)

    def test_malformed_pairing_is_not_misdiagnosed_as_context_size(self):
        self.connect(scene_access=True)
        original = [{"role": "user", "content": "Inspect one node."},
                    {"role": "assistant", "content": "", "tool_calls": [
                        self.tool_call("missing_result", "houlm_inspect_node", {"path": "/obj/test"})]}]
        self.arm_continuation(original)
        self.widget._turn_has_edits = False
        self.widget._next_agent_round(self.widget._generation)
        self.app.processEvents()
        self.assertFalse([entry for entry in qt_support.StudioHandler.requests if entry[0] == "POST"])
        self.assertNotIn("size limit", self.widget.status.text().lower())
        self.assertNotIn("narrower question", self.widget.status.text().lower())
        self.assertIsNone(self.widget._agent_history)
        self.assertEqual(self.widget.prompt.toPlainText(), original[0]["content"])

    def test_impossible_user_prompt_fails_without_silently_truncating_or_running_anything(self):
        self.connect(scene_access=True)
        prompt = "Keep this exact authored source: " + "x" * panel.core.MAX_REQUEST_BYTES
        self.widget.prompt.setPlainText(prompt)
        with mock.patch.object(panel.python_actions, "apply") as python, mock.patch.object(
                panel.edits, "apply") as edits, mock.patch.object(panel.toolchat, "execute_calls") as reads:
            self.widget._send()
            self.app.processEvents()
            python.assert_not_called()
            edits.assert_not_called()
            reads.assert_not_called()
        self.assertEqual(self.widget.prompt.toPlainText(), prompt)
        self.assertFalse([entry for entry in qt_support.StudioHandler.requests if entry[0] == "POST"])
        self.assertIsNone(self.widget._agent_history)
        self.assertRegex(self.widget.status.text().lower(), r"large|limit|size|fit")


if __name__ == "__main__":
    unittest.main()
