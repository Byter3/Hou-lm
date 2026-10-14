"""Real PySide2 child lifetimes; no Houdini GUI, saved credentials or server."""
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

import test_panel as qt_support

if qt_support._qt_available:
    from PySide2 import QtCore, QtWidgets
    import shiboken2
    from houlm import panel_host
    panel = qt_support.panel


@unittest.skipUnless(qt_support._qt_available, qt_support._qt_reason)
class PanelHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.patches = [
            mock.patch.dict(sys.modules, {"houlm.panel": panel}),
            mock.patch.object(panel.core, "load_preferences", return_value={}),
            mock.patch.object(panel.core, "save_preferences"),
            mock.patch.object(panel.credentials, "load_key", return_value=""),
            mock.patch.object(panel.credentials, "save_key"),
            mock.patch.object(panel.credentials, "delete_key"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.host = panel_host.PanelHost()

    def drain_deletions(self):
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.app.processEvents()

    def tearDown(self):
        if shiboken2.isValid(self.host):
            self.host.shutdown()
            self.host.deleteLater()
        self.drain_deletions()

    def test_repeated_reload_reuses_root_and_waits_for_child_destruction(self):
        root_pointer = shiboken2.getCppPointer(self.host)[0]
        for index in range(30):
            child = self.host.assistant_widget()
            child._timer.start(60000)
            old_generation = child._generation
            callback = mock.Mock()
            self.host.retire_assistant(callback)
            callback.assert_not_called()
            self.assertTrue(shiboken2.isValid(child))
            self.assertFalse(child._timer.isActive())
            self.assertGreater(child._generation, old_generation)
            with self.assertRaises(RuntimeError):
                self.host.install_assistant()
            self.drain_deletions()
            callback.assert_called_once_with(True)
            self.assertFalse(shiboken2.isValid(child))
            self.assertIsNone(self.host.assistant_widget())
            self.host.install_assistant()
            self.assertEqual(shiboken2.getCppPointer(self.host)[0], root_pointer)
            self.assertIs(self.host.assistant_widget().parent(), self.host)
            self.assertEqual(self.host.layout().count(), 2)

    def test_retire_aborts_request_and_closes_review_before_deletion(self):
        child = self.host.assistant_widget()
        reply = mock.Mock()
        child._reply = reply
        child._review_dialog = QtWidgets.QDialog(child)
        review = child._review_dialog
        child._pending_review = object()
        child.connection_dialog.show()
        review.show()
        child.api_key.setText("synthetic-ram-key")
        callback = mock.Mock()
        self.host.retire_assistant(callback)
        reply.abort.assert_called_once()
        reply.deleteLater.assert_called_once()
        self.assertIsNone(child._reply)
        self.assertIsNone(child._pending_review)
        self.assertIsNone(child._review_dialog)
        self.assertFalse(review.isVisible())
        self.assertFalse(child.connection_dialog.isVisible())
        self.assertEqual(child.api_key.text(), "")
        self.drain_deletions()
        callback.assert_called_once_with(True)
        self.assertFalse(shiboken2.isValid(review))

    def test_shutdown_during_retirement_cancels_replacement(self):
        callback = mock.Mock()
        self.host.retire_assistant(callback)
        self.host.shutdown()
        self.drain_deletions()
        callback.assert_called_once_with(False)
        with self.assertRaises(RuntimeError):
            self.host.install_assistant()

    def test_shutdown_is_idempotent_and_leaves_native_root_ownership_alone(self):
        child = self.host.assistant_widget()
        with mock.patch.object(child, "disconnect", wraps=child.disconnect) as disconnect:
            self.host.shutdown()
            self.host.shutdown()
            disconnect.assert_called_once()
        self.drain_deletions()
        self.assertTrue(shiboken2.isValid(self.host))
        self.assertTrue(shiboken2.isValid(child))

    def test_closed_or_busy_host_rejects_retirement(self):
        self.host.retire_assistant(mock.Mock())
        with self.assertRaises(RuntimeError):
            self.host.retire_assistant(mock.Mock())
        self.drain_deletions()
        self.host.shutdown()
        with self.assertRaises(RuntimeError):
            self.host.retire_assistant(mock.Mock())

    def test_failed_install_can_be_reported_without_destroying_native_root(self):
        self.host.retire_assistant(mock.Mock())
        self.drain_deletions()
        with mock.patch.object(panel, "create_interface", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                self.host.install_assistant()
        self.host.show_reload_failure()
        self.assertTrue(shiboken2.isValid(self.host))
        self.assertIsNone(self.host.assistant_widget())
        self.assertIn("Restart Houdini", self.host._notice.text())


class PanelDefinitionTests(unittest.TestCase):
    def test_houdini_owns_stable_root_and_destroy_callback_only_shuts_down(self):
        definition = Path(__file__).resolve().parents[1] / "houdini/python_panels/hou_lm.pypanel"
        document = ET.parse(definition)
        script = document.find("interface/script").text
        host = mock.Mock()
        fake_host_module = types.ModuleType("houlm.panel_host")
        fake_host_module.PanelHost = mock.Mock(return_value=host)
        fake_package = types.ModuleType("houlm")
        fake_package.panel_host = fake_host_module
        tab = mock.Mock()
        namespace = {"kwargs": {"paneTab": tab}}
        with mock.patch.dict(sys.modules, {
                "hou": types.ModuleType("hou"), "houlm": fake_package,
                "houlm.panel_host": fake_host_module}):
            exec(compile(script, str(definition), "exec"), namespace)
            self.assertIs(namespace["onCreateInterface"](), host)
            tab.setLabel.assert_called_once_with("Hou-lm Assistant")
            tab.showToolbar.assert_called_once_with(False)
            namespace["onDestroyInterface"]()
            namespace["onDestroyInterface"]()
        host.shutdown.assert_called_once()
        host.close.assert_not_called()
        host.deleteLater.assert_not_called()


if __name__ == "__main__":
    unittest.main()
