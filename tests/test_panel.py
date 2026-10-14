"""Exercise real Qt networking against loopback; never contact the studio VM."""

import json
import os
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "houdini" / "python3.11libs"))
_DLL_HANDLES = []
_qt_available = False
_qt_reason = "PySide2 is not installed for this Python runtime"

# Houdini's plain Python executable needs its Qt path and DLL directory supplied.
if sys.platform == "win32":
    install = Path(os.environ.get("HFS", r"C:\Program Files\Side Effects Software\Houdini 20.5.445"))
    forced_packages = install / "python311" / "lib" / "site-packages-forced"
    if sys.version_info[:2] == (3, 11) and forced_packages.is_dir():
        sys.path.insert(0, str(forced_packages))
        _DLL_HANDLES.append(os.add_dll_directory(str(install / "bin")))
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(install / "bin" / "Qt_plugins" / "platforms"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide2 import QtCore, QtWidgets
except ImportError as error:
    _qt_reason = str(error)
else:
    fake_hou = types.ModuleType("hou")
    with mock.patch.dict(sys.modules, {"hou": fake_hou}):
        from houlm import panel
    _qt_available = True


class StudioHandler(BaseHTTPRequestHandler):
    requests = []
    chat_responses = []

    def log_message(self, *args):
        pass

    def _respond(self, status, payload, content_type="application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.requests.append(("GET", self.path, self.headers.get("Authorization"), None))
        if self.path.startswith("/redirect/"):
            self.send_response(302)
            self.send_header("Location", "/redirect-destination")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path.startswith("/denied/"):
            self._respond(401, {"error": "SERVER_SECRET_MUST_NOT_APPEAR"})
        else:
            self._respond(200, {"data": [
                {"id": "permitted-local", "name": "Studio Local"},
                {"id": "permitted-cloud", "name": "Studio Cloud"},
            ]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.requests.append(("POST", self.path, self.headers.get("Authorization"), body))
        if self.chat_responses:
            status, response = self.chat_responses.pop(0)
            content_type = "text/event-stream" if isinstance(response, bytes) and response.startswith(b"data:") else "application/json"
            self._respond(status, response, content_type)
        elif self.path.startswith("/broken-stream/"):
            response = b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
            self._respond(200, response, "text/event-stream")
        elif self.path.startswith("/sse/"):
            events = [
                {"choices": [{"delta": {"role": "assistant", "reasoning_content": "Internal reasoning is not the answer"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "O"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "K"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"total_tokens": 24}},
            ]
            response = ("".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n").encode()
            self._respond(200, response, "text/event-stream")
        else:
            self._respond(200, {"choices": [{"message": {"content": "OK", "role": "assistant"}}]})


@unittest.skipUnless(_qt_available, _qt_reason)
class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StudioHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        StudioHandler.requests.clear()
        StudioHandler.chat_responses.clear()
        self.preference_read = mock.patch.object(panel.core, "load_preferences", return_value={})
        self.preference_write = mock.patch.object(panel.core, "save_preferences")
        self.preference_read.start()
        self.save_preferences = self.preference_write.start()
        self.credential_read = mock.patch.object(panel.credentials, "load_key", return_value="")
        self.credential_write = mock.patch.object(panel.credentials, "save_key")
        self.credential_delete = mock.patch.object(panel.credentials, "delete_key")
        self.load_key = self.credential_read.start()
        self.save_key = self.credential_write.start()
        self.delete_key = self.credential_delete.start()
        self.widget = panel.AssistantPanel()

    def tearDown(self):
        self.widget.disconnect()
        self.widget.deleteLater()
        self.app.processEvents()
        self.preference_write.stop()
        self.preference_read.stop()
        self.credential_delete.stop()
        self.credential_write.stop()
        self.credential_read.stop()

    def wait_for(self, predicate, seconds=4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Qt operation did not complete: " + self.widget.status.text())

    def connect(self, suffix="", scene_access=False):
        self.widget.server_url.setText(self.base + suffix)
        self.widget.api_key.setText("test-only-session-key")
        self.widget._connect()
        self.wait_for(lambda: self.widget._reply is None)
        self.assertTrue(self.widget._connected)
        self.widget.scene_tools.setChecked(scene_access)

    def test_initial_controls_have_no_prebaked_server(self):
        self.assertEqual(self.widget.server_url.text(), "")
        self.assertFalse(self.widget.send_button.isEnabled())
        self.assertFalse(self.widget.include_selection.isChecked())
        self.assertTrue(self.widget.connect_button.isEnabled())
        self.assertTrue(self.widget.scene_tools.isChecked())
        self.assertFalse(hasattr(self.widget, "ask_button"))
        self.assertFalse(hasattr(self.widget, "build_button"))
        self.assertFalse(hasattr(self.widget, "broker_url"))
        self.assertFalse(hasattr(self.widget, "broker_fingerprint"))

    @staticmethod
    def tool_call(identifier, name="houlm_session_info", arguments=None):
        return {"id": identifier, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments or {}),
        }}

    @staticmethod
    def tool_stream(call, reasoning_items=None):
        arguments = call["function"]["arguments"]
        split = max(1, len(arguments) // 2)
        events = [
            {"choices": [{"delta": {"role": "assistant", "tool_calls": [{
                "index": 0, "id": call["id"], "type": "function",
                "function": {"name": call["function"]["name"], "arguments": arguments[:split]},
            }]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": arguments[split:]}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        if reasoning_items:
            events[0]["choices"][0]["delta"]["reasoning_items"] = reasoning_items
        return ("".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n").encode()

    @staticmethod
    def sse_events(events):
        return ("".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n").encode()

    def invalid_argument_stream(self, identifier="invalid_arguments"):
        call = self.tool_call(identifier, "houlm_propose_edits")
        call["function"]["arguments"] = '{"private":"test-only-session-key","PRIVATE_ARGUMENT_SENTINEL":'
        return self.tool_stream(call)

    @staticmethod
    def fake_tool_results(unused_hou, calls):
        return [{"role": "tool", "tool_call_id": call["id"], "content": '{"ok":true,"result":{"truncated":false}}'} for call in calls]

    def test_scene_tool_loop_reads_this_session_and_preserves_followup_history(self):
        from test_inspection import Hou, Node, Parm
        node = Node("/obj/geo/wrangle", parameters=[Parm("snippet", "@P.y += 1;")])
        current_hou = Hou(nodes=(node,), selection=(node,))
        self.connect()
        self.widget.scene_tools.setChecked(True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("selection_1", "houlm_get_selection"))),
            (200, self.tool_stream(self.tool_call("inspect_1", "houlm_inspect_node", {"path": node.path()}))),
            (200, {"choices": [{"message": {"content": "The wrangle raises each point."}, "finish_reason": "stop"}]}),
            (200, self.final_reply("The wrangle raises each point.")),
        ])
        with mock.patch.object(panel, "hou", current_hou), mock.patch.object(
            panel.toolchat.inspection, "execute", wraps=panel.toolchat.inspection.execute
        ) as execute:
            self.widget.prompt.setPlainText("Explain my selected wrangle")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            self.assertEqual([call.args[1] for call in execute.call_args_list], ["get_selection", "inspect_node"])
            self.assertTrue(all(call.args[0] is current_hou for call in execute.call_args_list))
        posts = [entry for entry in StudioHandler.requests if entry[0] == "POST"]
        self.assertEqual(len(posts), 4)
        self.assertTrue(all(entry[1] == "/api/chat/completions" for entry in posts))
        self.assertEqual(len(posts[0][3]["tools"]), len(panel.toolchat.TOOLS) + 2)
        for post, count in zip(posts, (0, 1, 2, 2)):
            self.assertIn("Current artist turn execution record: %d scene tool calls" % count,
                          post[3]["messages"][0]["content"])
        history = posts[-2][3]["messages"]
        self.assertEqual([message["role"] for message in history], ["system", "user", "assistant", "tool", "assistant", "tool"])
        output = json.loads(history[-1]["content"])
        self.assertTrue(output["ok"])
        self.assertEqual(output["result"]["node"]["parameters"][0]["raw_value"], "@P.y += 1;")
        self.assertIn("Scene tools:\nRead the current selection", self.widget.transcript.toPlainText())
        self.assertIn("after 2 scene tool calls", self.widget.status.text())
        self.assertNotIn("Text reply only", self.widget.transcript.toPlainText())
        self.assertEqual(self.widget._messages[-1]["content"], "The wrangle raises each point.")
        self.assertIn(panel.toolchat.COMPLETION_REMINDER, posts[-1][3]["messages"][0]["content"])
        self.assertEqual(self.widget.transcript.toPlainText().count("The wrangle raises each point."), 1)

        self.widget.prompt.setPlainText("What would a negative offset do?")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
        followup = StudioHandler.requests[-1][3]["messages"]
        self.assertIn("Current artist turn execution record: 0 scene tool calls", followup[0]["content"])
        self.assertEqual([message["role"] for message in followup], ["system", "user", "assistant", "tool", "assistant", "tool", "assistant", "assistant", "user"])
        self.assertIn("No scene tools were called for this message", self.widget.status.text())

    def test_text_claim_without_tool_call_does_not_count_as_execution(self):
        self.connect(scene_access=True)
        answer = "I created the requested node."
        StudioHandler.chat_responses.append((200, {"choices": [{
            "message": {"content": answer}, "finish_reason": "stop"}]}))
        with mock.patch.object(panel.toolchat, "execute_calls") as reads, mock.patch.object(
            panel.python_actions, "apply"
        ) as python_apply, mock.patch.object(panel.edits, "apply") as typed_apply:
            self.widget.prompt.setPlainText("Create a geometry node")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            reads.assert_not_called()
            python_apply.assert_not_called()
            typed_apply.assert_not_called()
        self.assertIsNone(self.widget._pending_review)
        self.assertEqual(len([r for r in StudioHandler.requests if r[0] == "POST"]), 1)
        self.assertIn("No scene tools were called for this message", self.widget.transcript.toPlainText())
        self.assertIn("No scene tools were called for this message", self.widget.status.text())
        # Local execution evidence is independent of the model's prose and does
        # not become synthetic assistant/tool history or an action to execute.
        self.assertEqual(self.widget._messages[-1], {"role": "assistant", "content": answer})

    def test_discovery_handoff_continues_to_review_without_another_user_prompt(self):
        self.connect(scene_access=True)
        # Match the reported failure: eight reads in two batches, then the
        # model mistakes its per-response cap for the end of the user task.
        for start, count in ((0, 5), (5, 3)):
            calls = [self.tool_call("discover_%d" % index) for index in range(start, start + count)]
            StudioHandler.chat_responses.append((200, {"choices": [{"message": {
                "content": "Checking installed definitions.", "tool_calls": calls}, "finish_reason": "tool_calls"}]}))
        handoff = "The per-response tool limit was reached. Please reply continue to create the network."
        proposal = self.tool_call("create_after_check", "houlm_propose_python", self.python_arguments('result = {"changed": True}'))
        verification = self.tool_call("verify_created", "houlm_inspect_node", {"path": "/obj/TEST"})
        StudioHandler.chat_responses.extend([
            (200, self.final_reply(handoff)),
            (200, self.tool_stream(proposal)),
            (200, self.tool_stream(verification)),
            (200, self.final_reply("Created and verified the requested network.")),
        ])
        plan = types.SimpleNamespace(summary="Create the requested network with native Houdini Python")
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results) as reads, mock.patch.object(
            panel.python_actions, "prepare", return_value=plan
        ), mock.patch.object(panel.python_actions, "apply", return_value={
            "status": "applied", "success": True, "execution_started": True
        }) as apply, mock.patch.object(panel.toolchat, "build_payload", wraps=panel.toolchat.build_payload) as build:
            self.widget.prompt.setPlainText("Create the network, then inspect it to verify the result.")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.assertEqual(reads.call_count, 8)
            self.assertTrue(self.widget._completion_check_used)
            self.assertNotIn(handoff, self.widget.transcript.toPlainText())
            apply.assert_not_called()
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            apply.assert_called_once()
            self.assertEqual(reads.call_args.args[1], [verification])
            checks = [call for call in build.call_args_list if call.kwargs.get("completion_check")]
            self.assertEqual(len(checks), 1)
            self.assertEqual(checks[0].kwargs["turn_budget"]["requests_remaining"], panel.MAX_AGENT_ROUNDS - 3)
            self.assertEqual(checks[0].kwargs["turn_budget"]["tool_calls_remaining"], panel.MAX_AGENT_TOOL_CALLS - 8)
        posts = [r[3] for r in StudioHandler.requests if r[0] == "POST"]
        self.assertEqual(len(posts), 6)
        self.assertEqual(sum(message["role"] == "user" for message in posts[-1]["messages"]), 1)
        self.assertIn("Created and verified", self.widget.transcript.toPlainText())

    def test_completion_check_can_finish_read_only_or_ask_a_question_once(self):
        self.connect(scene_access=True)
        for answer in ("The scene is empty.", "Which object should I modify?"):
            with self.subTest(answer=answer):
                self.widget._new_chat()
                StudioHandler.requests.clear()
                StudioHandler.chat_responses.extend([
                    (200, self.tool_stream(self.tool_call("inspect_before_final"))),
                    (200, self.final_reply(answer)),
                    (200, self.final_reply(answer)),
                ])
                with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results), mock.patch.object(
                    panel.edits, "prepare"
                ) as typed, mock.patch.object(panel.python_actions, "prepare") as python:
                    self.widget.prompt.setPlainText("Inspect the scene and tell me what you need.")
                    self.widget._send()
                    self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
                    typed.assert_not_called()
                    python.assert_not_called()
                self.assertEqual(len([r for r in StudioHandler.requests if r[0] == "POST"]), 3)
                self.assertEqual(self.widget.transcript.toPlainText().count(answer), 1)

    def test_stop_or_new_chat_cancels_queued_completion_check(self):
        self.connect(scene_access=True)
        for stop in (self.widget._cancel_turn, self.widget._new_chat, self.widget.disconnect):
            with self.subTest(stop=stop.__name__):
                self.widget._connected = True
                self.widget._agent_history = [{"role": "user", "content": "Inspect the scene"}]
                self.widget._agent_tool_count = 8
                self.widget._agent_rounds = 3
                self.widget._completion_check_used = False
                callbacks = []
                with mock.patch.object(panel.QtCore.QTimer, "singleShot", side_effect=lambda delay, callback: callbacks.append(callback)):
                    self.widget._agent_reply({"content": "Please continue.", "tool_calls": []})
                self.assertIn("Checking task completion", self.widget.activity_banner.title.text())
                self.assertEqual(len(callbacks), 1)
                with mock.patch.object(self.widget, "_request") as request:
                    stop()
                    callbacks[0]()
                    request.assert_not_called()

    def test_completion_check_respects_proposal_history_and_remaining_budget(self):
        self.connect(scene_access=True)
        for attempted, rounds, count in ((True, 3, 8), (False, panel.MAX_AGENT_ROUNDS - 1, 8), (False, 3, panel.MAX_AGENT_TOOL_CALLS)):
            with self.subTest(attempted=attempted, rounds=rounds, count=count):
                self.widget._agent_history = [{"role": "user", "content": "Work in the scene"}]
                self.widget._proposal_attempted = attempted
                self.widget._completion_check_used = False
                self.widget._agent_rounds = rounds
                self.widget._agent_tool_count = count
                with mock.patch.object(panel.QtCore.QTimer, "singleShot") as queued:
                    self.widget._agent_reply({"content": "The task cannot continue.", "tool_calls": []})
                    queued.assert_not_called()
                self.assertIsNone(self.widget._agent_history)
        self.assertEqual(self.widget._turn_budget(new_turn=True)["requests_remaining"], panel.MAX_AGENT_ROUNDS)
        self.widget._agent_rounds = panel.MAX_AGENT_ROUNDS - 1
        self.widget._agent_tool_count = 8
        self.assertEqual(self.widget._turn_budget()["tool_calls_remaining"], 0)

    def test_new_user_turn_resets_completion_check_and_proposal_state(self):
        self.connect(scene_access=True)
        self.widget._completion_check_used = True
        self.widget._proposal_attempted = True
        self.widget._agent_rounds = panel.MAX_AGENT_ROUNDS
        self.widget._agent_tool_count = panel.MAX_AGENT_TOOL_CALLS
        with mock.patch.object(panel.toolchat, "build_payload", wraps=panel.toolchat.build_payload) as build:
            self.widget.prompt.setPlainText("Explain the current scene")
            self.widget._send()
            self.assertFalse(self.widget._completion_check_used)
            self.assertFalse(self.widget._proposal_attempted)
            self.assertEqual(build.call_args.kwargs["turn_budget"]["requests_remaining"], panel.MAX_AGENT_ROUNDS)
            self.assertEqual(build.call_args.kwargs["turn_budget"]["tool_calls_remaining"], panel.MAX_AGENT_TOOL_CALLS)
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)

    def test_plain_chat_reports_disabled_scene_access(self):
        self.connect(scene_access=False)
        self.widget.prompt.setPlainText("Create a geometry node")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None)
        request = next(r for r in StudioHandler.requests if r[0] == "POST")
        self.assertNotIn("tools", request[3])
        self.assertIn("Scene access is off", self.widget.status.text())
        self.assertIn("no scene commands ran", self.widget.status.text())

    def test_scene_tool_provider_state_is_echoed_but_never_displayed(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        intermediate = [{"id": "rs_provider_1", "type": "reasoning",
                         "encrypted_content": "OPAQUE_INTERMEDIATE_STATE",
                         "summary": [{"type": "summary_text", "text": "HIDDEN_INTERMEDIATE_SUMMARY"}]}]
        final = [{"id": "rs_provider_2", "type": "reasoning",
                  "encrypted_content": "OPAQUE_FINAL_STATE",
                  "summary": [{"type": "summary_text", "text": "HIDDEN_FINAL_SUMMARY"}]}]
        checked = [{"id": "rs_provider_3", "type": "reasoning",
                    "encrypted_content": "OPAQUE_CHECKED_STATE",
                    "summary": []}]
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("state_1"), intermediate)),
            (200, {"choices": [{"message": {"content": "The scene is ready.", "reasoning_items": final}, "finish_reason": "stop"}]}),
            (200, {"choices": [{"message": {"content": "The scene is ready.", "reasoning_items": checked}, "finish_reason": "stop"}]}),
        ])
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results):
            self.widget.prompt.setPlainText("Inspect this session")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
        sent = StudioHandler.requests[-1][3]["messages"]
        self.assertEqual(sent[-3]["reasoning_items"], intermediate)
        self.assertEqual(sent[-1]["reasoning_items"], final)
        self.assertEqual(self.widget._messages[-1]["reasoning_items"], checked)
        self.widget.prompt.setPlainText("Tell me more")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
        followup = StudioHandler.requests[-1][3]["messages"]
        self.assertEqual(followup[-2]["reasoning_items"], checked)
        self.assertEqual(followup[2]["reasoning_items"], intermediate)
        self.assertNotIn("OPAQUE_", self.widget.transcript.toPlainText())
        self.assertNotIn("HIDDEN_", self.widget.transcript.toPlainText())

    def test_scene_tool_failure_keeps_completed_batches_for_resume(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        self.widget.prompt.setPlainText("Say OK")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
        saved_history = list(self.widget._messages)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("call_1"))),
            (400, {"detail": "Unknown error with PRIVATE_CONTENT"}),
        ])
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results):
            self.widget.prompt.setPlainText("Inspect the scene")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
        self.assertEqual(self.widget._messages[:len(saved_history)], saved_history)
        self.assertEqual([item["role"] for item in self.widget._messages[len(saved_history):]], ["user", "assistant", "tool"])
        self.assertEqual(self.widget._messages[-1]["tool_call_id"], "call_1")
        panel.toolchat.build_payload("studio-chatgpt", self.widget._messages)
        self.assertEqual(self.widget.prompt.toPlainText(), "Inspect the scene")
        self.assertIn("[Request failed]", self.widget.transcript.toPlainText())
        self.assertNotIn("PRIVATE_CONTENT", self.widget.transcript.toPlainText())

    def test_scene_tool_round_limit_preserves_completed_reads_without_exceeding_limit(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        StudioHandler.chat_responses.extend((200, self.tool_stream(self.tool_call("round_%d" % index))) for index in range(panel.MAX_AGENT_ROUNDS))
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results) as execute:
            self.widget.prompt.setPlainText("Inspect everything")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            self.assertEqual(execute.call_count, panel.MAX_AGENT_ROUNDS - 1)
        self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), panel.MAX_AGENT_ROUNDS)
        self.assertEqual(sum(m["role"] == "tool" for m in self.widget._messages), panel.MAX_AGENT_ROUNDS - 1)
        panel.toolchat.build_payload("studio-chatgpt", self.widget._messages)
        self.assertEqual(self.widget.prompt.toPlainText(), "Inspect everything")
        self.assertIn("%d-round limit" % panel.MAX_AGENT_ROUNDS, self.widget.status.text())

    def test_scene_tool_total_query_limit_stops_before_next_batch(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        batches = panel.MAX_AGENT_TOOL_CALLS // 8
        for round_index in range(batches + 1):
            calls = [self.tool_call("round_%d_call_%d" % (round_index, index)) for index in range(8)]
            StudioHandler.chat_responses.append((200, {"choices": [{"message": {"content": "", "tool_calls": calls}, "finish_reason": "tool_calls"}]}))
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results) as execute:
            self.widget.prompt.setPlainText("Inspect everything")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            executed_calls = [call for batch in execute.call_args_list for call in batch.args[1]]
            self.assertEqual(len(executed_calls), panel.MAX_AGENT_TOOL_CALLS)
            self.assertNotIn("round_%d_call_0" % batches, [call["id"] for call in executed_calls])
        self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), batches + 1)
        self.assertIn("%d-query limit" % panel.MAX_AGENT_TOOL_CALLS, self.widget.status.text())
        self.assertEqual(sum(m["role"] == "tool" for m in self.widget._messages), panel.MAX_AGENT_TOOL_CALLS)
        self.assertNotIn("round_%d_call_0" % batches, json.dumps(self.widget._messages))
        panel.toolchat.build_payload("studio-chatgpt", self.widget._messages)

    def test_new_chat_cancels_queued_tool_continuation(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        StudioHandler.chat_responses.append((200, self.tool_stream(self.tool_call("queued_1"))))
        callbacks = []
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results), mock.patch.object(
            panel.QtCore.QTimer, "singleShot", side_effect=lambda delay, callback: callbacks.append(callback)
        ):
            self.widget.prompt.setPlainText("Inspect this session")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and bool(callbacks))
        self.assertIsNotNone(self.widget._agent_history)
        self.assertFalse(self.widget.send_button.isEnabled())
        self.widget._new_chat()
        callbacks[0]()
        self.app.processEvents()
        self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), 1)
        self.assertIsNone(self.widget._agent_history)
        self.assertIsNone(self.widget._reply)
        self.assertEqual(self.widget._messages, [])

    def test_scene_tool_toggle_clears_history_before_plain_chat(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        self.widget._messages = [{"role": "tool", "tool_call_id": "old", "content": "old scene context"}]
        self.widget.transcript.setPlainText("Old scene context")
        self.widget.scene_tools.setChecked(False)
        self.assertEqual(self.widget._messages, [])
        self.assertEqual(self.widget.transcript.toPlainText(), "")
        self.widget.prompt.setPlainText("Hello")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None)
        self.assertNotIn("tools", StudioHandler.requests[-1][3])

    def test_edit_proposal_waits_for_apply_then_continues_with_verification(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        self.assertTrue(self.widget.scene_tools.isChecked())
        proposal = self.tool_call("edit_1", "houlm_propose_edits", {"operations": [{"op": "set_parameter", "path": "/obj/geo/box", "parameter": "tx", "value": 2}]})
        selection = self.tool_call("read_1", "houlm_get_selection")
        verification = self.tool_call("verify_1", "houlm_inspect_node", {"path": "/obj/geo/box"})
        StudioHandler.chat_responses.extend([
            (200, {"choices": [{"message": {"content": "", "tool_calls": [proposal, selection]}, "finish_reason": "tool_calls"}]}),
            (200, self.tool_stream(verification)),
            (200, {"choices": [{"message": {"content": "The box now has the reviewed offset."}, "finish_reason": "stop"}]}),
        ])
        plan = types.SimpleNamespace(summary="1. /obj/geo/box tx: 0 -> 2")
        applied = {"status": "applied", "success": True, "changed_paths": ["/obj/geo/box"], "completed_operations": 1}
        with mock.patch.object(panel.edits, "prepare", return_value=plan) as prepare, mock.patch.object(
            panel.edits, "apply", return_value=applied
        ) as apply, mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results) as reads:
            self.widget.prompt.setPlainText("Move this box")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.assertIsNone(self.widget._reply)
            self.assertFalse(self.widget.send_button.isEnabled())
            self.assertEqual(self.widget._messages, [])
            self.assertIn(plan.summary, self.widget._review_dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText())
            apply.assert_not_called()
            prepare.assert_called_once_with(panel.hou, json.loads(proposal["function"]["arguments"]))
            self.assertEqual(reads.call_args.args[1], [selection])
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            apply.assert_called_once_with(panel.hou, plan)
            self.assertEqual([call.args[1] for call in reads.call_args_list], [[selection], [verification]])
        posts = [request[3] for request in StudioHandler.requests if request[0] == "POST"]
        self.assertIn("houlm_propose_edits", [tool["function"]["name"] for tool in posts[0]["tools"]])
        self.assertEqual([message.get("tool_call_id") for message in posts[1]["messages"][-2:]], ["edit_1", "read_1"])
        self.assertEqual(json.loads(posts[1]["messages"][-2]["content"]), applied)
        self.assertIn("Applied reviewed scene changes", self.widget.transcript.toPlainText())
        self.assertIsNone(self.widget._review_dialog)

    def test_streamed_spare_parameter_add_and_set_requires_review_then_verifies(self):
        from test_edits import Hou, arguments, parameter, spare
        current_hou = Hou()
        current_hou.session = types.SimpleNamespace()
        current_hou.applicationVersionString = lambda: "20.5.445"
        current_hou.hipFile = types.SimpleNamespace(basename=lambda: "panel-test.hip")
        node = current_hou.a
        before = node.parmTemplateGroup().asDialogScript()
        proposal = arguments(
            spare(path=node.path(), name="falloff", default=0.5,
                  min_value=0, max_value=1, min_strict=True, max_strict=True),
            parameter(path=node.path(), name="falloff", value=0.75),
        )
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("spare_rejected", "houlm_propose_edits", proposal))),
            (200, self.final_reply("The proposal was rejected; nothing was changed.")),
        ])
        with mock.patch.object(panel, "hou", current_hou), mock.patch.object(
            panel.edits, "prepare", wraps=panel.edits.prepare
        ) as prepare, mock.patch.object(panel.edits, "apply", wraps=panel.edits.apply) as apply:
            self.widget.prompt.setPlainText("Add a falloff slider and set it to 0.75")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            review = self.widget._review_dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText()
            self.assertIn("falloff", review)
            self.assertIn("0.5", review)
            self.assertIn("0.75", review)
            self.assertIsNone(node.parm("falloff"))
            self.assertEqual(current_hou.layout_calls, [])
            apply.assert_not_called()
            self.widget.review_reject_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            self.assertEqual(node.parmTemplateGroup().asDialogScript(), before)
            self.assertEqual(current_hou.layout_calls, [])
            self.assertEqual(current_hou.undos.groups, [])
            apply.assert_not_called()
            rejected = json.loads(StudioHandler.requests[-1][3]["messages"][-1]["content"])
            self.assertEqual(rejected["status"], "rejected")

            StudioHandler.chat_responses.extend([
                (200, self.tool_stream(self.tool_call("spare_applied", "houlm_propose_edits", proposal))),
                (200, self.tool_stream(self.tool_call("spare_verified", "houlm_parameter_schema", {"path": node.path(), "query": "falloff"}))),
                (200, self.final_reply("Falloff was added and its authored value is 0.75.")),
            ])
            self.widget.prompt.setPlainText("Go ahead with those values; show the proposal again")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.assertIsNone(node.parm("falloff"))
            self.assertEqual(current_hou.layout_calls, [])
            apply.assert_not_called()
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            self.assertEqual(prepare.call_count, 2)
            apply.assert_called_once()
        self.assertEqual(current_hou.layout_calls, [node.path()])
        self.assertEqual(len(current_hou.undos.groups), 1)
        self.assertTrue(node.parm("falloff").isSpare())
        self.assertEqual(node.parm("falloff").value, 0.75)
        self.assertEqual(node.parm("falloff").template.defaults, (0.5,))
        posts = [request for request in StudioHandler.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 5)
        self.assertTrue(all(request[1] == "/api/chat/completions" for request in posts))
        applied = json.loads(posts[-2][3]["messages"][-1]["content"])
        self.assertEqual(applied["status"], "applied")
        self.assertTrue(applied["success"])
        self.assertEqual(applied["completed_operations"], 2)
        self.assertEqual(applied["changed_paths"], [node.path()])
        verification = json.loads(posts[-1][3]["messages"][-1]["content"])
        self.assertTrue(verification["ok"])
        record = verification["result"]["parameters"][0]
        self.assertEqual(record["name"], "falloff")
        self.assertEqual(record["raw_value"], "0.75")
        self.assertTrue(record["is_spare"])
        self.assertEqual((record["minimum"], record["maximum"]), (0, 1))
        self.assertTrue(record["minimum_is_strict"])
        self.assertTrue(record["maximum_is_strict"])
        self.assertIn("Falloff was added", self.widget.transcript.toPlainText())

    def test_edit_reject_and_window_close_never_apply(self):
        for close_window in (False, True):
            with self.subTest(close_window=close_window):
                if not self.widget._connected:
                    self.connect()
                self.widget.scene_tools.setChecked(True)
                self.widget._new_chat()
                StudioHandler.chat_responses.append((200, self.tool_stream(self.tool_call("reject_1", "houlm_propose_edits"))))
                with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Concrete change")), mock.patch.object(panel.edits, "apply") as apply:
                    self.widget.prompt.setPlainText("Make a box")
                    self.widget._send()
                    self.wait_for(lambda: self.widget._pending_review is not None)
                    if close_window:
                        self.widget._review_dialog.reject()
                    else:
                        self.widget.review_reject_button.click()
                    self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
                    apply.assert_not_called()
                result = json.loads(StudioHandler.requests[-1][3]["messages"][-1]["content"])
                self.assertEqual(result["status"], "rejected")
                self.assertIn("proposal rejected", self.widget.transcript.toPlainText())

    def test_applied_edit_history_survives_later_network_failure_and_retry(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("committed_edit", "houlm_propose_edits"))),
            (503, {"error": "PRIVATE_SERVER_ERROR"}),
        ])
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Concrete change")), mock.patch.object(
            panel.edits, "apply", return_value={"status": "applied", "success": True, "changed_paths": ["/obj/box1"], "completed_operations": 1}
        ) as apply:
            self.widget.prompt.setPlainText("Make a box")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            self.assertEqual([message["role"] for message in self.widget._messages], ["user", "assistant", "tool"])
            self.assertEqual(json.loads(self.widget._messages[-1]["content"])["status"], "applied")
            self.assertIn("remain in Houdini", self.widget.status.text())
            self.assertEqual(self.widget.prompt.toPlainText(), "Make a box")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            apply.assert_called_once()
        history = StudioHandler.requests[-1][3]["messages"]
        self.assertEqual([message["role"] for message in history], ["system", "user", "assistant", "tool", "user"])
        self.assertEqual(json.loads(history[-2]["content"])["status"], "applied")

    def test_two_edit_proposals_are_rejected_without_preparing(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        calls = [self.tool_call("edit_%d" % index, "houlm_propose_edits") for index in range(2)]
        StudioHandler.chat_responses.append((200, {"choices": [{"message": {"content": "", "tool_calls": calls}, "finish_reason": "tool_calls"}]}))
        with mock.patch.object(panel.edits, "prepare") as prepare, mock.patch.object(panel.edits, "apply") as apply:
            self.widget.prompt.setPlainText("Make two boxes")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            prepare.assert_not_called()
            apply.assert_not_called()
        self.assertTrue(all(json.loads(message["content"])["status"] == "not_permitted" for message in StudioHandler.requests[-1][3]["messages"][-2:]))

    @staticmethod
    def python_hou():
        from test_edits import Hou
        current = Hou()
        current.selectedNodes = lambda: (current.a,)
        current.applicationVersionString = lambda: "20.5.test"
        current.hipFile = types.SimpleNamespace(basename=lambda: "untitled.hip", path=lambda: "untitled.hip")
        return current

    @staticmethod
    def python_arguments(code, target_paths=None):
        return {"summary": "Use the installed Houdini API", "code": code,
                "target_paths": ["/obj/geo1/a"] if target_paths is None else target_paths}

    def test_python_exact_source_review_reject_and_close_never_execute(self):
        self.connect(scene_access=True)
        current = self.python_hou()
        code = 'print("reviewed inspection")\nresult = {"value": hou.node("/obj/geo1/a").parm("density").rawValue()}\n'
        for close_window in (False, True):
            with self.subTest(close_window=close_window):
                self.widget._new_chat()
                StudioHandler.chat_responses.extend([
                    (200, self.tool_stream(self.tool_call("python_reject", "houlm_propose_python", self.python_arguments(code)))),
                    (200, self.final_reply("The Python was not run.")),
                ])
                with mock.patch.object(panel, "hou", current), mock.patch.object(
                    panel.python_actions, "prepare", wraps=panel.python_actions.prepare
                ) as prepare, mock.patch.object(panel.python_actions, "apply", wraps=panel.python_actions.apply) as apply:
                    self.widget.prompt.setPlainText("Inspect the density using Python")
                    self.widget._send()
                    self.wait_for(lambda: self.widget._pending_review is not None)
                    self.assertEqual(self.widget._review_dialog.windowTitle(), "Review Houdini Python")
                    self.assertIn(code, self.widget._review_dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText())
                    hint = self.widget._review_dialog.findChild(QtWidgets.QLabel, "dialogHint").text()
                    self.assertIn("Houdini's permissions", hint)
                    self.assertIn("does not guarantee rollback", hint)
                    self.assertEqual(self.widget.review_apply_button.text(), "Apply Python")
                    prepare.assert_called_once()
                    apply.assert_not_called()
                    if close_window:
                        self.widget._review_dialog.reject()
                    else:
                        self.widget.review_reject_button.click()
                    self.wait_for(lambda: self.widget._agent_history is None and self.widget._reply is None)
                    apply.assert_not_called()
                result = json.loads(StudioHandler.requests[-1][3]["messages"][-1]["content"])
                self.assertEqual(result["status"], "rejected")
                self.assertFalse(result["execution_started"])
                self.assertIn("Houdini Python: proposal rejected", self.widget.transcript.toPlainText())

    def test_python_apply_runs_exact_source_then_returns_ordered_results_and_inspects(self):
        self.connect(scene_access=True)
        current = self.python_hou()
        code = ('hou.node("/obj/geo1/a").parm("density").set(2.5, follow_parm_reference=False)\n'
                'print("updated density")\nresult = {"value": hou.node("/obj/geo1/a").parm("density").rawValue()}\n')
        proposal = self.tool_call("python_apply", "houlm_propose_python", self.python_arguments(code))
        selection = self.tool_call("python_selection", "houlm_get_selection")
        StudioHandler.chat_responses.extend([
            (200, {"choices": [{"message": {"content": "", "tool_calls": [proposal, selection]}, "finish_reason": "tool_calls"}]}),
            (200, self.tool_stream(self.tool_call("python_verify", "houlm_parameter_schema", {"path": "/obj/geo1/a"}))),
            (200, self.final_reply("The density is now 2.5.")),
        ])
        with mock.patch.object(panel, "hou", current), mock.patch.object(
            panel.python_actions, "apply", wraps=panel.python_actions.apply
        ) as apply, mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results) as reads:
            self.widget.prompt.setPlainText("Update the density with the general Houdini API")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.assertEqual(current.a.parm("density").value, 1.0)
            self.assertEqual(reads.call_count, 1)
            apply.assert_not_called()
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._agent_history is None and self.widget._reply is None)
            apply.assert_called_once()
            self.assertTrue(callable(apply.call_args.kwargs["cancelled"]))
            self.assertEqual(reads.call_count, 2)
        self.assertEqual(current.a.parm("density").value, 2.5)
        posts = [request[3] for request in StudioHandler.requests if request[0] == "POST"]
        self.assertIn("houlm_propose_python", [tool["function"]["name"] for tool in posts[0]["tools"]])
        results = posts[1]["messages"][-2:]
        self.assertEqual([result["tool_call_id"] for result in results], ["python_apply", "python_selection"])
        outcome = json.loads(results[0]["content"])
        self.assertTrue(outcome["success"])
        self.assertTrue(outcome["execution_started"])
        self.assertEqual(outcome["result"]["value"], "2.5")
        self.assertIn("updated density", outcome["stdout"])
        self.assertIn("Ran reviewed Houdini Python", self.widget.transcript.toPlainText())

    def test_empty_python_delta_then_message_snapshot_requires_exact_source_review(self):
        self.connect(scene_access=True)
        current = self.python_hou()
        code = ('hou.node("/obj/geo1/a").parm("density").set(2.5, follow_parm_reference=False)\n'
                'result = {"value": hou.node("/obj/geo1/a").parm("density").rawValue()}\n')
        arguments = self.python_arguments(code)
        proposal = self.tool_call("python_snapshot", "houlm_propose_python", arguments)
        response = self.sse_events([
            {"choices": [{"delta": {"tool_calls": [{"index": 20, "id": proposal["id"], "type": "function",
                "function": {"name": "houlm_propose_python", "arguments": ""}}]}}]},
            {"choices": [{"message": {"content": "", "tool_calls": [proposal]}, "finish_reason": "tool_calls"}]},
        ])
        StudioHandler.chat_responses.extend([(200, response), (200, self.final_reply("Density verified at 2.5."))])
        with mock.patch.object(panel, "hou", current), mock.patch.object(
            panel.python_actions, "prepare", wraps=panel.python_actions.prepare
        ) as prepare, mock.patch.object(
            panel.python_actions, "apply", wraps=panel.python_actions.apply
        ) as apply, mock.patch.object(panel.toolchat, "execute_calls") as reads:
            self.widget.prompt.setPlainText("Update density using Python")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            prepare.assert_called_once_with(current, arguments)
            self.assertEqual(self.widget._review_dialog.windowTitle(), "Review Houdini Python")
            self.assertIn(code, self.widget._review_dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText())
            self.assertEqual(current.a.parm("density").value, 1.0)
            apply.assert_not_called()
            reads.assert_not_called()
            self.assertFalse(self.widget._argument_retry_used)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            apply.assert_called_once()
        self.assertEqual(current.a.parm("density").value, 2.5)
        posts = [request[3] for request in StudioHandler.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[1]["messages"][-2]["tool_calls"], [proposal])
        outcome = json.loads(posts[1]["messages"][-1]["content"])
        self.assertTrue(outcome["success"])
        self.assertTrue(outcome["execution_started"])
        self.assertEqual(outcome["result"]["value"], "2.5")
        self.assertEqual(self.widget._last_error_details, "")

    def test_invalid_or_conflicting_python_snapshot_never_prepares_or_applies(self):
        self.connect(scene_access=True)
        current = self.python_hou()
        for variant, code, attempts in (
            ("malformed", "tool_arguments_invalid_json", 2),
            ("different_id", "conflicting_stream_snapshot", 1),
            ("different_arguments", "conflicting_stream_snapshot", 1),
        ):
            with self.subTest(variant=variant):
                self.widget._new_chat()
                StudioHandler.requests.clear()
                proposal = self.tool_call("python_snapshot", "houlm_propose_python",
                    self.python_arguments('hou.node("/obj/geo1/a").parm("density").set(9.0)'))
                prefix = ""
                if variant == "malformed":
                    proposal["function"]["arguments"] = '{"PRIVATE_SOURCE":"test-only-session-key",'
                elif variant == "different_id":
                    proposal["id"] = "changed_identity"
                else:
                    prefix = '{"summary": "A different proposal'
                response = self.sse_events([
                    {"choices": [{"delta": {"tool_calls": [{"index": 20, "id": "python_snapshot", "type": "function",
                        "function": {"name": "houlm_propose_python", "arguments": prefix}}]}}]},
                    {"choices": [{"message": {"tool_calls": [proposal]}, "finish_reason": "tool_calls"}]},
                ])
                StudioHandler.chat_responses.extend([(200, response)] * attempts)
                with mock.patch.object(panel, "hou", current), mock.patch.object(
                    panel.python_actions, "prepare"
                ) as prepare, mock.patch.object(panel.python_actions, "apply") as apply, mock.patch.object(
                    panel.toolchat, "execute_calls"
                ) as reads:
                    self.widget.prompt.setPlainText("Update density using Python")
                    self.widget._send()
                    self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
                    prepare.assert_not_called()
                    apply.assert_not_called()
                    reads.assert_not_called()
                self.assertIsNone(self.widget._pending_review)
                self.assertIsNone(self.widget._review_dialog)
                self.assertEqual(current.a.parm("density").value, 1.0)
                self.assertIn(code, self.widget._last_error_details)
                self.assertNotIn("PRIVATE_SOURCE", self.widget._last_error_details)
                self.assertNotIn("test-only-session-key", self.widget._last_error_details)
                self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), attempts)

    def test_python_partial_failure_keeps_tool_history_when_followup_request_fails(self):
        self.connect(scene_access=True)
        current = self.python_hou()
        code = ('hou.node("/obj/geo1/a").parm("density").set(3.0, follow_parm_reference=False)\n'
                'print("changed before failure")\nraise RuntimeError("Synthetic execution failure")\n')
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("python_partial", "houlm_propose_python", self.python_arguments(code)))),
            (503, {"error": "PRIVATE_SERVER_ERROR"}),
        ])
        with mock.patch.object(panel, "hou", current), mock.patch.object(
            panel.python_actions, "apply", wraps=panel.python_actions.apply
        ) as apply:
            self.widget.prompt.setPlainText("Run the reviewed change")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._agent_history is None and self.widget._reply is None)
            apply.assert_called_once()
        self.assertEqual(current.a.parm("density").value, 3.0)
        self.assertEqual([item["role"] for item in self.widget._messages], ["user", "assistant", "tool"])
        outcome = json.loads(self.widget._messages[-1]["content"])
        self.assertFalse(outcome["success"])
        self.assertTrue(outcome["execution_started"])
        self.assertEqual(outcome["rollback"], "not_attempted")
        self.assertTrue(outcome["manual_undo_needed"])
        self.assertIn("no automatic rollback", self.widget._last_edit_details)
        self.assertIn("remain in Houdini", self.widget.status.text())
        self.assertEqual(len([r for r in StudioHandler.requests if r[0] == "POST"]), 2)
        self.assertFalse(self.widget._recovery_pending)

    def test_python_failure_gets_model_result_without_automatic_recovery_round(self):
        self.connect(scene_access=True)
        current = self.python_hou()
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("python_failed", "houlm_propose_python", self.python_arguments('raise RuntimeError("Stop here")')))),
            (200, self.final_reply("The Python failed; inspect the scene before a repair.")),
        ])
        with mock.patch.object(panel, "hou", current), mock.patch.object(panel.toolchat, "build_payload", wraps=panel.toolchat.build_payload) as build:
            self.widget.prompt.setPlainText("Run this proposed action")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._agent_history is None and self.widget._reply is None)
        self.assertEqual(len([r for r in StudioHandler.requests if r[0] == "POST"]), 2)
        self.assertFalse(any(call.kwargs.get("recovery_reminder") for call in build.call_args_list))
        result = json.loads(StudioHandler.requests[-1][3]["messages"][-1]["content"])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["execution_started"])
        self.assertIn("Python execution started", self.widget._last_edit_details)

    def test_new_chat_and_scene_access_cancel_pending_python_review(self):
        self.connect(scene_access=True)
        current = self.python_hou()
        for disable_scene in (False, True):
            with self.subTest(disable_scene=disable_scene):
                self.widget.scene_tools.setChecked(True)
                StudioHandler.chat_responses.append((200, self.tool_stream(self.tool_call(
                    "cancel_python", "houlm_propose_python", self.python_arguments('result = {"value": 1}')))))
                with mock.patch.object(panel, "hou", current), mock.patch.object(panel.python_actions, "apply", wraps=panel.python_actions.apply) as apply:
                    self.widget.prompt.setPlainText("Inspect with reviewed Python")
                    self.widget._send()
                    self.wait_for(lambda: self.widget._pending_review is not None)
                    generation = self.widget._generation
                    if disable_scene:
                        self.widget.scene_tools.setChecked(False)
                    else:
                        self.widget._new_chat()
                    self.widget._resolve_review(True, generation)
                    self.app.processEvents()
                    apply.assert_not_called()
                self.assertIsNone(self.widget._pending_review)
                self.assertIsNone(self.widget._review_dialog)

    def test_active_python_cancellation_stops_continuation_and_reports_remaining_changes(self):
        self.connect(scene_access=True)
        current = self.python_hou()
        code = ('hou.node("/obj/geo1/a").parm("density").set(3.0, follow_parm_reference=False)\n'
                'hou.node("/obj/geo1/b").parm("density").set(9.0, follow_parm_reference=False)\n')
        StudioHandler.chat_responses.append((200, self.tool_stream(self.tool_call(
            "python_cancel_running", "houlm_propose_python", self.python_arguments(code)))))
        current.a.parm("density").side_effect = self.widget._new_chat
        with mock.patch.object(panel, "hou", current), mock.patch.object(
            panel.python_actions, "apply", wraps=panel.python_actions.apply
        ) as apply:
            self.widget.prompt.setPlainText("Run this reviewed action")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.app.processEvents()
            apply.assert_called_once()
        self.assertEqual(current.a.parm("density").value, 3.0)
        self.assertEqual(current.b.parm("density").value, 1.0)
        self.assertIsNone(self.widget._agent_history)
        self.assertIsNone(self.widget._pending_review)
        self.assertEqual(len([r for r in StudioHandler.requests if r[0] == "POST"]), 1)
        self.assertIn("interrupted after starting", self.widget.status.text())
        self.assertIn("Changes may remain", self.widget.status.text())

    def test_python_changed_target_rejects_execution_without_claiming_scene_change(self):
        self.connect(scene_access=True)
        current = self.python_hou()
        code = 'hou.node("/obj/geo1/a").parm("density").set(4.0, follow_parm_reference=False)'
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("python_stale", "houlm_propose_python", self.python_arguments(code)))),
            (200, self.final_reply("The target changed; inspect it before a fresh proposal.")),
        ])
        with mock.patch.object(panel, "hou", current):
            self.widget.prompt.setPlainText("Update this target")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            current.selectedNodes = lambda: ()
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._agent_history is None and self.widget._reply is None)
        self.assertEqual(current.a.parm("density").value, 1.0)
        result = json.loads(StudioHandler.requests[-1][3]["messages"][-1]["content"])
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["manual_undo_needed"])
        self.assertIn("was not executed", self.widget._last_edit_details)

    def test_mixed_and_duplicate_python_proposals_are_rejected_before_preparation(self):
        self.connect(scene_access=True)
        for names in (("houlm_propose_edits", "houlm_propose_python"), ("houlm_propose_python", "houlm_propose_python")):
            with self.subTest(names=names):
                self.widget._new_chat()
                calls = [self.tool_call("proposal_%d" % index, name) for index, name in enumerate(names)]
                StudioHandler.chat_responses.append((200, {"choices": [{"message": {"content": "", "tool_calls": calls}, "finish_reason": "tool_calls"}]}))
                with mock.patch.object(panel.edits, "prepare") as typed_prepare, mock.patch.object(panel.edits, "apply") as typed_apply, mock.patch.object(
                    panel.python_actions, "prepare"
                ) as python_prepare, mock.patch.object(panel.python_actions, "apply") as python_apply:
                    self.widget.prompt.setPlainText("Make a scene change")
                    self.widget._send()
                    self.wait_for(lambda: self.widget._agent_history is None and self.widget._reply is None)
                    for method in (typed_prepare, typed_apply, python_prepare, python_apply):
                        method.assert_not_called()
                results = StudioHandler.requests[-1][3]["messages"][-2:]
                self.assertEqual([item["tool_call_id"] for item in results], [item["id"] for item in calls])
                self.assertTrue(all(json.loads(item["content"])["status"] == "not_permitted" for item in results))

    def test_disabled_scene_access_never_prepares_or_applies_edits(self):
        self.connect(scene_access=False)
        StudioHandler.chat_responses.append((200, self.tool_stream(self.tool_call("unsolicited_edit_1", "houlm_propose_edits"))))
        with mock.patch.object(panel.edits, "prepare") as prepare, mock.patch.object(panel.edits, "apply") as apply:
            self.widget.prompt.setPlainText("Explain the scene")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            prepare.assert_not_called()
            apply.assert_not_called()
        posts = [request[3] for request in StudioHandler.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertNotIn("tools", posts[0])
        self.assertIsNone(self.widget._pending_review)
        self.assertIsNone(self.widget._review_dialog)

    def test_new_chat_invalidates_pending_edit_approval(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        StudioHandler.chat_responses.append((200, self.tool_stream(self.tool_call("stale_review", "houlm_propose_edits"))))
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Concrete change")), mock.patch.object(panel.edits, "apply") as apply:
            self.widget.prompt.setPlainText("Make a box")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            generation = self.widget._generation
            self.widget._new_chat()
            self.widget._resolve_review(True, generation)
            self.app.processEvents()
            apply.assert_not_called()
        self.assertIsNone(self.widget._pending_review)
        self.assertIsNone(self.widget._review_dialog)
        self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), 1)

    def test_invalid_edit_proposal_hides_exception_and_does_not_apply(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        StudioHandler.chat_responses.append((200, self.tool_stream(self.tool_call("bad_plan", "houlm_propose_edits"))))
        with mock.patch.object(panel.edits, "prepare", side_effect=ValueError("PRIVATE_EXCEPTION")), mock.patch.object(panel.edits, "apply") as apply:
            self.widget.prompt.setPlainText("Make a box")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            apply.assert_not_called()
        self.assertIsNone(self.widget._review_dialog)
        result = StudioHandler.requests[-1][3]["messages"][-1]["content"]
        self.assertEqual(json.loads(result)["status"], "invalid")
        self.assertNotIn("PRIVATE_EXCEPTION", result)

    def test_typed_edit_validation_error_reaches_model_for_correction(self):
        self.connect()
        self.widget.scene_tools.setChecked(True)
        StudioHandler.chat_responses.append((200, self.tool_stream(self.tool_call("correctable_plan", "houlm_propose_edits"))))
        diagnostic = "Parameter does not exist on /obj/box1. Inspect parameter_schema first."
        with mock.patch.object(panel.edits, "prepare", side_effect=panel.edits.EditError(diagnostic)), mock.patch.object(panel.edits, "apply") as apply:
            self.widget.prompt.setPlainText("Resize the box")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            apply.assert_not_called()
        result = json.loads(StudioHandler.requests[-1][3]["messages"][-1]["content"])
        self.assertEqual(result, {"status": "invalid", "success": False, "message": diagnostic,
                                  "error": {"code": "invalid_edit", "message": diagnostic}})

    @staticmethod
    def clean_apply_failure():
        return {"status": "failed", "success": False, "completed_operations": 1,
                "rollback": "reverted_scene_fields", "manual_undo_needed": False,
                "failed_operation": {"index": 2, "op": "set_parameter", "path": "/obj/box1", "parameter": "bad_size"},
                "error": {"code": "parameter_missing", "message": "Use an existing parameter name."}}

    @staticmethod
    def final_reply(content):
        return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}

    def test_clean_apply_failure_gets_one_recovery_and_correction_requires_review(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("failed_proposal", "houlm_propose_edits"))),
            (200, self.final_reply("PREMATURE_STOP")),
            (200, self.tool_stream(self.tool_call("corrected_proposal", "houlm_propose_edits"))),
            (200, self.final_reply("The corrected parameter is applied.")),
        ])
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Resize the box")), mock.patch.object(
            panel.edits, "apply", side_effect=[self.clean_apply_failure(), {"status": "applied", "success": True, "completed_operations": 1}]
        ) as apply, mock.patch.object(panel.toolchat, "build_payload", wraps=panel.toolchat.build_payload) as build:
            self.widget.prompt.setPlainText("Resize the box")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.assertEqual(apply.call_count, 1)
            self.assertFalse(self.widget._recovery_pending)
            self.assertNotIn("PREMATURE_STOP", self.widget.transcript.toPlainText())
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            self.assertEqual(apply.call_count, 2)
            self.assertEqual(sum(call.kwargs.get("recovery_reminder") is True for call in build.call_args_list), 1)
        posts = [request[3] for request in StudioHandler.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 4)
        self.assertIn(panel.toolchat.RECOVERY_REMINDER, posts[2]["messages"][0]["content"])
        self.assertNotIn(panel.toolchat.RECOVERY_REMINDER, posts[3]["messages"][0]["content"])
        self.assertTrue(any(message.get("content") == "PREMATURE_STOP" for message in self.widget._messages))
        self.assertIn("corrected parameter is applied", self.widget.transcript.toPlainText())

    def test_recovery_stops_after_one_nudge_and_resets_on_next_user_turn(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("single_recovery", "houlm_propose_edits"))),
            (200, self.final_reply("PREMATURE_STOP")),
            (200, self.final_reply("The available tools cannot create this dependency.")),
        ])
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Create a dependency")), mock.patch.object(
            panel.edits, "apply", return_value=self.clean_apply_failure()
        ) as apply:
            self.widget.prompt.setPlainText("Create a dependency")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            apply.assert_called_once()
        self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), 3)
        self.assertNotIn("PREMATURE_STOP", self.widget.transcript.toPlainText())
        self.assertIn("cannot create this dependency", self.widget.transcript.toPlainText())
        self.assertTrue(self.widget._recovery_used)
        self.widget.prompt.setPlainText("Explain a different approach")
        self.widget._send()
        self.assertFalse(self.widget._recovery_used)
        self.assertFalse(self.widget._recovery_pending)
        self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)

    def test_uncertain_rollback_never_gets_a_recovery_nudge(self):
        self.connect(scene_access=True)
        for override in ({"rollback": "incomplete"}, {"manual_undo_needed": True}, {"manual_undo_needed": None}):
            with self.subTest(override=override):
                self.widget._new_chat()
                before = len(StudioHandler.requests)
                StudioHandler.chat_responses.extend([
                    (200, self.tool_stream(self.tool_call("unsafe_recovery", "houlm_propose_edits"))),
                    (200, self.final_reply("Inspect the remaining scene changes manually.")),
                ])
                failure = dict(self.clean_apply_failure(), **override)
                with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Change the scene")), mock.patch.object(panel.edits, "apply", return_value=failure):
                    self.widget.prompt.setPlainText("Change the scene")
                    self.widget._send()
                    self.wait_for(lambda: self.widget._pending_review is not None)
                    self.widget.review_apply_button.click()
                    self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
                self.assertEqual(len(StudioHandler.requests) - before, 2)
                self.assertFalse(self.widget._recovery_used)
                self.assertIn("remaining scene changes manually", self.widget.transcript.toPlainText())

    def test_new_proposal_validation_failure_clears_pending_recovery(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("first_attempt", "houlm_propose_edits"))),
            (200, self.tool_stream(self.tool_call("new_attempt", "houlm_propose_edits"))),
            (200, self.final_reply("The correction needs a supported node type.")),
        ])
        metadata = {"index": 1, "op": "create_node", "path": "/obj/box1"}
        evidence = {"parameters": [{"name": "sizex", "type": "float"}], "truncated": False}
        validation = panel.edits.EditError("Inspect the installed type.", "type_missing", failed_operation=metadata, available_schema=evidence)
        with mock.patch.object(panel.edits, "prepare", side_effect=[types.SimpleNamespace(summary="Change the box"), validation]), mock.patch.object(
            panel.edits, "apply", return_value=self.clean_apply_failure()
        ), mock.patch.object(panel.toolchat, "build_payload", wraps=panel.toolchat.build_payload) as build:
            self.widget.prompt.setPlainText("Change the box")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            self.assertFalse(any(call.kwargs.get("recovery_reminder") for call in build.call_args_list))
        result = json.loads(StudioHandler.requests[-1][3]["messages"][-1]["content"])
        self.assertEqual(result["failed_operation"], metadata)
        self.assertEqual(result["available_schema"], evidence)
        self.assertEqual(result["error"]["code"], "type_missing")
        self.assertIn("type_missing", self.widget._last_edit_details)
        self.assertIn("Captured parameter schema", self.widget._last_edit_details)
        self.assertIn("sizex (float)", self.widget._last_edit_details)
        self.assertFalse(self.widget._recovery_pending)

    def test_new_chat_cancels_queued_recovery_continuation(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("cancel_recovery", "houlm_propose_edits"))),
            (200, self.final_reply("PREMATURE_STOP")),
        ])
        callbacks = []
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Change the box")), mock.patch.object(
            panel.edits, "apply", return_value=self.clean_apply_failure()
        ):
            self.widget.prompt.setPlainText("Change the box")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            with mock.patch.object(panel.QtCore.QTimer, "singleShot", side_effect=lambda delay, callback: callbacks.append(callback)):
                self.widget.review_apply_button.click()
                callbacks.pop(0)()  # Run the approved action after the UI yield.
                callbacks.pop(0)()  # Send the next model request.
                self.wait_for(lambda: self.widget._reply is None and self.widget._recovery_used)
            self.assertEqual(len(callbacks), 1)
            self.widget._new_chat()
            callbacks[0]()
            self.app.processEvents()
        self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), 2)
        self.assertIsNone(self.widget._agent_history)
        self.assertFalse(self.widget._recovery_pending)
        self.assertFalse(self.widget._recovery_used)

    def test_recovery_respects_remaining_round_and_tool_budgets(self):
        self.connect(scene_access=True)
        for exhausted in ("rounds", "tools"):
            with self.subTest(exhausted=exhausted):
                self.widget._new_chat()
                before = len(StudioHandler.requests)
                StudioHandler.chat_responses.extend([
                    (200, self.tool_stream(self.tool_call("limited_recovery", "houlm_propose_edits"))),
                    (200, self.final_reply("The proposal failed and this turn has reached its limit.")),
                ])
                with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Change the box")), mock.patch.object(panel.edits, "apply", return_value=self.clean_apply_failure()):
                    self.widget.prompt.setPlainText("Change the box")
                    self.widget._send()
                    self.wait_for(lambda: self.widget._pending_review is not None)
                    self.widget.review_apply_button.click()
                    if exhausted == "rounds":
                        self.widget._agent_rounds = panel.MAX_AGENT_ROUNDS - 1
                    else:
                        self.widget._agent_tool_count = panel.MAX_AGENT_TOOL_CALLS
                    self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
                self.assertEqual(len(StudioHandler.requests) - before, 2)
                self.assertFalse(self.widget._recovery_used)

    def test_edit_failure_shows_exact_operation_safe_reason_and_local_rollback(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("diagnostic_edit", "houlm_propose_edits"))),
            (200, {"choices": [{"message": {"content": "The parameter name needs correction."}, "finish_reason": "stop"}]}),
        ])
        failure = {
            "status": "failed", "success": False, "completed_operations": 2,
            "failed_operation": {"index": 3, "op": "set_parameter", "path": "/obj/fluid/flipsolver1", "parameter": "viscosity"},
            "error": {"code": "parameter_missing", "message": "The parameter does not exist on this node."},
            "rollback": "reverted_scene_fields", "manual_undo_needed": False,
            "changed_paths": ["/obj/fluid/flipsolver1"],
            "request_log": "PRIVATE_REQUEST_LOG", "exception": "PRIVATE_EXCEPTION",
        }
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Change viscosity")), mock.patch.object(panel.edits, "apply", return_value=failure):
            self.widget.prompt.setPlainText("Enable viscosity")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
        transcript = self.widget.transcript.toPlainText()
        for expected in ("operation 3", "/obj/fluid/flipsolver1", "viscosity", "parameter_missing", "does not exist", "local executor reversed"):
            self.assertIn(expected, transcript)
            self.assertIn(expected, self.widget._last_edit_details)
        self.assertNotIn("PRIVATE_", transcript)
        self.assertNotIn("PRIVATE_", self.widget._last_edit_details)
        self.assertEqual(self.widget._last_error_details, "")
        self.assertFalse(self.widget.edit_details_button.isHidden())
        self.widget._show_edit_details()
        self.assertEqual(self.widget._edit_details_dialog.windowTitle(), "Scene edit details")
        detail = self.widget._edit_details_dialog.findChild(QtWidgets.QPlainTextEdit)
        self.assertEqual(detail.toPlainText(), self.widget._last_edit_details)
        copy = next(button for button in self.widget._edit_details_dialog.findChildren(QtWidgets.QPushButton) if button.text() == "Copy details")
        copy.click()
        self.assertEqual(self.app.clipboard().text(), self.widget._last_edit_details)

        self.widget.prompt.setPlainText("Inspect before changing anything else")
        self.widget._send()
        self.assertEqual(self.widget._last_edit_details, "")
        self.assertIsNone(self.widget._edit_details_dialog)
        self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)

    def test_edit_details_and_later_http_failure_remain_separate(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("partial_edit", "houlm_propose_edits"))),
            (400, {"error": {"message": "Required property 'annotations' is missing."}}),
        ])
        failure = {"status": "failed", "success": False, "completed_operations": 0,
                   "failed_operation": {"index": 1, "op": "create_node", "path": "/obj/fluid"},
                   "error": {"code": "node_creation_failed", "message": "The requested node could not be created."},
                   "rollback": "incomplete", "manual_undo_needed": True, "changed_paths": ["/obj/fluid"]}
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Create fluid network")), mock.patch.object(panel.edits, "apply", return_value=failure):
            self.widget.prompt.setPlainText("Build a fluid network")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
        self.assertIn("node_creation_failed", self.widget._last_edit_details)
        self.assertIn("could not reverse every change", self.widget._last_edit_details)
        self.assertIn("annotations", self.widget._last_error_details)
        self.assertNotIn("annotations", self.widget._last_edit_details)
        self.assertFalse(self.widget.edit_details_button.isHidden())
        self.assertFalse(self.widget.error_details_button.isHidden())
        self.assertEqual(self.widget._messages[-1]["role"], "tool")
        self.widget._new_chat()
        self.assertEqual(self.widget._last_edit_details, "")
        self.assertTrue(self.widget.edit_details_button.isHidden())

    def test_edit_diagnostics_are_bounded_redacted_and_handle_missing_fields(self):
        self.widget.api_key.setText("test-only-session-key")
        summary, details = self.widget._edit_failure_description({
            "status": "failed", "failed_operation": {"op": []},
            "error": {"message": "Unsafe\\u0000" + "test-only-session-key" + "\x00\u202e" + "x" * 50000},
            "changed_paths": ["/obj/" + "y" * 2000] * 100,
            "log": "PRIVATE_LOG_MUST_NOT_APPEAR",
        })
        self.assertIn("operation unknown", summary)
        self.assertIn("Rollback status is unavailable", details)
        self.assertIn("[redacted]", details)
        self.assertNotIn("test-only-session-key", details)
        self.assertNotIn("\x00", details)
        self.assertNotIn("\u202e", details)
        self.assertNotIn("PRIVATE_LOG", details)
        self.assertLessEqual(len(details), 12030)
        self.assertIn("[Details truncated]", details)

    def test_read_then_edit_uses_same_conversation_without_mode_changes(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("selection_before_edit", "houlm_get_selection"))),
            (200, {"choices": [{"message": {"content": "The selected wrangle offsets its points."}, "finish_reason": "stop"}]}),
            (200, self.final_reply("The selected wrangle offsets its points.")),
        ])
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results):
            self.widget.prompt.setPlainText("Explain my selected wrangle")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
        read_history = list(self.widget._messages)
        self.assertEqual([message["role"] for message in read_history], ["user", "assistant", "tool", "assistant", "assistant"])
        StudioHandler.chat_responses.append((200, self.tool_stream(self.tool_call("edit_after_read", "houlm_propose_edits"))))
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Increase the offset")), mock.patch.object(
            panel.edits, "apply", return_value={"status": "applied", "success": True, "completed_operations": 1}
        ) as apply:
            self.widget.prompt.setPlainText("Now increase that offset")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.assertEqual(self.widget._messages, read_history)
            apply.assert_not_called()
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            apply.assert_called_once()
        self.assertEqual(self.widget._messages[:len(read_history)], read_history)
        self.assertEqual(self.widget._messages[len(read_history)]["content"], "Now increase that offset")
        posts = [request[3] for request in StudioHandler.requests if request[0] == "POST"]
        self.assertTrue(all("houlm_propose_edits" in [tool["function"]["name"] for tool in post["tools"]] for post in posts))

    def test_successful_login_is_saved_but_disconnect_preserves_it(self):
        self.connect()
        self.save_key.assert_called_once_with(self.base, "test-only-session-key")
        self.widget.disconnect()
        self.delete_key.assert_not_called()
        self.assertEqual(self.widget.api_key.text(), "")

    def test_saved_login_restores_and_connects_after_construction(self):
        self.widget.deleteLater()
        self.app.processEvents()
        panel.core.load_preferences.return_value = {"server_url": self.base, "model_id": "permitted-cloud"}
        self.load_key.return_value = "test-restored-key"
        self.widget = panel.AssistantPanel()
        self.assertFalse(self.widget._connected)
        self.assertEqual(self.widget.api_key.text(), "test-restored-key")
        self.wait_for(lambda: self.widget._connected)
        self.assertEqual(StudioHandler.requests[0][:3], ("GET", "/api/models", "Bearer test-restored-key"))
        self.assertEqual(self.widget.models.currentData(), "permitted-cloud")
        self.assertTrue(self.widget.scene_tools.isChecked())

    def test_closing_before_deferred_login_cancels_connection(self):
        self.widget.deleteLater()
        self.app.processEvents()
        panel.core.load_preferences.return_value = {"server_url": self.base}
        self.load_key.return_value = "test-restored-key"
        self.widget = panel.AssistantPanel()
        self.widget.close()
        self.app.processEvents()
        self.assertEqual(StudioHandler.requests, [])
        self.delete_key.assert_not_called()

    def test_credential_save_failure_keeps_connected_session(self):
        self.save_key.side_effect = OSError("PRIVATE_STORAGE_ERROR")
        self.connect(scene_access=True)
        self.assertTrue(self.widget._connected)
        self.assertIn("could not save", self.widget.status.text())
        self.assertNotIn("PRIVATE_STORAGE_ERROR", self.widget.status.text())

    def test_sign_out_deletes_only_current_server_key(self):
        self.connect()
        self.widget._sign_out()
        self.delete_key.assert_called_once_with(self.base)
        self.assertFalse(self.widget._connected)
        self.assertEqual(self.widget.api_key.text(), "")

    def test_server_edit_restores_only_matching_server_key(self):
        self.connect()
        target = self.base + "/another-server"
        self.load_key.side_effect = lambda server: "other-server-key" if server == target else ""
        request_count = len(StudioHandler.requests)
        self.widget.server_url.setText(target)
        self.widget.server_url.textEdited.emit(target)
        self.assertEqual(self.widget.api_key.text(), "other-server-key")
        self.assertFalse(self.widget._connected)
        self.assertEqual(len(StudioHandler.requests), request_count)
        self.widget._connect()
        self.wait_for(lambda: self.widget._reply is None)
        self.assertEqual(StudioHandler.requests[-1][:3], ("GET", "/another-server/api/models", "Bearer other-server-key"))

    def test_worker_thread_cannot_construct_qt_widget(self):
        outcome = []

        def from_shell_like_worker():
            try:
                panel.AssistantPanel()
            except RuntimeError as error:
                outcome.append(str(error))

        worker = threading.Thread(target=from_shell_like_worker, daemon=True)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive(), "Widget creation blocked the worker thread")
        self.assertEqual(len(outcome), 1)
        self.assertIn("thread", outcome[0].lower())

    def test_server_models_and_text_snapshot_complete_via_qt_network(self):
        self.connect()
        self.assertEqual(self.widget.models.currentData(), "permitted-local")
        self.assertEqual(self.widget.models.count(), 2)
        self.widget.include_selection.setChecked(True)
        self.widget.prompt.setPlainText("Explain this wrangle")
        with mock.patch.object(panel.scene, "snapshot_selection", return_value={"nodes": [{"path": "/obj/geo/wrangle", "vex": "@P.y += 1;"}]}) as snapshot:
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None)
            snapshot.assert_called_once_with(panel.hou)
        request = StudioHandler.requests[-1]
        self.assertEqual(request[:3], ("POST", "/api/chat/completions", "Bearer test-only-session-key"))
        self.assertEqual(request[3]["model"], "permitted-local")
        self.assertTrue(request[3]["stream"])
        self.assertIn("@P.y += 1;", request[3]["messages"][-1]["content"])
        self.assertTrue(all(isinstance(message["content"], str) for message in request[3]["messages"]))
        self.assertEqual(self.widget._messages[-1]["content"], "OK")
        self.assertIn("Assistant:\nOK", self.widget.transcript.toPlainText())
        self.assertEqual(self.save_preferences.call_args.args, (self.base, "permitted-local"))

    def test_requested_streaming_response_is_accepted(self):
        self.connect("/sse")
        self.widget.prompt.setPlainText("Hello")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None)
        self.assertEqual(self.widget._messages[-1]["content"], "OK")
        self.assertTrue(StudioHandler.requests[-1][3]["stream"])
        self.assertNotIn("Internal reasoning", self.widget.transcript.toPlainText())

    def test_http_200_malformed_sse_exposes_safe_protocol_details_not_response(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.append((200, b'data: {"PRIVATE_SCENE_BODY":"test-only-session-key",\n\n'))
        with mock.patch.object(panel.toolchat, "execute_calls") as reads, mock.patch.object(panel.edits, "prepare") as prepare, mock.patch.object(panel.edits, "apply") as apply:
            self.widget.prompt.setPlainText("Inspect the current scene")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            reads.assert_not_called()
            prepare.assert_not_called()
            apply.assert_not_called()
        self.assertTrue(self.widget._last_error_details)
        self.assertFalse(self.widget.error_details_button.isHidden())
        self.assertEqual(self.widget._last_edit_details, "")
        self.assertEqual(self.widget._messages, [])
        self.assertEqual(self.widget.prompt.toPlainText(), "Inspect the current scene")
        self.assertIn("200", self.widget._last_error_details)
        self.assertIn("malformed_sse", self.widget._last_error_details)
        self.assertNotIn("PRIVATE_SCENE_BODY", self.widget._last_error_details)
        self.assertNotIn("test-only-session-key", self.widget._last_error_details)
        self.assertNotIn("unexpected response", self.widget.status.text())
        self.widget._show_error_details()
        self.assertEqual(self.widget._details_dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText(), self.widget._last_error_details)

    def test_http_200_tool_argument_deltas_with_null_metadata_are_accepted(self):
        self.connect(scene_access=True)
        events = [
            {"choices": [{"delta": {"tool_calls": [{"index": 17, "id": "sparse_selection", "type": "function",
                "function": {"name": "houlm_get_selection", "arguments": "{"}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 17, "id": None, "type": None,
                "function": {"name": None, "arguments": "}"}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 17, "function": {"name": None, "arguments": None}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": None}, "finish_reason": "tool_calls"}]},
        ]
        StudioHandler.chat_responses.append((200, self.sse_events(events)))
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=self.fake_tool_results) as reads, mock.patch.object(panel.edits, "apply") as apply:
            self.widget.prompt.setPlainText("What is selected?")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            reads.assert_called_once_with(panel.hou, [self.tool_call("sparse_selection", "houlm_get_selection")])
            apply.assert_not_called()
        self.assertEqual(self.widget._last_error_details, "")
        self.assertEqual(self.widget._messages[-1]["content"], "OK")
        self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), 3)

    def test_invalid_arguments_retry_once_from_valid_history_and_correction_needs_review(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.invalid_argument_stream("discarded_call")),
            (200, self.tool_stream(self.tool_call("corrected_call", "houlm_propose_edits"))),
            (200, self.final_reply("The reviewed correction is applied.")),
        ])
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Create the box")) as prepare, mock.patch.object(
            panel.edits, "apply", return_value={"status": "applied", "success": True, "completed_operations": 1}
        ) as apply, mock.patch.object(panel.toolchat, "execute_calls") as reads, mock.patch.object(
            panel.toolchat, "build_payload", wraps=panel.toolchat.build_payload
        ) as build:
            self.widget.prompt.setPlainText("Create the box")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            prepare.assert_called_once()
            apply.assert_not_called()
            reads.assert_not_called()
            self.assertEqual(self.widget._agent_history, [{"role": "user", "content": "Create the box"}])
            self.assertEqual(self.widget._agent_tool_count, 0)
            self.assertTrue(self.widget._argument_retry_used)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            apply.assert_called_once()
            self.assertEqual(sum(call.kwargs.get("tool_argument_retry") is not None for call in build.call_args_list), 1)
            self.assertNotIn("tool_argument_retry", build.call_args_list[-1].kwargs)
        posts = [request[3] for request in StudioHandler.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertEqual(posts[1]["messages"][1:], [{"role": "user", "content": "Create the box"}])
        self.assertNotIn("discarded_call", json.dumps(self.widget._messages))
        self.assertNotIn("PRIVATE_ARGUMENT_SENTINEL", self.widget.transcript.toPlainText())
        self.assertEqual(self.widget.transcript.toPlainText().count("Retrying this model step once"), 1)
        self.assertEqual(self.widget._last_error_details, "")

    def test_invalid_argument_retry_is_bounded_and_keeps_both_safe_failures(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.invalid_argument_stream("first_bad_call")),
            (200, self.invalid_argument_stream("second_bad_call")),
        ])
        with mock.patch.object(panel.toolchat, "execute_calls") as reads, mock.patch.object(panel.edits, "prepare") as prepare, mock.patch.object(panel.edits, "apply") as apply:
            self.widget.prompt.setPlainText("Create the box")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            reads.assert_not_called()
            prepare.assert_not_called()
            apply.assert_not_called()
        self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), 2)
        self.assertEqual(self.widget._messages, [])
        self.assertEqual(self.widget.prompt.toPlainText(), "Create the box")
        self.assertIn("First rejected response", self.widget._last_error_details)
        self.assertIn("Failure after retry", self.widget._last_error_details)
        self.assertGreaterEqual(self.widget._last_error_details.count("tool_arguments_invalid_json"), 2)
        self.assertNotIn("PRIVATE_ARGUMENT_SENTINEL", self.widget._last_error_details)
        self.assertNotIn("test-only-session-key", self.widget._last_error_details)
        self.assertFalse(self.widget.error_details_button.isHidden())

    def test_invalid_argument_retry_followed_by_http_failure_preserves_diagnostics(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.invalid_argument_stream()),
            (400, {"error": {"message": "Required property 'annotations' is missing. PRIVATE_SERVER_BODY"}}),
        ])
        self.widget.prompt.setPlainText("Inspect the scene")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
        self.assertIn("tool_arguments_invalid_json", self.widget._last_error_details)
        self.assertIn("annotations", self.widget._last_error_details)
        self.assertIn("Failure after retry", self.widget._last_error_details)
        self.assertNotIn("PRIVATE_SERVER_BODY", self.widget._last_error_details)

    def test_legacy_and_refined_argument_codes_each_retry_once_on_new_user_turn(self):
        self.connect(scene_access=True)
        codes = ("invalid_tool_arguments", "tool_arguments_missing", "tool_arguments_invalid_json", "tool_arguments_not_object")
        for code in codes:
            with self.subTest(code=code), mock.patch.object(panel.toolchat, "parse_reply", side_effect=[
                panel.core.ProtocolError(code), {"content": "Corrected response", "tool_calls": []},
            ]), mock.patch.object(panel.toolchat, "build_payload", wraps=panel.toolchat.build_payload) as build, mock.patch.object(
                panel.toolchat, "execute_calls"
            ) as reads, mock.patch.object(panel.edits, "prepare") as prepare:
                self.widget.prompt.setPlainText("Continue the task")
                self.widget._send()
                self.assertFalse(self.widget._argument_retry_used)
                self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
                self.assertEqual([call.kwargs["tool_argument_retry"] for call in build.call_args_list if "tool_argument_retry" in call.kwargs], [code])
                reads.assert_not_called()
                prepare.assert_not_called()
                self.assertTrue(self.widget._argument_retry_used)
                self.assertEqual(self.widget._last_error_details, "")
        self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), len(codes) * 2)
        self.assertEqual([message["role"] for message in self.widget._messages], ["user", "assistant"] * len(codes))

    def test_new_chat_cancels_queued_invalid_argument_retry(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.append((200, self.invalid_argument_stream()))
        callbacks = []
        with mock.patch.object(panel.QtCore.QTimer, "singleShot", side_effect=lambda delay, callback: callbacks.append(callback)), mock.patch.object(panel.edits, "prepare") as prepare:
            self.widget.prompt.setPlainText("Build a box")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and bool(callbacks))
            self.assertTrue(self.widget._argument_retry_used)
            self.assertIsNotNone(self.widget._agent_history)
            self.widget._new_chat()
            callbacks[0]()
            self.app.processEvents()
            prepare.assert_not_called()
        self.assertEqual(len([request for request in StudioHandler.requests if request[0] == "POST"]), 1)
        self.assertFalse(self.widget._argument_retry_used)
        self.assertEqual(self.widget._response_retry_details, "")
        self.assertEqual(self.widget._last_error_details, "")
        self.assertIsNone(self.widget._agent_history)

    def test_invalid_argument_retry_respects_round_and_tool_limits(self):
        self.connect(scene_access=True)
        for limit in ("rounds", "tools"):
            with self.subTest(limit=limit):
                self.widget._new_chat()
                before = len(StudioHandler.requests)
                StudioHandler.chat_responses.append((200, self.invalid_argument_stream()))
                self.widget.prompt.setPlainText("Build a box")
                self.widget._send()
                if limit == "rounds":
                    self.widget._agent_rounds = panel.MAX_AGENT_ROUNDS
                else:
                    self.widget._agent_tool_count = panel.MAX_AGENT_TOOL_CALLS
                self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
                self.assertEqual(len(StudioHandler.requests) - before, 1)
                self.assertNotIn("First rejected response", self.widget._last_error_details)

    def test_invalid_argument_retry_never_replays_previously_applied_edits(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("already_applied", "houlm_propose_edits"))),
            (200, self.invalid_argument_stream("never_execute")),
            (200, self.invalid_argument_stream("still_invalid")),
        ])
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Create the box")) as prepare, mock.patch.object(
            panel.edits, "apply", return_value={"status": "applied", "success": True, "completed_operations": 1, "changed_paths": ["/obj/box1"]}
        ) as apply:
            self.widget.prompt.setPlainText("Create the box")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            prepare.assert_called_once()
            apply.assert_called_once()
        self.assertEqual([message["role"] for message in self.widget._messages], ["user", "assistant", "tool"])
        self.assertEqual(json.loads(self.widget._messages[-1]["content"])["status"], "applied")
        history = StudioHandler.requests[-1][3]["messages"][1:]
        self.assertEqual(history, self.widget._messages)
        self.assertNotIn("never_execute", json.dumps(history))
        self.assertNotIn("still_invalid", json.dumps(history))
        self.assertIn("remain in Houdini", self.widget.status.text())

    def test_http_200_too_many_tool_calls_reports_limit_without_scene_actions(self):
        self.connect(scene_access=True)
        calls = [dict(self.tool_call("overflow_%d" % index, "houlm_propose_edits", {"secret": "PRIVATE_SCENE_BODY test-only-session-key"}), index=index * 3)
                 for index in range(panel.toolchat.MAX_TOOL_CALLS + 1)]
        events = [{"choices": [{"delta": {"tool_calls": calls}, "finish_reason": "tool_calls"}]}]
        StudioHandler.chat_responses.append((200, self.sse_events(events)))
        with mock.patch.object(panel.toolchat, "execute_calls") as reads, mock.patch.object(panel.edits, "prepare") as prepare, mock.patch.object(panel.edits, "apply") as apply:
            self.widget.prompt.setPlainText("Build the requested network")
            self.widget._send()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            reads.assert_not_called()
            prepare.assert_not_called()
            apply.assert_not_called()
        self.assertTrue(self.widget._last_error_details)
        self.assertFalse(self.widget.error_details_button.isHidden())
        self.assertIn("200", self.widget._last_error_details)
        self.assertIn("too_many_tool_calls", self.widget._last_error_details)
        self.assertIn("tool", self.widget.status.text().lower())
        self.assertIn(str(panel.toolchat.MAX_TOOL_CALLS), self.widget._last_error_details)
        self.assertNotIn("PRIVATE_SCENE_BODY", self.widget._last_error_details)
        self.assertNotIn("test-only-session-key", self.widget._last_error_details)
        self.assertEqual(self.widget._messages, [])
        self.assertIsNone(self.widget._pending_review)

    def test_protocol_failure_after_apply_keeps_applied_tool_history(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.extend([
            (200, self.tool_stream(self.tool_call("applied_before_parse_error", "houlm_propose_edits"))),
            (200, b'data: {"PRIVATE_SCENE_BODY":\n\n'),
        ])
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Create the box")), mock.patch.object(
            panel.edits, "apply", return_value={"status": "applied", "success": True, "completed_operations": 1, "changed_paths": ["/obj/box1"]}
        ) as apply:
            self.widget.prompt.setPlainText("Create the box")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
            apply.assert_called_once()
        self.assertEqual([message["role"] for message in self.widget._messages], ["user", "assistant", "tool"])
        self.assertEqual(json.loads(self.widget._messages[-1]["content"])["status"], "applied")
        self.assertIn("remain in Houdini", self.widget.status.text())
        self.assertTrue(self.widget._last_error_details)
        self.assertFalse(self.widget.error_details_button.isHidden())

    def test_unknown_response_parser_exception_is_not_echoed(self):
        self.connect(scene_access=True)
        for error_type in (ValueError, RecursionError):
            with self.subTest(error_type=error_type), mock.patch.object(panel.toolchat, "parse_reply", side_effect=error_type(
                "PRIVATE_EXCEPTION Authorization: Bearer test-only-session-key /obj/private_scene"
            )), mock.patch.object(panel.toolchat, "execute_calls") as reads:
                self.widget.prompt.setPlainText("Inspect the scene")
                self.widget._send()
                self.wait_for(lambda: self.widget._reply is None and self.widget._agent_history is None)
                reads.assert_not_called()
            self.assertTrue(self.widget._last_error_details)
            self.assertFalse(self.widget.error_details_button.isHidden())
            visible = self.widget.status.text() + self.widget._last_error_details + self.widget.transcript.toPlainText()
            for private in ("PRIVATE_EXCEPTION", "test-only-session-key", "/obj/private_scene"):
                self.assertNotIn(private, visible)

    def test_partial_stream_does_not_commit_a_successful_turn(self):
        self.connect("/broken-stream")
        self.widget.prompt.setPlainText("Hello")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None)
        self.assertEqual(self.widget._messages, [])
        self.assertEqual(self.widget.prompt.toPlainText(), "Hello")
        self.assertIn("[Request failed]", self.widget.transcript.toPlainText())
        self.assertNotIn("partial", self.widget.transcript.toPlainText())

    def test_connection_fields_live_only_in_popup_and_hide_after_connect(self):
        self.widget.show()
        self.app.processEvents()
        self.assertFalse(self.widget.connection_dialog.isVisible())
        self.assertFalse(self.widget.api_key.isVisible())
        self.assertFalse(self.widget.server_url.isVisible())
        self.widget._show_connection_dialog()
        self.app.processEvents()
        self.assertTrue(self.widget.connection_dialog.isVisible())
        self.assertTrue(self.widget.api_key.isVisible())
        self.assertEqual(self.widget.api_key.echoMode(), QtWidgets.QLineEdit.Password)
        self.connect()
        self.assertFalse(self.widget.connection_dialog.isVisible())
        self.assertFalse(self.widget.api_key.isVisible())

    def test_transcript_escapes_model_markup_and_loads_no_resources(self):
        content = 'Assistant:\n<img src="https://external.example/private.png"><script>bad()</script>\n'
        self.widget.transcript.appendPlainText(content)
        rendered = self.widget.transcript.toHtml()
        self.assertIn("&lt;img", rendered)
        self.assertNotIn('<img src="https://external.example', rendered)
        self.assertEqual(self.widget.transcript.toPlainText(), content)
        self.assertFalse(self.widget.transcript.openExternalLinks())
        self.assertFalse(self.widget.transcript.openLinks())
        self.assertEqual(bytes(self.widget.transcript.loadResource(2, QtCore.QUrl("https://external.example/private.png"))), b"")

    def test_busy_banner_is_visible_and_animated_until_response_finishes(self):
        self.connect(scene_access=True)
        self.widget.prompt.setPlainText("Explain the scene")
        self.widget._send()
        banner = self.widget.activity_banner
        self.assertFalse(banner.isHidden())
        self.assertTrue(banner._timer.isActive())
        self.assertTrue(banner._clock.isValid())
        self.assertIn("Waiting for server", banner.title.text())
        self.assertEqual(banner.progress.maximum(), 0)
        self.wait_for(lambda: self.widget._agent_history is None and self.widget._reply is None)
        self.assertTrue(banner.isHidden())
        self.assertFalse(banner._timer.isActive())

    def test_public_plan_and_target_are_visible_before_deferred_scene_read(self):
        self.connect(scene_access=True)
        self.widget._agent_history = [{"role": "user", "content": "Build something"}]
        self.widget._agent_model_id = "permitted-cloud"
        call = self.tool_call("network_check", "houlm_list_children", {"path": "/obj"})
        plan = "I’ll check which objects exist before preparing the new network."
        def execute(unused_hou, calls):
            self.assertIn("/obj", self.widget.activity_banner.title.text())
            return [{"role": "tool", "tool_call_id": calls[0]["id"],
                     "content": json.dumps({"ok": True, "result": {"child_count": 0, "children": []}})}]
        with mock.patch.object(panel.toolchat, "execute_calls", side_effect=execute) as reads:
            self.widget._agent_reply({"content": plan, "tool_calls": [call]})
            reads.assert_not_called()
            self.assertIn(plan, self.widget.transcript.toPlainText())
            self.assertIn("/obj", self.widget.activity_banner.title.text())
            self.wait_for(lambda: self.widget._agent_history is None and self.widget._reply is None)
            reads.assert_called_once()
        text = self.widget.transcript.toPlainText()
        self.assertEqual(text.count(plan), 1)
        self.assertIn("/obj is empty", text)
        self.assertNotIn("Inspect node parameters and connections", text)

    def test_stop_cancels_queued_read_but_keeps_login(self):
        self.connect(scene_access=True)
        self.widget._agent_history = [{"role": "user", "content": "Inspect"}]
        call = self.tool_call("queued", "houlm_inspect_node", {"path": "/obj/test"})
        with mock.patch.object(panel.toolchat, "execute_calls") as reads:
            self.widget._agent_reply({"content": "I’ll inspect the requested node.", "tool_calls": [call]})
            self.widget.activity_banner.stop_button.click()
            self.app.processEvents()
            reads.assert_not_called()
        self.assertIsNone(self.widget._agent_history)
        self.assertTrue(self.widget._connected)
        self.assertEqual(self.widget.api_key.text(), "test-only-session-key")
        self.assertTrue(self.widget.activity_banner.isHidden())

    def test_review_state_is_not_thinking_and_stop_can_cancel_queued_apply(self):
        self.connect(scene_access=True)
        StudioHandler.chat_responses.append((200, self.tool_stream(self.tool_call("review_state", "houlm_propose_edits"))))
        with mock.patch.object(panel.edits, "prepare", return_value=types.SimpleNamespace(summary="Create a test node")), mock.patch.object(panel.edits, "apply") as apply:
            self.widget.prompt.setPlainText("Create a test node")
            self.widget._send()
            self.wait_for(lambda: self.widget._pending_review is not None)
            self.assertEqual(self.widget.activity_banner.title.text(), "Waiting for your review")
            self.assertTrue(self.widget.activity_banner.progress.isHidden())
            self.widget.review_apply_button.click()
            self.assertIn("Applying approved changes", self.widget.activity_banner.title.text())
            apply.assert_not_called()
            self.widget.activity_banner.stop_button.click()
            self.app.processEvents()
            apply.assert_not_called()

    def test_assistant_markdown_is_formatted_without_changing_raw_history(self):
        content = 'Assistant:\n**Created** `OUT`.\n\n- Connected input\n- Added ramp\n\n```c\n@P.y = chramp("falloff", u);\n```\n'
        self.widget.transcript.appendPlainText(content)
        visible = QtWidgets.QTextBrowser.toPlainText(self.widget.transcript)
        self.assertIn("@P.y = chramp", visible)
        self.assertNotIn("```", visible)
        self.assertNotIn("**Created**", visible)
        self.assertEqual(self.widget.transcript.toPlainText(), content)

    def test_panel_fits_a_narrow_dock_with_activity(self):
        self.widget.transcript.appendPlainText("Assistant:\nChecking the selected node.\n")
        self.widget.activity_banner.show_stage("Thinking", "Checking an existing node before preparing the next change.")
        self.widget.resize(360, 720)
        self.widget.show()
        self.app.processEvents()
        self.assertLessEqual(self.widget.width(), 360)
        for control in (self.widget.activity_banner, self.widget.prompt, self.widget.models, self.widget.send_button):
            right = control.mapTo(self.widget, control.rect().topRight()).x()
            self.assertLess(right, self.widget.width())
        self.assertLess(self.widget.connection_indicator.geometry().right(), self.widget.new_chat_button.geometry().left())
        self.assertLess(self.widget.new_chat_button.geometry().right(), self.widget.connections_button.geometry().left())

    def test_suggestion_drafts_prompt_without_scene_read_or_request(self):
        with mock.patch.object(panel.scene, "snapshot_selection") as snapshot:
            self.widget.suggestion_buttons[0].click()
            self.assertIn("selected nodes", self.widget.prompt.toPlainText())
            self.assertTrue(self.widget.include_selection.isChecked())
            snapshot.assert_not_called()
        self.assertEqual(StudioHandler.requests, [])

    def test_second_turn_preserves_standard_chat_history_without_scene_access(self):
        self.connect()
        with mock.patch.object(panel.scene, "snapshot_selection") as snapshot:
            for prompt in ("Reply exactly OK", "What did I ask before?"):
                self.widget.prompt.setPlainText(prompt)
                self.widget._send()
                self.wait_for(lambda: self.widget._reply is None)
            snapshot.assert_not_called()
        payload = StudioHandler.requests[-1][3]
        self.assertEqual([message["role"] for message in payload["messages"]], ["system", "user", "assistant", "user"])
        self.assertEqual(payload["messages"][1:], [
            {"role": "user", "content": "Reply exactly OK"},
            {"role": "assistant", "content": "OK"},
            {"role": "user", "content": "What did I ask before?"},
        ])
        self.assertEqual(len(self.widget._messages), 4)

    def test_failed_second_turn_is_marked_and_retry_does_not_duplicate_history(self):
        self.connect()
        self.widget.prompt.setPlainText("Reply exactly OK")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None)
        successful_history = list(self.widget._messages)
        StudioHandler.chat_responses.append((400, {"error": {
            "message": "Required property 'annotations' is missing. Authorization: Bearer test-only-session-key",
            "param": "input[2].content[0]",
        }, "input": "PRIVATE_SCENE_DATA"}))
        self.widget.prompt.setPlainText("Follow up")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None)
        self.assertEqual(self.widget._messages, successful_history)
        self.assertEqual(self.widget.prompt.toPlainText(), "Follow up")
        self.assertIn("annotations", self.widget.status.text())
        transcript = self.widget.transcript.toPlainText()
        self.assertEqual(transcript.count("[Request failed]"), 1)
        self.assertNotIn("test-only-session-key", transcript)
        self.assertNotIn("PRIVATE_SCENE_DATA", transcript)
        self.assertIn("annotations", self.widget._last_error_details)
        self.assertNotIn("test-only-session-key", self.widget._last_error_details)
        self.assertNotIn("PRIVATE_SCENE_DATA", self.widget._last_error_details)
        self.assertFalse(self.widget.error_details_button.isHidden())
        self.widget._show_error_details()

        self.widget._send()
        self.assertEqual(self.widget._last_error_details, "")
        self.assertTrue(self.widget.error_details_button.isHidden())
        self.assertIsNone(self.widget._details_dialog)
        self.wait_for(lambda: self.widget._reply is None)
        request_history = StudioHandler.requests[-1][3]["messages"]
        self.assertEqual([message["role"] for message in request_history], ["system", "user", "assistant", "user"])
        self.assertEqual(sum(message["content"] == "Follow up" for message in request_history), 1)
        self.assertEqual(len(self.widget._messages), 4)

    def test_new_chat_clears_error_details(self):
        self.connect()
        StudioHandler.chat_responses.append((400, {"error": {"message": "Required property 'annotations' is missing."}}))
        self.widget.prompt.setPlainText("Hello")
        self.widget._send()
        self.wait_for(lambda: self.widget._reply is None)
        self.assertTrue(self.widget._last_error_details)
        self.widget._show_error_details()
        self.widget._new_chat()
        self.assertEqual(self.widget._last_error_details, "")
        self.assertTrue(self.widget.error_details_button.isHidden())
        self.assertIsNone(self.widget._details_dialog)

    def test_context_menu_changes_without_reading_scene(self):
        with mock.patch.object(panel.scene, "snapshot_selection") as snapshot:
            self.widget.scene_tools.setChecked(False)
            self.assertIn("not attached", self.widget.node_context_button.toolTip())
            self.widget.include_selection.setChecked(True)
            self.assertIn("selected node text will be attached", self.widget.node_context_button.toolTip())
            self.widget.include_selection.setChecked(False)
            self.assertIn("not attached", self.widget.node_context_button.toolTip())
            snapshot.assert_not_called()

    def test_redirect_is_not_followed_or_forwarded_authorization(self):
        self.widget.server_url.setText(self.base + "/redirect")
        self.widget.api_key.setText("test-only-session-key")
        self.widget._connect()
        self.wait_for(lambda: self.widget._reply is None)
        self.assertEqual(len(StudioHandler.requests), 1)
        self.assertEqual(StudioHandler.requests[0][1], "/redirect/api/models")
        self.assertIn("redirected", self.widget.status.text())
        self.assertFalse(self.widget._connected)

    def test_url_edit_aborts_pending_request_and_clears_session(self):
        self.connect()
        self.widget._messages = [{"role": "user", "content": "private old context"}]
        self.widget.prompt.setPlainText("Question")
        self.widget._send()
        self.assertIsNotNone(self.widget._reply)
        self.widget.server_url.setText(self.base + "/changed")
        self.widget.server_url.textEdited.emit(self.widget.server_url.text())
        self.assertIsNone(self.widget._reply)
        self.assertEqual(self.widget.api_key.text(), "")
        self.assertEqual(self.widget._messages, [])
        self.assertEqual(self.widget.models.count(), 0)
        self.assertFalse(self.widget._connected)
        self.assertFalse(self.widget.send_button.isEnabled())

    def test_key_edit_keeps_typed_key_and_invalidates_connection(self):
        self.connect()
        self.widget.api_key.setText("replacement-key")
        self.widget.api_key.textEdited.emit("replacement-key")
        self.assertEqual(self.widget.api_key.text(), "replacement-key")
        self.assertFalse(self.widget._connected)
        self.assertEqual(self.widget.models.count(), 0)

    def test_model_change_clears_prior_context(self):
        self.connect()
        self.widget._messages = [{"role": "user", "content": "prior selection"}]
        self.widget.transcript.setPlainText("Prior selection")
        self.widget.models.setCurrentIndex(1)
        self.assertEqual(self.widget._messages, [])
        self.assertEqual(self.widget.transcript.toPlainText(), "")

    def test_server_error_does_not_expose_body(self):
        self.widget.server_url.setText(self.base + "/denied")
        self.widget.api_key.setText("test-only-session-key")
        self.widget._connect()
        self.wait_for(lambda: self.widget._reply is None)
        self.assertIn("Access denied", self.widget.status.text())
        self.assertNotIn("SERVER_SECRET", self.widget.status.text())
        self.assertFalse(self.widget._connected)
        self.save_key.assert_not_called()

    def test_invalid_key_never_starts_request(self):
        self.widget.server_url.setText(self.base)
        self.widget.api_key.setText("invalid key")
        self.widget._connect()
        self.assertIsNone(self.widget._reply)
        self.assertEqual(StudioHandler.requests, [])


if __name__ == "__main__":
    unittest.main()
