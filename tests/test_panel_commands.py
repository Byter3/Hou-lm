"""Command consent and receipts in real Qt with synthetic Houdini objects.

No model server or real HIP is used. The Python/typed executors themselves run
against the same in-memory HOM doubles as their own unit tests.
"""

import copy
import json
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest import mock

import test_panel as qt_support
from test_conversations import EVENTS, FakeHipFile
from test_edits import arguments as edit_arguments, parameter

if qt_support._qt_available:
    from PySide2 import QtCore, QtWidgets
    import shiboken2
    panel = qt_support.panel


@unittest.skipUnless(qt_support._qt_available, qt_support._qt_reason)
class PanelCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    tool_call = staticmethod(qt_support.PanelTests.tool_call)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.hou = qt_support.PanelTests.python_hou()
        self.hou.session = types.SimpleNamespace()
        self.hou.hipFileEventType = EVENTS
        hip = self.folder / "commands.hip"
        hip.write_bytes(b"Synthetic existence marker; not a real Houdini scene")
        self.hou.hipFile = FakeHipFile(hip, new=False)
        self.hou.hipFile.basename = lambda: Path(self.hou.hipFile.path()).name
        self.preferences = {"server_url": "https://studio.invalid", "model_id": "synthetic-model"}
        self.prefs_write = mock.Mock(side_effect=self.save_preferences)
        patches = [
            mock.patch.object(panel, "hou", self.hou),
            mock.patch.object(panel.core, "load_preferences", side_effect=lambda: copy.deepcopy(self.preferences)),
            mock.patch.object(panel.core, "save_preferences", self.prefs_write),
            mock.patch.object(panel.credentials, "load_key", return_value=""),
            mock.patch.object(panel.credentials, "save_key"),
            mock.patch.object(panel.credentials, "delete_key"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.widget = panel.AssistantPanel()
        self.prepare_connection()
        self.command_number = 0

    def save_preferences(self, server, model, **settings):
        self.preferences.update(server_url=server, model_id=model)
        self.preferences.update(settings)

    def prepare_connection(self):
        self.widget._connected = True
        self.widget._server = "https://studio.invalid"
        self.widget.models.blockSignals(True)
        self.widget.models.addItem("Synthetic model", "synthetic-model")
        self.widget.models.blockSignals(False)
        self.widget._next_agent_round = mock.Mock()
        self.widget._update_controls()

    def destroy_widget(self):
        if shiboken2.isValid(self.widget):
            self.widget.disconnect()
            self.widget.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.app.processEvents()

    def tearDown(self):
        self.destroy_widget()

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.001)
        self.fail("Deferred command did not reach expected state: " + self.widget.status.text())

    def propose(self, source=None, *, typed=False):
        self.command_number += 1
        if typed:
            args = edit_arguments(parameter(value=3.25))
            tool_name = "houlm_propose_edits"
        else:
            source = source or ('hou.node("/obj/geo1/a").parm("density").set(2.5, follow_parm_reference=False)\n'
                                'print("Synthetic density changed")\n'
                                'result = {"density": hou.node("/obj/geo1/a").parm("density").rawValue()}\n')
            args = qt_support.PanelTests.python_arguments(source)
            tool_name = "houlm_propose_python"
        call = self.tool_call("command_%d" % self.command_number, tool_name, args)
        assistant = {"role": "assistant", "content": "Performing the synthetic test change.", "tool_calls": [call]}
        self.widget._agent_history = [{"role": "user", "content": "Change the synthetic density"}]
        self.widget._agent_model_id = "synthetic-model"
        self.widget._prepare_agent_proposal(assistant, [call], [], self.widget._generation)
        return args

    def saved_active(self):
        self.assertTrue(self.widget._history.path.is_file(), self.widget._history.error)
        document = json.loads(self.widget._history.path.read_text(encoding="utf-8"))
        return next(item for item in document["conversations"] if item["id"] == document["active_conversation_id"])

    def test_default_requires_manual_review_and_records_exact_source_before_execution(self):
        self.assertFalse(self.widget.auto_run_commands.isChecked())
        with mock.patch.object(panel.python_actions, "apply", wraps=panel.python_actions.apply) as execution:
            args = self.propose()
            self.assertIsNotNone(self.widget._pending_review)
            self.assertIsNotNone(self.widget._review_dialog)
            self.assertEqual(self.hou.a.parm("density").value, 1.0)
            self.app.processEvents()
            execution.assert_not_called()
            record = self.widget.transcript.export_commands()[-1]
            self.assertEqual(record["source"], args["code"])
            self.assertEqual(record["status"], "pending")
            self.widget.review_apply_button.click()
            execution.assert_not_called()
            self.wait_for(lambda: execution.called)
            execution.assert_called_once()
        self.assertEqual(self.hou.a.parm("density").value, 2.5)
        self.assertEqual(self.widget.transcript.export_commands()[-1]["status"], "completed")

    def test_auto_python_prepares_then_runs_once_without_opening_review_dialog(self):
        self.widget.auto_run_commands.setChecked(True)
        with mock.patch.object(panel.python_actions, "prepare", wraps=panel.python_actions.prepare) as prepare, mock.patch.object(
            panel.python_actions, "apply", wraps=panel.python_actions.apply
        ) as execution:
            args = self.propose()
            prepare.assert_called_once_with(self.hou, args)
            execution.assert_not_called()
            self.assertIsNone(self.widget._review_dialog)
            self.assertIsNone(self.widget._pending_review)
            self.assertIsNotNone(self.widget._queued_action)
            self.wait_for(lambda: execution.called)
            self.app.processEvents()
            execution.assert_called_once()
        self.assertEqual(self.hou.a.parm("density").value, 2.5)
        receipt = self.widget.transcript.export_commands()[-1]
        self.assertEqual(receipt["source"], args["code"])
        self.assertIn(args["summary"], receipt["title"])
        self.assertNotIn('hou.node(', receipt["title"])
        self.assertNotIn("\n", receipt["title"])
        self.assertEqual(receipt["status"], "completed")
        self.assertIn("Synthetic density changed", receipt["result_text"])
        self.assertIsNone(self.widget._queued_action)

    def test_auto_typed_edits_execute_without_popup_and_keep_exact_arguments(self):
        self.widget.auto_run_commands.setChecked(True)
        with mock.patch.object(panel.edits, "prepare", wraps=panel.edits.prepare) as prepare, mock.patch.object(
            panel.edits, "apply", wraps=panel.edits.apply
        ) as execution:
            args = self.propose(typed=True)
            prepare.assert_called_once_with(self.hou, args)
            self.assertIsNone(self.widget._review_dialog)
            execution.assert_not_called()
            self.wait_for(lambda: execution.called)
            execution.assert_called_once()
        self.assertEqual(self.hou.a.parm("density").value, 3.25)
        receipt = self.widget.transcript.export_commands()[-1]
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(json.loads(receipt["source"]), args)

    def test_auto_run_never_executes_a_proposal_that_fails_preparation(self):
        self.widget.auto_run_commands.setChecked(True)
        with mock.patch.object(panel.python_actions, "apply") as execution:
            self.propose("def invalid source !")
            self.app.processEvents()
            execution.assert_not_called()
        self.assertIsNone(self.widget._review_dialog)
        self.assertIsNone(self.widget._queued_action)
        result = json.loads(self.widget._messages[-1]["content"])
        self.assertFalse(result["execution_started"])
        self.assertEqual(result["status"], "invalid")

    def test_revoking_auto_run_before_dispatch_reopens_review_without_mutating(self):
        self.widget.auto_run_commands.setChecked(True)
        with mock.patch.object(panel.python_actions, "apply", wraps=panel.python_actions.apply) as execution:
            self.propose()
            self.widget.auto_run_commands.setChecked(False)
            self.wait_for(lambda: self.widget._pending_review is not None)
            execution.assert_not_called()
            self.assertEqual(self.hou.a.parm("density").value, 1.0)
            self.assertIsNotNone(self.widget._review_dialog)
            self.assertEqual(len(self.widget.transcript.export_commands()), 1)
            self.widget.review_apply_button.click()
            self.wait_for(lambda: execution.called)
            execution.assert_called_once()
        self.assertEqual(self.hou.a.parm("density").value, 2.5)

    def test_stop_cancels_queued_auto_command_and_records_that_it_never_ran(self):
        self.widget.auto_run_commands.setChecked(True)
        with mock.patch.object(panel.python_actions, "apply", wraps=panel.python_actions.apply) as execution:
            self.propose()
            self.widget.activity_banner.stop_button.click()
            self.app.processEvents()
            execution.assert_not_called()
        self.assertEqual(self.hou.a.parm("density").value, 1.0)
        self.assertIsNone(self.widget._queued_action)
        self.assertIsNone(self.widget._pending_review)
        self.assertEqual(self.widget.transcript.export_commands()[-1]["status"], "cancelled")

    def test_enabling_auto_run_during_manual_review_dispatches_existing_proposal_once(self):
        with mock.patch.object(panel.python_actions, "apply", wraps=panel.python_actions.apply) as execution:
            self.propose()
            self.assertIsNotNone(self.widget._review_dialog)
            self.widget.auto_run_commands.setChecked(True)
            self.assertIsNone(self.widget._review_dialog)
            execution.assert_not_called()
            self.wait_for(lambda: execution.called)
            self.app.processEvents()
            execution.assert_called_once()
        self.assertEqual(len(self.widget.transcript.export_commands()), 1)

    def test_checkbox_is_saved_as_workstation_preference_not_sidecar_permission(self):
        self.widget.auto_run_commands.setChecked(True)
        self.assertTrue(self.preferences["auto_run_commands"])
        self.assertTrue(self.prefs_write.call_args.kwargs["auto_run_commands"])
        self.widget._checkpoint()
        saved = self.saved_active()
        self.assertNotIn("auto_run_commands", saved)
        self.destroy_widget()
        self.widget = panel.AssistantPanel()
        self.assertTrue(self.widget.auto_run_commands.isChecked())
        self.prepare_connection()
        self.widget.auto_run_commands.setChecked(False)
        self.assertFalse(self.preferences["auto_run_commands"])

    def test_saved_scene_content_cannot_enable_automatic_execution(self):
        self.widget._history.active["auto_run_commands"] = True
        self.widget._restore_history()
        self.assertFalse(self.widget.auto_run_commands.isChecked())

    def test_exact_commands_persist_and_restore_without_reexecution_even_with_auto_enabled(self):
        self.widget.auto_run_commands.setChecked(True)
        with mock.patch.object(panel.python_actions, "apply", wraps=panel.python_actions.apply) as execution:
            args = self.propose()
            self.wait_for(lambda: execution.called)
        self.widget._checkpoint()
        saved = self.saved_active()
        self.assertEqual(saved["commands"][-1]["source"], args["code"])
        self.assertEqual(saved["commands"][-1]["status"], "completed")
        self.assertIn("Synthetic density changed", saved["commands"][-1]["result_text"])
        self.destroy_widget()
        # Force JSON reload rather than process-local history reuse.
        self.hou.session._houlm_conversations = None
        with mock.patch.object(panel.python_actions, "apply") as execution, mock.patch.object(panel.edits, "apply") as edits:
            self.widget = panel.AssistantPanel()
            self.prepare_connection()
            self.app.processEvents()
            execution.assert_not_called()
            edits.assert_not_called()
        self.assertTrue(self.widget.auto_run_commands.isChecked())
        restored = self.widget.transcript.export_commands()[-1]
        self.assertEqual(restored["source"], args["code"])
        self.assertEqual(restored["status"], "completed")
        self.assertEqual(self.widget.transcript._expanded, set())
        self.assertIsNone(self.widget._queued_action)
        self.assertIsNone(self.widget._pending_review)

    def test_failed_python_receipt_retains_source_and_execution_error(self):
        self.widget.auto_run_commands.setChecked(True)
        code = 'print("Synthetic failure output")\nraise ValueError("synthetic command failure")\n'
        with mock.patch.object(panel.python_actions, "apply", wraps=panel.python_actions.apply) as execution:
            self.propose(code)
            self.wait_for(lambda: execution.called)
        receipt = self.widget.transcript.export_commands()[-1]
        self.assertEqual(receipt["source"], code)
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("synthetic command failure", receipt["result_text"])
        self.assertIn("Synthetic failure output", receipt["result_text"])


if __name__ == "__main__":
    unittest.main()
