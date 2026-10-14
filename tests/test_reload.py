"""Reload coordinates disposable children without rebuilding native panes."""

import builtins
import contextlib
import io
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest import mock


RELOADER = Path(__file__).resolve().parents[1] / "scripts" / "reload_houdini.py"


class FakeSignal:
    def __init__(self):
        self.handlers = []

    def connect(self, handler):
        self.handlers.append(handler)

    def disconnect(self, handler):
        self.handlers.remove(handler)

    def emit(self):
        for handler in list(self.handlers):
            handler()


class FakeHost:
    _houlm_host_version = 1

    def __init__(self):
        self._valid = True
        self._closed = False
        self._retiring = False
        self.child = mock.Mock()
        self.child._connected = False
        self.destroyed = FakeSignal()
        self.callback = None
        self.assistant_widget = mock.Mock(side_effect=lambda: self.child)
        self.retire_assistant = mock.Mock(side_effect=self._retire)
        self.install_assistant = mock.Mock()
        self.show_reload_failure = mock.Mock()

    def _retire(self, callback):
        self._retiring = True
        self.callback = callback

    def destroy_child(self):
        self.child = None
        self._retiring = False
        callback, self.callback = self.callback, None
        if callback is not None:
            callback(not self._closed)

    def destroy(self):
        self._closed = True
        self._valid = False
        self.destroyed.emit()


class ReloadTests(unittest.TestCase):
    def setUp(self):
        self.old_path = list(sys.path)
        self.ui_queue = []
        self.qt_queue = []
        self.main_thread = object()
        self.app = mock.Mock()
        self.app.thread.return_value = self.main_thread
        self.hou = types.ModuleType("hou")
        self.hou.isUIAvailable = mock.Mock(return_value=True)
        self.hou.session = types.SimpleNamespace()
        self.hou.paneTabType = types.SimpleNamespace(PythonPanel="python_panel")
        self.hou.pypanel = mock.Mock()
        self.hou.ui = mock.Mock()
        self.hou.ui.paneTabs.return_value = ()
        self.defer = types.ModuleType("hdefereval")
        self.defer.executeDeferred = mock.Mock(side_effect=self.ui_queue.append)
        self.qtcore = types.SimpleNamespace(
            QThread=types.SimpleNamespace(currentThread=mock.Mock(return_value=self.main_thread)),
            QTimer=types.SimpleNamespace(singleShot=mock.Mock(
                side_effect=lambda delay, callback: self.qt_queue.append(callback))))
        self.qtwidgets = types.SimpleNamespace(
            QApplication=types.SimpleNamespace(instance=mock.Mock(return_value=self.app)))
        pyside = types.ModuleType("PySide2")
        pyside.QtCore = self.qtcore
        pyside.QtWidgets = self.qtwidgets
        shiboken = types.ModuleType("shiboken2")
        shiboken.isValid = lambda widget: getattr(widget, "_valid", True)
        self.package = types.ModuleType("houlm")
        self.package.__path__ = []
        self.core = types.ModuleType("houlm.core")
        self.core.normalize_server_url = mock.Mock(side_effect=lambda value: value.strip().rstrip("/"))
        self.credentials = types.ModuleType("houlm.credentials")
        self.credentials.save_key = mock.Mock()
        self.package.core = self.core
        self.package.credentials = self.credentials
        self.modules = mock.patch.dict(sys.modules, {
            "hou": self.hou, "hdefereval": self.defer, "PySide2": pyside,
            "shiboken2": shiboken, "houlm": self.package,
            "houlm.core": self.core, "houlm.credentials": self.credentials})
        self.modules.start()
        self.reload_patch = mock.patch("importlib.reload")
        self.reload_module = self.reload_patch.start()
        self.caches_patch = mock.patch("importlib.invalidate_caches")
        self.invalidate = self.caches_patch.start()
        self.stdout = io.StringIO()
        self.output = contextlib.redirect_stdout(self.stdout)
        self.output.__enter__()
        self.namespace = runpy.run_path(str(RELOADER))
        self.host = FakeHost()
        self.tabs = []
        self.use_hosts(self.host)

    def tearDown(self):
        try:
            self.assert_no_native_rebuild()
        finally:
            self.output.__exit__(None, None, None)
            self.caches_patch.stop()
            self.reload_patch.stop()
            self.modules.stop()
            sys.path[:] = self.old_path

    def use_hosts(self, *hosts):
        tabs = []
        for host in hosts:
            tab = mock.Mock()
            tab.type.return_value = "python_panel"
            tab.activeInterface.return_value.name.return_value = "hou_lm"
            tab.activeInterfaceRootWidget.return_value = host
            tabs.append(tab)
        self.tabs.extend(tabs)
        self.hou.ui.paneTabs.return_value = tuple(tabs)

    def assert_no_native_rebuild(self):
        self.hou.pypanel.installFile.assert_not_called()
        self.hou.pypanel.setMenuInterfaces.assert_not_called()
        self.hou.ui.curDesktop.assert_not_called()
        for tab in self.tabs:
            tab.reloadActiveInterface.assert_not_called()
            tab.setActiveInterface.assert_not_called()
            tab.close.assert_not_called()
            tab.pane.assert_not_called()

    def begin(self):
        self.namespace["reload_panel"]()
        self.assertTrue(self.hou.session._hou_lm_reload_pending)
        self.assertEqual(len(self.ui_queue), 1)
        self.ui_queue.pop(0)()

    def finish(self):
        self.assertEqual(len(self.qt_queue), 1)
        self.qt_queue.pop(0)()

    def connected_child(self, host):
        child = host.child
        child._connected = True
        child._server = "https://studio.example.test:8088"
        child.server_url.text.return_value = child._server + "/"
        child.api_key.text.return_value = "synthetic-key"
        return child

    def legacy_child(self, host=None, *, messages=None, transcript=None, draft=""):
        host = host or self.host
        child = types.SimpleNamespace(
            _connected=False, _server="https://studio.example.test:8088",
            _messages=messages if messages is not None else [
                {"role": "user", "content": "Create a test object."},
                {"role": "assistant", "content": "Created the test object."}],
            _agent_history=None, _reply=None, _pending_review=None,
            _pending_messages=None, _pending_prompt="",
            transcript=types.SimpleNamespace(_parts=transcript if transcript is not None else [
                "You:\nCreate a test object.\n", "Assistant:\nCreated the test object.\n"]),
            prompt=types.SimpleNamespace(toPlainText=lambda: draft),
            models=types.SimpleNamespace(currentData=lambda: "studio-chatgpt"),
            scene_tools=types.SimpleNamespace(isChecked=lambda: True),
            api_key=types.SimpleNamespace(text=mock.Mock(return_value="synthetic-secret")))
        host.child = child
        replacement = mock.Mock()
        replacement._history = object()
        host.install_assistant.side_effect = lambda: setattr(host, "child", replacement)
        return child, replacement

    def enable_hip_events(self):
        hip = types.SimpleNamespace(filename="C:/synthetic/first.hip", callbacks=[])
        hip.path = lambda: hip.filename
        hip.addEventCallback = hip.callbacks.append
        self.hou.hipFile = hip
        self.hou.hipFileEventType = types.SimpleNamespace(BeforeLoad="before_load", BeforeClear="before_clear")
        return hip

    def test_shell_defers_all_qt_imports_and_host_access(self):
        actual_import = builtins.__import__

        def safe_shell_import(name, *args, **kwargs):
            if name.startswith("PySide2") or name == "shiboken2":
                self.fail("Shell imported a Qt module before deferred callback")
            return actual_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=safe_shell_import):
            runpy.run_path(str(RELOADER), run_name="__main__")
        self.assertEqual(len(self.ui_queue), 1)
        self.hou.ui.paneTabs.assert_not_called()
        self.reload_module.assert_not_called()

    def test_one_reload_is_pending_until_child_destruction_and_next_qt_turn(self):
        self.namespace["reload_panel"]()
        self.namespace["reload_panel"]()
        self.assertEqual(len(self.ui_queue), 1)
        self.ui_queue.pop(0)()
        self.host.retire_assistant.assert_called_once()
        self.reload_module.assert_not_called()
        self.assertEqual(self.qt_queue, [])
        self.namespace["reload_panel"]()
        self.assertEqual(self.ui_queue, [])
        self.host.destroy_child()
        self.reload_module.assert_not_called()
        self.host.install_assistant.assert_not_called()
        self.assertTrue(self.hou.session._hou_lm_reload_pending)
        self.qtcore.QTimer.singleShot.assert_called_once()
        self.assertEqual(self.qtcore.QTimer.singleShot.call_args.args[0], 0)
        self.finish()
        self.host.install_assistant.assert_called_once()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)
        self.assertEqual(self.host.destroyed.handlers, [])

    def test_two_hosts_wait_for_both_children_before_module_reload(self):
        second = FakeHost()
        self.use_hosts(self.host, second)
        self.begin()
        self.host.destroy_child()
        self.assertEqual(self.qt_queue, [])
        self.reload_module.assert_not_called()
        second.destroy_child()
        self.reload_module.assert_not_called()
        self.finish()
        self.host.install_assistant.assert_called_once()
        second.install_assistant.assert_called_once()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)

    def test_legacy_or_missing_root_leaves_current_pane_untouched(self):
        for legacy in (mock.Mock(), None):
            with self.subTest(kind=type(legacy).__name__):
                self.use_hosts(legacy)
                self.begin()
                self.assertFalse(self.hou.session._hou_lm_reload_pending)
                self.assertEqual(self.qt_queue, [])
                self.reload_module.assert_not_called()
                if legacy is not None:
                    legacy.disconnect.assert_not_called()
                    legacy.close.assert_not_called()
                    legacy.deleteLater.assert_not_called()
        self.assertIn("clean restart", self.stdout.getvalue())
        self.assertIn("pane + menu", self.stdout.getvalue())

    def test_no_hosts_does_not_create_a_new_pane(self):
        self.use_hosts()
        self.begin()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)
        self.assertEqual(self.qt_queue, [])
        self.reload_module.assert_not_called()

    def test_invalid_or_closing_or_retiring_host_prevents_any_retirement(self):
        for field in ("_valid", "_closed", "_retiring"):
            with self.subTest(field=field):
                other = FakeHost()
                setattr(other, field, field != "_valid")
                self.use_hosts(self.host, other)
                self.begin()
                self.host.retire_assistant.assert_not_called()
                other.retire_assistant.assert_not_called()
                self.reload_module.assert_not_called()
                self.assertFalse(self.hou.session._hou_lm_reload_pending)

    def test_all_verified_logins_are_saved_before_any_retirement(self):
        other = FakeHost()
        self.use_hosts(self.host, other)
        self.connected_child(self.host)
        self.connected_child(other)
        order = mock.Mock()
        order.attach_mock(self.credentials.save_key, "save")
        order.attach_mock(self.host.retire_assistant, "retire_first")
        order.attach_mock(other.retire_assistant, "retire_second")
        self.begin()
        self.assertEqual([call[0] for call in order.mock_calls],
                         ["save", "save", "retire_first", "retire_second"])
        self.credentials.save_key.assert_has_calls([
            mock.call("https://studio.example.test:8088", "synthetic-key"),
            mock.call("https://studio.example.test:8088", "synthetic-key")])
        self.assertNotIn("synthetic-key", self.stdout.getvalue())

    def test_mismatched_server_keeps_all_panels_alive(self):
        other = FakeHost()
        self.use_hosts(self.host, other)
        self.connected_child(self.host)
        child = self.connected_child(other)
        child.server_url.text.return_value = "https://other.example.test"
        self.begin()
        self.credentials.save_key.assert_called_once()
        self.host.retire_assistant.assert_not_called()
        other.retire_assistant.assert_not_called()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)

    def test_failed_credential_save_keeps_all_panels_alive_and_hides_exception(self):
        other = FakeHost()
        self.use_hosts(self.host, other)
        self.connected_child(self.host)
        self.connected_child(other)
        self.credentials.save_key.side_effect = (None, OSError("private-error-text"))
        self.begin()
        self.host.retire_assistant.assert_not_called()
        other.retire_assistant.assert_not_called()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)
        self.assertNotIn("private-error-text", self.stdout.getvalue())
        self.assertNotIn("synthetic-key", self.stdout.getvalue())

    def test_unverified_login_is_never_saved(self):
        for state in (False, None, 1, mock.Mock()):
            with self.subTest(state=type(state).__name__):
                child = self.connected_child(self.host)
                child._connected = state
                self.assertTrue(self.namespace["_preserve_active_login"](child))
        self.credentials.save_key.assert_not_called()

    def test_destroyed_host_completes_wait_without_installing_anything(self):
        self.begin()
        callback = self.host.callback
        self.host.destroy()
        callback(False)  # Duplicate child/parent destruction notification.
        self.finish()
        self.reload_module.assert_not_called()
        self.host.install_assistant.assert_not_called()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)
        self.assertIn("cancelled", self.stdout.getvalue())

    def test_host_closed_after_child_destroyed_but_before_finish_is_skipped(self):
        self.begin()
        self.host.destroy_child()
        self.host._closed = True
        self.finish()
        self.reload_module.assert_not_called()
        self.host.install_assistant.assert_not_called()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)

    def test_one_closed_host_does_not_prevent_other_host_reloading(self):
        second = FakeHost()
        self.use_hosts(self.host, second)
        self.begin()
        self.host.destroy()
        self.assertEqual(self.qt_queue, [])
        second.destroy_child()
        self.finish()
        self.host.install_assistant.assert_not_called()
        second.install_assistant.assert_called_once()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)

    def test_loaded_modules_reload_in_order_and_stable_host_is_excluded(self):
        names = self.namespace["RELOAD_MODULES"]
        loaded = {name: types.ModuleType(name) for name in names}
        loaded["houlm.panel_host"] = types.ModuleType("houlm.panel_host")
        with mock.patch.dict(sys.modules, loaded):
            self.begin()
            self.host.destroy_child()
            self.finish()
        self.assertEqual([call.args[0].__name__ for call in self.reload_module.call_args_list], list(names))
        self.assertNotIn("houlm.panel_host", names)
        self.assertLess(names.index("houlm.transcript"), names.index("houlm.panel"))
        self.assertEqual(names[-1], "houlm.panel")
        self.invalidate.assert_called_once()

    def test_module_reload_failure_resets_pending_without_native_fallback(self):
        self.reload_module.side_effect = RuntimeError("private-module-error")
        self.begin()
        self.host.destroy_child()
        self.finish()
        self.host.install_assistant.assert_not_called()
        self.host.show_reload_failure.assert_called_once()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)
        self.assertNotIn("private-module-error", self.stdout.getvalue())

    def test_retirement_failure_waits_for_other_child_and_clears_pending(self):
        second = FakeHost()
        self.use_hosts(self.host, second)
        self.host.retire_assistant.side_effect = RuntimeError("retire failed")
        self.begin()
        self.assertEqual(self.qt_queue, [])
        second.destroy_child()
        self.finish()
        self.reload_module.assert_not_called()
        self.host.show_reload_failure.assert_called_once()
        second.show_reload_failure.assert_called_once()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)

    def test_child_install_failure_resets_pending(self):
        self.host.install_assistant.side_effect = RuntimeError("install failed")
        self.begin()
        self.host.destroy_child()
        self.finish()
        self.host.show_reload_failure.assert_called_once()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)

    def test_worker_thread_failure_resets_pending_before_native_access(self):
        self.namespace["reload_panel"]()
        self.qtcore.QThread.currentThread.return_value = object()
        with self.assertRaisesRegex(RuntimeError, "UI thread"):
            self.ui_queue.pop(0)()
        self.hou.ui.paneTabs.assert_not_called()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)

    def test_headless_rejection_does_not_set_pending_or_queue(self):
        self.hou.isUIAvailable.return_value = False
        with self.assertRaisesRegex(RuntimeError, "graphical application"):
            self.namespace["reload_panel"]()
        self.assertFalse(getattr(self.hou.session, "_hou_lm_reload_pending", False))
        self.assertEqual(self.ui_queue, [])

    def test_deferred_dispatch_failure_does_not_leave_pending_latched(self):
        self.defer.executeDeferred.side_effect = RuntimeError("dispatch failed")
        with self.assertRaisesRegex(RuntimeError, "dispatch failed"):
            self.namespace["reload_panel"]()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)

    def test_legacy_chat_is_copied_before_disconnect_clears_old_widget(self):
        child, replacement = self.legacy_child(draft="Explain the new setup.")

        def retire(callback):
            child._messages.clear()
            child.transcript._parts.clear()
            self.host._retire(callback)

        self.host.retire_assistant.side_effect = retire
        self.begin()
        replacement._import_legacy_conversation.assert_not_called()
        self.host.destroy_child()
        self.finish()
        snapshot = replacement._import_legacy_conversation.call_args.args[0]
        self.assertEqual(len(snapshot["messages"]), 2)
        self.assertEqual(len(snapshot["transcript"]), 2)
        self.assertEqual(snapshot["draft"], "Explain the new setup.")
        self.assertEqual(snapshot["server"], "https://studio.example.test:8088")
        self.assertEqual(snapshot["model"], "studio-chatgpt")
        self.assertTrue(snapshot["scene_access"])
        self.assertEqual(snapshot["turn_state"], "idle")
        self.assertEqual(set(snapshot), {"messages", "transcript", "draft", "server", "model", "scene_access", "turn_state"})
        child.api_key.text.assert_not_called()
        replacement._send.assert_not_called()
        replacement._request.assert_not_called()
        self.assertEqual(self.hou._houlm_legacy_reload_snapshots, {})

    def test_completed_agent_batches_are_preserved_without_replaying_pending_review(self):
        child, replacement = self.legacy_child()
        child._agent_history = child._messages + [
            {"role": "assistant", "content": "Inspecting.",
             "reasoning_items": [{"id": "opaque-test", "encrypted_content": "opaque-preserved"}],
             "tool_calls": [{"id": "inspect-1", "type": "function", "function": {
                 "name": "houlm_inspect", "arguments": '{"path":"/obj/test"}'}}]},
            {"role": "tool", "tool_call_id": "inspect-1", "content": '{"nodes":[]}'}]
        child._pending_review = {"source": "raise AssertionError('must not run')"}
        child._pending_prompt = "Continue building."
        self.begin()
        self.host.destroy_child()
        self.finish()
        snapshot = replacement._import_legacy_conversation.call_args.args[0]
        self.assertEqual(snapshot["messages"], child._agent_history)
        self.assertEqual(snapshot["turn_state"], "interrupted")
        self.assertEqual(snapshot["draft"], "Continue building.")
        self.assertNotIn("pending_review", snapshot)
        replacement._complete_review.assert_not_called()
        replacement._send.assert_not_called()

    def test_incomplete_agent_batch_falls_back_to_committed_history_and_keeps_transcript(self):
        child, replacement = self.legacy_child()
        child._agent_history = child._messages + [{"role": "assistant", "content": "",
            "tool_calls": [{"id": "unfinished", "type": "function", "function": {
                "name": "houlm_inspect", "arguments": "{}"}}]}]
        self.begin()
        self.host.destroy_child()
        self.finish()
        snapshot = replacement._import_legacy_conversation.call_args.args[0]
        self.assertEqual(snapshot["messages"], child._messages)
        self.assertEqual(snapshot["transcript"], child.transcript._parts)
        self.assertEqual(snapshot["turn_state"], "interrupted")

    def test_existing_history_panel_and_empty_legacy_panel_do_not_import(self):
        second = FakeHost()
        self.use_hosts(self.host, second)
        unused, replacement = self.legacy_child(second, messages=[], transcript=[])
        self.begin()
        self.host.destroy_child()
        second.destroy_child()
        self.finish()
        replacement._import_legacy_conversation.assert_not_called()
        self.assertEqual(self.hou._houlm_legacy_reload_snapshots, {})

    def test_unserializable_legacy_chat_stops_before_any_widget_is_retired(self):
        second = FakeHost()
        self.use_hosts(self.host, second)
        child, replacement = self.legacy_child(second)
        child._messages[0]["content"] = object()
        self.begin()
        self.host.retire_assistant.assert_not_called()
        second.retire_assistant.assert_not_called()
        self.reload_module.assert_not_called()
        replacement._import_legacy_conversation.assert_not_called()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)
        self.assertIn("current chat could not be preserved", self.stdout.getvalue())
        self.assertEqual(len(child._messages), 2)

    def test_orphaned_committed_tool_result_stops_reload_without_losing_chat(self):
        self.legacy_child(messages=[{"role": "tool", "tool_call_id": "orphan", "content": "result"}])
        self.begin()
        self.host.retire_assistant.assert_not_called()
        self.assertFalse(self.hou.session._hou_lm_reload_pending)

    def test_failed_import_keeps_snapshot_for_next_reload_attempt(self):
        unused, replacement = self.legacy_child()
        replacement._import_legacy_conversation.side_effect = RuntimeError("private-import-error")
        self.begin()
        self.host.destroy_child()
        self.finish()
        self.assertEqual(len(self.hou._houlm_legacy_reload_snapshots), 1)
        self.assertNotIn("private-import-error", self.stdout.getvalue())
        replacement._import_legacy_conversation.side_effect = None
        self.begin()
        self.host.destroy_child()
        self.finish()
        self.assertEqual(replacement._import_legacy_conversation.call_count, 2)
        self.assertEqual(self.hou._houlm_legacy_reload_snapshots, {})

    def test_failed_import_backup_cannot_be_attached_to_a_different_hip(self):
        hip = self.enable_hip_events()
        unused, replacement = self.legacy_child()
        replacement._import_legacy_conversation.side_effect = RuntimeError("import failure")
        self.begin()
        self.host.destroy_child()
        self.finish()
        backup = self.hou._houlm_legacy_reload_snapshots[str(id(self.host))]
        self.assertEqual(backup["scene"]["path"], "C:/synthetic/first.hip")
        self.host.retire_assistant.reset_mock()
        replacement._import_legacy_conversation.reset_mock()
        hip.filename = "C:/synthetic/other.hip"
        self.begin()
        self.host.retire_assistant.assert_not_called()
        replacement._import_legacy_conversation.assert_not_called()
        self.assertIs(self.hou._houlm_legacy_reload_snapshots[str(id(self.host))], backup)
        self.assertFalse(self.hou.session._hou_lm_reload_pending)
        self.assertIn("retained chat belongs to an earlier HIP session", self.stdout.getvalue())

    def test_retained_backup_is_invalidated_by_same_path_load_and_clear(self):
        for event in ("before_load", "before_clear"):
            with self.subTest(event=event):
                self.hou._houlm_legacy_reload_snapshots = {}
                self.hou._houlm_legacy_reload_scene_state = None
                hip = self.enable_hip_events()
                unused, replacement = self.legacy_child()
                replacement._import_legacy_conversation.side_effect = RuntimeError("import failure")
                self.begin()
                self.host.destroy_child()
                self.finish()
                self.assertEqual(len(hip.callbacks), 1)
                for callback in hip.callbacks:
                    callback(event)
                self.host.retire_assistant.reset_mock()
                replacement._import_legacy_conversation.reset_mock()
                self.begin()
                self.host.retire_assistant.assert_not_called()
                replacement._import_legacy_conversation.assert_not_called()
                self.assertEqual(len(self.hou._houlm_legacy_reload_snapshots), 1)

    def test_hip_switch_during_deferred_widget_retirement_keeps_backup_without_importing(self):
        hip = self.enable_hip_events()
        unused, replacement = self.legacy_child()
        self.begin()
        self.host.destroy_child()
        hip.filename = "C:/synthetic/switched.hip"
        self.finish()
        self.host.install_assistant.assert_not_called()
        replacement._import_legacy_conversation.assert_not_called()
        self.assertEqual(len(self.hou._houlm_legacy_reload_snapshots), 1)
        self.assertFalse(self.hou.session._hou_lm_reload_pending)
        self.assertIn("keep Houdini open", self.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
