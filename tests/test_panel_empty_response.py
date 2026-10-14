"""Retry an empty provider response without replaying completed Houdini work.

Real Qt networking talks only to the synthetic loopback server. HOM, edits,
credentials and preferences are faked; history uses temporary HIP sidecars.
"""

import json
import types
import unittest
from unittest import mock

import test_panel as qt_support
import test_panel_history as history_support

if qt_support._qt_available:
    panel = qt_support.panel


@unittest.skipUnless(qt_support._qt_available, qt_support._qt_reason)
class PanelEmptyResponseTests(unittest.TestCase):
    setUpClass = classmethod(qt_support.PanelTests.setUpClass.__func__)
    tearDownClass = classmethod(qt_support.PanelTests.tearDownClass.__func__)
    setUp = history_support.PanelHistoryTests.setUp
    tearDown = history_support.PanelHistoryTests.tearDown
    destroy_widget = history_support.PanelHistoryTests.destroy_widget
    saved_active = history_support.PanelHistoryTests.saved_active
    wait_for = qt_support.PanelTests.wait_for
    connect = qt_support.PanelTests.connect
    tool_call = staticmethod(qt_support.PanelTests.tool_call)
    tool_stream = staticmethod(qt_support.PanelTests.tool_stream)
    sse_events = staticmethod(qt_support.PanelTests.sse_events)
    fake_tool_results = staticmethod(qt_support.PanelTests.fake_tool_results)
    final_reply = staticmethod(qt_support.PanelTests.final_reply)
    invalid_argument_stream = qt_support.PanelTests.invalid_argument_stream

    @staticmethod
    def empty_response(stream=True):
        message = {
            "role": "assistant", "content": "", "tool_calls": [],
            "reasoning_content": "PRIVATE_REJECTED_REASONING",
            "reasoning_items": [{"id": "rs_discard_empty", "type": "reasoning", "summary": [],
                                 "encrypted_content": "PRIVATE_REJECTED_PROVIDER_STATE"}],
        }
        response = {"choices": [{"delta" if stream else "message": message, "finish_reason": "stop"}]}
        if stream:
            return ("data: " + json.dumps(response) + "\n\ndata: [DONE]\n\n").encode()
        return response

    def posts(self):
        return [request[3] for request in qt_support.StudioHandler.requests if request[0] == "POST"]

    def finish(self):
        self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)

    def send(self, text="Inspect the staircase without modifying it or forcing a cook"):
        self.widget.prompt.setPlainText(text)
        self.widget._send()

    def assert_no_rejected_state(self):
        for value in (self.widget._messages, self.posts(), self.saved_active(),
                      self.widget.transcript.toPlainText(), self.widget._last_error_details):
            rendered = json.dumps(value)
            self.assertNotIn("PRIVATE_REJECTED", rendered)
            self.assertNotIn("rs_discard_empty", rendered)

    def test_empty_sse_and_json_retry_once_from_valid_history(self):
        self.connect(scene_access=True)
        for stream in (True, False):
            with self.subTest(stream=stream):
                self.widget._new_chat()
                qt_support.StudioHandler.requests.clear()
                qt_support.StudioHandler.chat_responses.extend([
                    (200, self.empty_response(stream)),
                    (200, self.final_reply("I have not made any scene changes.")),
                ])
                with mock.patch.object(panel.toolchat, "execute_calls") as reads, mock.patch.object(
                    panel.edits, "prepare"
                ) as prepare, mock.patch.object(panel.toolchat, "build_payload", wraps=panel.toolchat.build_payload) as build:
                    self.send()
                    self.finish()
                    reads.assert_not_called()
                    prepare.assert_not_called()
                posts = self.posts()
                self.assertEqual(len(posts), 2)
                self.assertEqual(posts[0]["messages"][1:], posts[1]["messages"][1:])
                self.assertEqual(sum(call.kwargs.get("empty_response_retry") is True for call in build.call_args_list), 1)
                self.assertTrue(self.widget._empty_response_retry_used)
                self.assertEqual(self.widget._last_error_details, "")
                self.assertEqual(self.widget._messages[-1]["content"], "I have not made any scene changes.")
                self.assert_no_rejected_state()

    def test_read_results_retained_and_recovery_answer_does_not_trigger_completion_check(self):
        self.connect(scene_access=True)
        call = self.tool_call("completed_read", "houlm_inspect_node", {"path": "/obj/geo1"})
        state = [{"id": "rs_valid_read", "type": "reasoning", "summary": [], "encrypted_content": "valid-read-state"}]
        qt_support.StudioHandler.chat_responses.extend([
            (200, self.tool_stream(call, state)),
            (200, self.empty_response()),
            (200, self.final_reply("The authored wiring is valid. No cook was requested.")),
        ])
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results) as reads, mock.patch.object(
            panel.edits, "prepare"
        ) as prepare, mock.patch.object(panel.toolchat, "build_payload", wraps=panel.toolchat.build_payload) as build:
            self.send()
            self.finish()
            reads.assert_called_once_with(panel.hou, [call])
            prepare.assert_not_called()
        posts = self.posts()
        self.assertEqual(len(posts), 3, "Recovery's final answer must not cause an extra completion-check request")
        self.assertEqual(posts[1]["messages"][1:], posts[2]["messages"][1:])
        self.assertEqual(posts[2]["messages"][-2]["reasoning_items"], state)
        self.assertEqual([item["role"] for item in self.widget._messages], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(self.saved_active()["messages"], self.widget._messages)
        self.assertFalse(any(item.kwargs.get("completion_check") for item in build.call_args_list))
        self.assert_no_rejected_state()

    def test_applied_edit_remains_once_and_retry_does_not_replay_it(self):
        self.connect(scene_access=True)
        call = self.tool_call("completed_edit", "houlm_propose_edits")
        result = {"status": "applied", "success": True, "completed_operations": 1, "changed_paths": ["/obj/geo1"]}
        qt_support.StudioHandler.chat_responses.extend([
            (200, self.tool_stream(call)),
            (200, self.empty_response()),
            (200, self.final_reply("The requested edit was applied once.")),
        ])
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Update one control")) as prepare, mock.patch.object(
            panel.edits, "apply", return_value=result
        ) as apply, mock.patch.object(panel.toolchat, "execute_calls") as reads:
            self.send("Update one control")
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.finish()
            prepare.assert_called_once()
            apply.assert_called_once()
            reads.assert_not_called()
        posts = self.posts()
        self.assertEqual(len(posts), 3)
        self.assertEqual(posts[1]["messages"][1:], posts[2]["messages"][1:])
        results = [item for item in self.widget._messages if item["role"] == "tool"]
        self.assertEqual(len(results), 1)
        self.assertEqual(json.loads(results[0]["content"]), result)
        self.assertEqual(self.saved_active()["messages"], self.widget._messages)
        self.assert_no_rejected_state()

    def test_repeated_empty_response_stops_after_one_retry_and_preserves_read_results(self):
        self.connect(scene_access=True)
        qt_support.StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("retained_read"))),
            (200, self.empty_response()),
            (200, self.empty_response(False)),
        ])
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results) as reads:
            self.send()
            self.finish()
            reads.assert_called_once()
        self.assertEqual(len(self.posts()), 3)
        self.assertEqual([item["role"] for item in self.widget._messages], ["user", "assistant", "tool"])
        self.assertIn("First rejected response", self.widget._last_error_details)
        self.assertIn("Failure after retry", self.widget._last_error_details)
        self.assertGreaterEqual(self.widget._last_error_details.count("empty_response"), 2)
        self.assertIn("REQUEST FAILED", self.widget.transcript.toPlainText().upper())
        self.assert_no_rejected_state()

    def test_invalid_argument_and_empty_failures_share_one_retry_allowance(self):
        self.connect(scene_access=True)
        for empty_first in (True, False):
            with self.subTest(empty_first=empty_first):
                self.widget._new_chat()
                qt_support.StudioHandler.requests.clear()
                responses = [self.empty_response(), self.invalid_argument_stream()]
                if not empty_first:
                    responses.reverse()
                qt_support.StudioHandler.chat_responses.extend((200, response) for response in responses)
                with mock.patch.object(panel.toolchat, "execute_calls") as reads, mock.patch.object(panel.edits, "prepare") as prepare:
                    self.send()
                    self.finish()
                    reads.assert_not_called()
                    prepare.assert_not_called()
                self.assertEqual(len(self.posts()), 2)
                self.assertEqual(self.widget._messages, [])
                self.assertIn("empty_response", self.widget._last_error_details)
                self.assertIn("tool_arguments_invalid_json", self.widget._last_error_details)
                self.assertIn("Failure after retry", self.widget._last_error_details)
                self.assert_no_rejected_state()

    def test_stop_new_chat_and_disconnect_cancel_queued_empty_retry(self):
        for action in ("_cancel_turn", "_new_chat", "disconnect"):
            with self.subTest(action=action):
                self.connect(scene_access=True)
                self.widget._new_chat()
                qt_support.StudioHandler.requests.clear()
                qt_support.StudioHandler.chat_responses.append((200, self.empty_response()))
                callbacks = []
                with mock.patch.object(panel.QtCore.QTimer, "singleShot", side_effect=lambda delay, callback: callbacks.append(callback)), mock.patch.object(
                    panel.toolchat, "execute_calls"
                ) as reads:
                    self.send()
                    self.wait_for(lambda: self.widget._reply is None and bool(callbacks))
                    self.assertTrue(self.widget._empty_response_retry_used)
                    getattr(self.widget, action)()
                    for callback in callbacks:
                        callback()
                    self.app.processEvents()
                    reads.assert_not_called()
                self.assertEqual(len(self.posts()), 1)
                self.assertIsNone(self.widget._agent_history)

    def test_exhausted_round_budget_does_not_retry(self):
        self.connect(scene_access=True)
        qt_support.StudioHandler.chat_responses.append((200, self.empty_response()))
        self.send()
        self.widget._agent_rounds = panel.MAX_AGENT_ROUNDS
        self.finish()
        self.assertEqual(len(self.posts()), 1)
        self.assertFalse(self.widget._empty_response_retry_used)
        self.assertIn("empty_response", self.widget._last_error_details)
        self.assertNotIn("First rejected response", self.widget._last_error_details)

    def test_exhausted_tool_budget_allows_only_text_recovery(self):
        self.connect(scene_access=True)
        qt_support.StudioHandler.chat_responses.extend([
            (200, self.empty_response()),
            (200, self.final_reply("The inspection limit has been reached; no further actions were run.")),
        ])
        with mock.patch.object(panel.toolchat, "execute_calls") as reads, mock.patch.object(panel.edits, "prepare") as prepare:
            self.send()
            self.widget._agent_tool_count = panel.MAX_AGENT_TOOL_CALLS
            self.finish()
            reads.assert_not_called()
            prepare.assert_not_called()
        self.assertEqual(len(self.posts()), 2)
        self.assertEqual(self.posts()[1]["tool_choice"], "none")
        self.assertEqual(self.widget._last_error_details, "")

    def test_empty_then_http_failure_retains_both_safe_diagnostics(self):
        self.connect(scene_access=True)
        qt_support.StudioHandler.chat_responses.extend([
            (200, self.empty_response()),
            (500, {"error": {"message": "PRIVATE_UPSTREAM_PAYLOAD"}}),
        ])
        self.send()
        self.finish()
        self.assertEqual(len(self.posts()), 2)
        self.assertIn("First rejected response", self.widget._last_error_details)
        self.assertIn("Failure after retry", self.widget._last_error_details)
        self.assertIn("empty_response", self.widget._last_error_details)
        self.assertNotIn("PRIVATE_UPSTREAM_PAYLOAD", self.widget._last_error_details)
        self.assert_no_rejected_state()

    def test_new_artist_turn_resets_empty_retry_allowance(self):
        self.connect(scene_access=True)
        for index in range(2):
            with self.subTest(turn=index):
                qt_support.StudioHandler.chat_responses.extend([
                    (200, self.empty_response()), (200, self.final_reply("Inspection complete.")),
                ])
                self.send("Inspect request %d without any edits" % index)
                self.assertFalse(self.widget._empty_response_retry_used)
                self.finish()
                self.assertTrue(self.widget._empty_response_retry_used)
        self.assertEqual(len(self.posts()), 4)
        self.assertEqual([item["role"] for item in self.widget._messages], ["user", "assistant"] * 2)

    def test_plain_chat_empty_response_is_not_retried_as_agent_work(self):
        self.connect(scene_access=False)
        qt_support.StudioHandler.chat_responses.append((200, self.empty_response()))
        with mock.patch.object(panel.toolchat, "execute_calls") as reads:
            self.send()
            self.finish()
            reads.assert_not_called()
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.widget._messages, [])
        self.assertIn("unexpected_response", self.widget._last_error_details)
        self.assertIn("Request kind: chat", self.widget._last_error_details)


if __name__ == "__main__":
    unittest.main()
