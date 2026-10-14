"""Old-panel migration and visible JSON status; native Qt with fake HOM only."""

import copy
import json
import unittest
from unittest import mock

import test_panel as qt_support
import test_panel_history as history_support

if qt_support._qt_available:
    from PySide2 import QtWidgets
    panel = qt_support.panel


@unittest.skipUnless(qt_support._qt_available, qt_support._qt_reason)
class PanelMigrationTests(unittest.TestCase):
    # Reuse the disposable HIP/JSON fixture without starting a network server
    # or inheriting unrelated tests. Every credential/preferences API is mocked.
    setUp = history_support.PanelHistoryTests.setUp
    tearDown = history_support.PanelHistoryTests.tearDown
    destroy_widget = history_support.PanelHistoryTests.destroy_widget
    saved_active = history_support.PanelHistoryTests.saved_active
    seed_chat = history_support.PanelHistoryTests.seed_chat

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def legacy_snapshot(self, *, interrupted=False):
        source = 'result = {"path": "/obj/migration_test"}'
        arguments = {"summary": "Create a synthetic node", "code": source, "target_paths": ["/obj"]}
        call = qt_support.PanelTests.tool_call("legacy-command-1", "houlm_propose_python", arguments)
        return {
            "messages": [
                {"role": "user", "content": "Create a synthetic node."},
                {"role": "assistant", "content": "Creating it.", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": "legacy-command-1",
                 "content": json.dumps({"success": True, "result": {"path": "/obj/migration_test"}})},
                {"role": "assistant", "content": "The requested node was created."},
            ],
            "transcript": ["You:\nCreate a synthetic node.", "Assistant:\nThe requested node was created."],
            "draft": "Explain the new node.",
            "server": "https://studio.example.test:8088",
            "model": "studio-chatgpt",
            "scene_access": True,
            "turn_state": "interrupted" if interrupted else "idle",
        }

    def test_legacy_chat_is_saved_with_exact_messages_draft_and_transcript_without_replay(self):
        snapshot = self.legacy_snapshot()
        original = copy.deepcopy(snapshot)
        with mock.patch.object(self.widget, "_request") as request, \
                mock.patch.object(panel.python_actions, "apply") as python, \
                mock.patch.object(panel.edits, "apply") as edits:
            self.widget._import_legacy_conversation(snapshot)
            self.app.processEvents()
            request.assert_not_called()
            python.assert_not_called()
            edits.assert_not_called()
        saved = self.saved_active()
        self.assertEqual(saved["messages"], original["messages"])
        self.assertEqual(saved["transcript"], original["transcript"])
        self.assertEqual(saved["draft"], original["draft"])
        self.assertEqual(saved["server"], original["server"])
        self.assertEqual(saved["model"], original["model"])
        self.assertEqual(self.widget._messages, original["messages"])
        self.assertEqual(self.widget.prompt.toPlainText(), original["draft"])
        self.assertEqual(self.widget._history.path, self.folder / ".houlm" / "test_v001.hip.json")
        self.assertEqual(saved["actions"], [])
        self.assertIsNone(self.widget._pending_review)
        snapshot["messages"][0]["content"] = "Mutated caller data"
        self.assertEqual(self.widget._messages, original["messages"])
        self.assertEqual(qt_support.StudioHandler.requests, [])

    def test_import_archives_existing_conversation_instead_of_replacing_it(self):
        original_id = self.seed_chat("Existing sidecar question", "Existing sidecar answer")
        original = copy.deepcopy(self.widget._history.active)
        self.widget._import_legacy_conversation(self.legacy_snapshot())
        document, unused = panel.conversations.history_store.load(self.widget._history.path)
        self.assertEqual(len(document["conversations"]), 2)
        archived = next(item for item in document["conversations"] if item["id"] == original_id)
        self.assertEqual(archived, original)
        self.assertNotEqual(document["active_conversation_id"], original_id)

    def test_import_archives_a_draft_only_conversation(self):
        self.widget.prompt.setPlainText("Keep this existing unsent draft.")
        self.widget._checkpoint()
        original_id = self.widget._history.active["id"]
        self.widget._import_legacy_conversation(self.legacy_snapshot())
        archived = next(item for item in self.widget._history.document["conversations"] if item["id"] == original_id)
        self.assertEqual(archived["draft"], "Keep this existing unsent draft.")
        self.assertNotEqual(self.widget._history.active["id"], original_id)

    def test_import_does_not_restore_credentials_preferences_or_auto_run(self):
        self.widget.server_url.setText("https://currently-configured.example.test")
        self.widget.api_key.setText("synthetic-existing-key")
        self.widget.auto_run_commands.setChecked(False)
        snapshot = self.legacy_snapshot()
        snapshot.update(api_key="must-not-import-this", auto_run_commands=True, password="must-not-import-this")
        with mock.patch.object(panel.core, "save_preferences") as preferences, \
                mock.patch.object(panel.credentials, "save_key") as save_key, \
                mock.patch.object(panel.credentials, "load_key") as load_key:
            self.widget._import_legacy_conversation(snapshot)
            preferences.assert_not_called()
            save_key.assert_not_called()
            load_key.assert_not_called()
        self.assertEqual(self.widget.server_url.text(), "https://currently-configured.example.test")
        self.assertEqual(self.widget.api_key.text(), "synthetic-existing-key")
        self.assertFalse(self.widget.auto_run_commands.isChecked())
        persisted = self.widget._history.path.read_text(encoding="utf-8")
        self.assertNotIn("must-not-import-this", persisted)
        self.assertNotIn("synthetic-existing-key", persisted)
        self.assertNotIn("auto_run_commands", self.saved_active())

    def test_import_preserves_generation_for_already_queued_auto_connect(self):
        snapshot = self.legacy_snapshot()
        self.widget.server_url.setText(snapshot["server"])
        self.widget.api_key.setText("synthetic-saved-key")
        queued_generation = self.widget._generation
        self.widget._import_legacy_conversation(snapshot)
        self.assertEqual(self.widget._generation, queued_generation)
        with mock.patch.object(self.widget, "_connect") as connect:
            self.widget._auto_connect(snapshot["server"], queued_generation)
            connect.assert_called_once_with()

    def test_interrupted_legacy_warning_survives_another_panel_reload(self):
        self.widget._import_legacy_conversation(self.legacy_snapshot(interrupted=True))
        self.assertEqual(self.saved_active()["turn_state"], "interrupted")
        self.assertIn("No saved action was replayed", self.widget._resume_note)
        self.destroy_widget()
        with mock.patch.object(panel.python_actions, "apply") as python, \
                mock.patch.object(panel.edits, "apply") as edits:
            self.widget = panel.AssistantPanel()
            self.app.processEvents()
            python.assert_not_called()
            edits.assert_not_called()
        self.assertIn("No saved action was replayed", self.widget._resume_note)
        self.assertEqual(self.saved_active()["turn_state"], "interrupted")

    def test_footer_distinguishes_destination_from_successful_json_save(self):
        self.widget.resize(640, 800)
        self.widget.show()
        self.app.processEvents()
        self.assertIn("JSON not saved yet", self.widget.history_status.toolTip())
        self.assertTrue(self.widget.history_status.isVisible())
        self.assertTrue(self.widget.history_location_button.isVisible())
        self.assertEqual(self.widget.history_location_button.text(), "Log")
        self.assertFalse(self.widget._history.path.exists())
        self.widget.prompt.setPlainText("Persist this draft.")
        self.widget._checkpoint()
        self.assertIn("JSON saved · test_v001.hip.json", self.widget.history_status.toolTip())
        self.assertIn(str(self.widget._history.path), self.widget.history_status.toolTip())
        self.assertFalse(self.widget.history_status.property("saveError"))
        self.assertTrue(self.widget._history.path.is_file())

    def test_unsaved_footer_and_log_dialog_do_not_promise_a_file(self):
        self.hou.hipFile.clear(self.folder / "untitled.hip")
        self.assertIn("Unsaved HIP", self.widget.history_status.toolTip())
        self.assertIsNone(self.widget._history.path)
        observed = []

        def examine(dialog):
            observed.append(dialog.windowTitle())
            fields = dialog.findChildren(QtWidgets.QLineEdit)
            self.assertEqual(len(fields), 1)
            self.assertTrue(fields[0].isReadOnly())
            self.assertIn("Save your HIP first", fields[0].text())
            buttons = {button.text(): button for button in dialog.findChildren(QtWidgets.QPushButton)}
            self.assertFalse(buttons["Copy path"].isEnabled())
            self.assertFalse(buttons["Open folder"].isEnabled())
            return QtWidgets.QDialog.Rejected

        with mock.patch.object(QtWidgets.QDialog, "exec_", examine):
            self.widget._show_history_location()
        self.assertEqual(observed, ["Chat history file"])

    def test_log_dialog_exposes_exact_path_and_retry_reports_actual_save_status(self):
        store = panel.conversations.history_store
        real_save = store.save
        self.widget.prompt.setPlainText("Keep this draft after a save failure.")
        with mock.patch.object(store, "save", side_effect=store.StoreError("write_failed")) as save:
            self.widget._checkpoint()
            self.assertIn("JSON not saved", self.widget.history_status.toolTip())
            self.assertTrue(self.widget.history_status.property("saveError"))

            def examine(dialog):
                field = dialog.findChildren(QtWidgets.QLineEdit)[0]
                self.assertEqual(field.text(), str(self.folder / ".houlm" / "test_v001.hip.json"))
                self.assertEqual(field.accessibleName(), "Chat history JSON path")
                buttons = {button.text(): button for button in dialog.findChildren(QtWidgets.QPushButton)}
                self.assertTrue(buttons["Copy path"].isEnabled())
                self.assertFalse(buttons["Open folder"].isEnabled())
                self.assertTrue(any("JSON not saved" in label.text() for label in dialog.findChildren(QtWidgets.QLabel)))
                save.side_effect = real_save
                buttons["Save now"].click()
                self.assertTrue(any("JSON saved" in label.text() for label in dialog.findChildren(QtWidgets.QLabel)))
                self.assertTrue(buttons["Open folder"].isEnabled())
                return QtWidgets.QDialog.Rejected

            with mock.patch.object(QtWidgets.QDialog, "exec_", examine):
                self.widget._show_history_location()
        self.assertTrue(self.widget._history.path.exists())
        self.assertEqual(self.saved_active()["draft"], "Keep this draft after a save failure.")
        self.assertFalse(self.widget.history_status.property("saveError"))


if __name__ == "__main__":
    unittest.main()
