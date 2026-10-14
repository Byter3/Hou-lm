"""Qt + JSON sidecar integration using temporary HIP paths and loopback only."""

import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import test_panel as qt_support
from test_conversations import EVENTS, FakeHipFile
from test_context_index import Hou, Node, CALL, result as inspection_result

if qt_support._qt_available:
    from PySide2 import QtCore, QtWidgets
    import shiboken2
    panel = qt_support.panel


@unittest.skipUnless(qt_support._qt_available, qt_support._qt_reason)
class PanelHistoryTests(unittest.TestCase):
    # Reuse only the synthetic loopback harness, not its 85 inherited tests.
    setUpClass = classmethod(qt_support.PanelTests.setUpClass.__func__)
    tearDownClass = classmethod(qt_support.PanelTests.tearDownClass.__func__)
    wait_for = qt_support.PanelTests.wait_for
    connect = qt_support.PanelTests.connect
    tool_call = staticmethod(qt_support.PanelTests.tool_call)

    def setUp(self):
        qt_support.StudioHandler.requests.clear()
        qt_support.StudioHandler.chat_responses.clear()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.first_hip = self.folder / "test_v001.hip"
        self.hou = Hou([Node("/obj"), Node("/stage"), Node("/obj/geo1")])
        self.hou.session = types.SimpleNamespace()
        self.hou.hipFileEventType = EVENTS
        self.hou.hipFile = FakeHipFile(self.first_hip, new=False)
        self.hou.hipFile.basename = lambda: Path(self.hou.hipFile.path()).name
        patches = [
            mock.patch.object(panel, "hou", self.hou),
            mock.patch.object(panel.core, "load_preferences", return_value={}),
            mock.patch.object(panel.core, "save_preferences"),
            mock.patch.object(panel.credentials, "load_key", return_value=""),
            mock.patch.object(panel.credentials, "save_key"),
            mock.patch.object(panel.credentials, "delete_key"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.widget = panel.AssistantPanel()

    def destroy_widget(self):
        if shiboken2.isValid(self.widget):
            self.widget.disconnect()
            self.widget.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.app.processEvents()

    def tearDown(self):
        self.destroy_widget()

    def seed_chat(self, prompt="Explain this scene", answer="Authored nodes inspected"):
        self.widget._messages = [{"role": "user", "content": prompt},
                                 {"role": "assistant", "content": answer}]
        self.widget.transcript.setPlainText("You:\n" + prompt + "\n\nAssistant:\n" + answer)
        self.widget._checkpoint()
        return self.widget._history.active["id"]

    def saved_active(self, hip=None):
        path = panel.conversations.history_store.sidecar_path(hip or self.hou.hipFile.path())
        document, _ = panel.conversations.history_store.load(path)
        return next(item for item in document["conversations"] if item["id"] == document["active_conversation_id"])

    def saved_other_scene(self, path, *, server=None, model="permitted-cloud", prompt="Other HIP question"):
        store = panel.conversations.history_store
        document = store.fresh_document()
        item = store.new_conversation(document, server=server or self.base, model=model)
        item.update(messages=[{"role": "user", "content": prompt}, {"role": "assistant", "content": "Other scene answer"}],
                    transcript=["You:\n" + prompt, "Assistant:\nOther scene answer"], scene_access=True)
        store.save(store.sidecar_path(path), document, None)
        return item

    def test_disconnect_and_widget_reload_restore_chat_draft_context_and_no_credentials(self):
        self.connect(scene_access=True)
        identifier = self.seed_chat()
        self.widget._context.observe_tool(CALL, inspection_result())
        self.widget.prompt.setPlainText("My next question")
        self.widget._checkpoint()
        expected_messages = copy.deepcopy(self.widget._messages)
        saved = self.saved_active()
        self.assertEqual(saved["id"], identifier)
        self.assertEqual(saved["draft"], "My next question")
        self.assertEqual(saved["scene_context"]["entries"][0]["path"], "/obj/geo1")
        self.assertNotIn("test-only-session-key", json.dumps(saved))
        self.destroy_widget()
        self.widget = panel.AssistantPanel()
        self.assertEqual(self.widget._messages, expected_messages)
        self.assertIn("Explain this scene", self.widget.transcript.toPlainText())
        self.assertEqual(self.widget.prompt.toPlainText(), "My next question")
        self.assertEqual(self.widget._history.active["id"], identifier)
        self.assertTrue(self.widget._context.export()["entries"][0]["stale"])
        self.assertEqual(self.widget.api_key.text(), "")

    def test_new_chat_retains_previous_conversation_in_sidecar(self):
        self.connect(scene_access=True)
        identifier = self.seed_chat()
        self.widget._new_chat()
        self.assertEqual(self.widget._messages, [])
        self.assertNotEqual(self.widget._history.active["id"], identifier)
        old = next(item for item in self.widget._history.document["conversations"] if item["id"] == identifier)
        self.assertEqual(old["messages"][0]["content"], "Explain this scene")
        self.assertTrue(self.widget._history.path.exists())
        self.assertEqual(len(self.widget._history.document["conversations"]), 2)

    def test_model_change_archives_history_under_its_original_route(self):
        self.connect(scene_access=True)
        identifier = self.seed_chat()
        self.assertEqual(self.widget.models.currentData(), "permitted-local")
        self.widget.models.setCurrentIndex(self.widget.models.findData("permitted-cloud"))
        old = next(item for item in self.widget._history.document["conversations"] if item["id"] == identifier)
        self.assertEqual(old["model"], "permitted-local")
        self.assertEqual(self.widget._history.active["model"], "permitted-cloud")
        self.assertEqual(self.widget._messages, [])

    def test_scene_access_change_keeps_old_agent_chat_in_agent_mode(self):
        self.connect(scene_access=True)
        identifier = self.seed_chat()
        self.widget.scene_tools.setChecked(False)
        old = next(item for item in self.widget._history.document["conversations"] if item["id"] == identifier)
        self.assertTrue(old["scene_access"])
        self.assertFalse(self.widget._history.active["scene_access"])
        self.assertEqual(self.widget._messages, [])

    def test_hip_load_restores_saved_model_and_context_collector_remains_active(self):
        self.connect(scene_access=True)
        self.seed_chat()
        self.widget._context.refresh()
        other_hip = self.folder / "other.hip"
        other = self.saved_other_scene(other_hip)
        self.hou.hipFile.load(other_hip)
        self.assertEqual(self.widget._history.active["id"], other["id"])
        self.assertEqual(self.widget.models.currentData(), "permitted-cloud")
        self.assertEqual(self.widget._messages[0]["content"], "Other HIP question")
        self.assertTrue(self.widget._route_matches())
        self.widget._context.refresh("Explain @/obj/geo1")
        context = self.widget._context.export()
        self.assertEqual(context["references"], ["/obj/geo1"])
        self.assertEqual([entry["path"] for entry in context["overview"]], ["/obj", "/stage"])
        self.assertFalse(context["overview_stale"])
        self.widget._context.observe_tool(CALL, inspection_result())
        self.assertFalse(self.widget._context.export()["entries"][0]["stale"])
        self.assertEqual(self.saved_active(self.first_hip)["messages"][0]["content"], "Explain this scene")

    def test_hip_load_of_other_server_history_cannot_send_to_current_route(self):
        self.connect(scene_access=True)
        self.seed_chat()
        other_hip = self.folder / "other_server.hip"
        self.saved_other_scene(other_hip, server="https://different-studio.invalid")
        self.hou.hipFile.load(other_hip)
        self.widget.prompt.setPlainText("Continue this conversation")
        count = len(qt_support.StudioHandler.requests)
        self.assertFalse(self.widget.send_button.isEnabled())
        self.widget._send()  # Ctrl+Enter must also enforce the route guard.
        self.app.processEvents()
        self.assertEqual(len(qt_support.StudioHandler.requests), count)
        self.assertIn("another server or model", self.widget.status.text())

    def test_save_as_copies_full_book_and_subsequent_changes_leave_old_sidecar(self):
        self.connect(scene_access=True)
        self.seed_chat()
        self.widget._new_chat()
        self.seed_chat("Second chat", "Second answer")
        old_path = self.widget._history.path
        self.hou.hipFile.save_as(self.folder / "test_v002.hip")
        old_bytes = old_path.read_bytes()
        copied = panel.conversations.history_store.load(self.widget._history.path)[0]
        self.assertEqual(len(copied["conversations"]), 2)
        self.assertEqual(copied["conversations"][0]["messages"][0]["content"], "Explain this scene")
        self.widget.prompt.setPlainText("Only the second version gets this draft")
        self.widget._checkpoint()
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertEqual(self.saved_active()["draft"], "Only the second version gets this draft")

    def test_action_is_persisted_running_before_execution_and_receipt_afterward(self):
        self.connect(scene_access=True)
        self.widget._messages = []
        self.widget._agent_history = [{"role": "user", "content": "Perform a synthetic action"}]
        self.widget._agent_model_id = "permitted-local"
        self.widget._pending_prompt = "Perform a synthetic action"
        arguments = {"summary": "Synthetic reviewed action", "code": "result = 'synthetic'", "target_paths": []}
        call = self.tool_call("synthetic_action", "houlm_propose_python", arguments)
        read_call = copy.deepcopy(CALL)
        read_call["type"] = "function"
        assistant = {"role": "assistant", "content": "", "tool_calls": [read_call, call]}
        self.widget._context.observe_tool(read_call, inspection_result())
        generation = self.widget._generation
        result = {"status": "applied", "success": True, "execution_started": True,
                  "result": "synthetic", "stdout": "", "manual_undo_needed": False}

        def apply(unused_hou, unused_plan, **kwargs):
            saved = self.saved_active()
            self.assertEqual(saved["actions"][-1]["status"], "running")
            self.assertEqual(saved["actions"][-1]["arguments"]["code"], "result = 'synthetic'")
            self.assertNotIn("result", saved["actions"][-1])
            return result

        with mock.patch.object(panel.python_actions, "apply", side_effect=apply) as execution, mock.patch.object(self.widget, "_next_agent_round"):
            self.widget._show_edit_review(types.SimpleNamespace(summary="Exact synthetic source"), call,
                                          [read_call, call], [inspection_result()], assistant, generation)
            self.assertEqual(self.saved_active()["actions"][-1]["status"], "review")
            execution.assert_not_called()
            self.widget.review_apply_button.click()
            self.wait_for(lambda: self.widget._history.active["actions"][-1]["status"] == "applied")
            execution.assert_called_once()
            self.app.processEvents()
        receipt = self.saved_active()
        self.assertEqual(receipt["actions"][-1]["status"], "applied")
        self.assertEqual(receipt["actions"][-1]["result"], result)
        self.assertEqual([message["role"] for message in receipt["messages"]], ["user", "assistant", "tool", "tool"])
        self.assertEqual(json.loads(receipt["messages"][-1]["content"])["result"], "synthetic")
        self.assertTrue(receipt["scene_context"]["entries"][0]["stale"],
                        "A pre-edit read must not be refreshed after applying its batch's action")

    def test_interrupted_action_restores_unknown_receipt_without_replaying_code_or_request(self):
        self.connect(scene_access=True)
        other_hip = self.folder / "interrupted.hip"
        self.saved_other_scene(other_hip)
        store = panel.conversations.history_store
        sidecar = store.sidecar_path(other_hip)
        document, revision = store.load(sidecar)
        item = document["conversations"][0]
        item["turn_state"] = "working"
        item["actions"] = [{"status": "running", "arguments": {"code": "Never replay this"}}]
        store.save(sidecar, document, revision)
        requests = len(qt_support.StudioHandler.requests)
        with mock.patch.object(panel.python_actions, "apply") as execute, mock.patch.object(panel.edits, "apply") as edit:
            self.hou.hipFile.load(other_hip)
            self.app.processEvents()
            execute.assert_not_called()
            edit.assert_not_called()
        self.assertEqual(len(qt_support.StudioHandler.requests), requests)
        self.assertEqual(self.widget._history.active["actions"][0]["status"], "unknown")
        self.assertIn("No saved action was replayed", self.widget._resume_note)
        self.assertIsNone(self.widget._pending_review)


if __name__ == "__main__":
    unittest.main()
