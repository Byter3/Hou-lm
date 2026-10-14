"""Launch must never initialize Houdini 20.5's generic Python Panel browser."""

import builtins
import contextlib
import io
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest import mock


LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "launch_houdini.py"


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.old_path = list(sys.path)
        self.queued = []
        self.main_thread = object()
        self.qt_app = mock.Mock()
        self.qt_app.thread.return_value = self.main_thread
        self.hou = types.ModuleType("hou")
        self.hou.isUIAvailable = mock.Mock(return_value=True)
        self.hou.session = types.SimpleNamespace()
        self.hou.paneTabType = types.SimpleNamespace(
            PythonPanel="python_panel", NetworkEditor="network_editor")
        self.interface = mock.Mock()
        self.interface.name.return_value = "hou_lm"
        self.hou.pypanel = types.SimpleNamespace(
            installFile=mock.Mock(),
            interfaceByName=mock.Mock(return_value=self.interface),
            setMenuInterfaces=mock.Mock())
        self.network = mock.Mock()
        self.network.type.return_value = "network_editor"
        self.tab = self.make_assistant_tab()
        self.desktop = mock.Mock()
        self.desktop.paneTabs.return_value = (self.network,)
        self.hou.ui = types.SimpleNamespace(
            setStatusMessage=mock.Mock(),
            paneTabs=mock.Mock(return_value=(self.network,)),
            paneTabUnderCursor=mock.Mock(return_value=None),
            curDesktop=mock.Mock(return_value=self.desktop))
        self.defer = types.ModuleType("hdefereval")
        self.defer.executeDeferred = self.queued.append
        self.qtcore = types.SimpleNamespace(
            QThread=types.SimpleNamespace(
                currentThread=mock.Mock(return_value=self.main_thread)))
        self.qtwidgets = types.SimpleNamespace(
            QApplication=types.SimpleNamespace(
                instance=mock.Mock(return_value=self.qt_app)))
        pyside = types.ModuleType("PySide2")
        pyside.QtCore = self.qtcore
        pyside.QtWidgets = self.qtwidgets
        self.modules = mock.patch.dict(sys.modules, {
            "hou": self.hou, "hdefereval": self.defer, "PySide2": pyside})
        self.modules.start()
        self.stdout = io.StringIO()
        self.output = contextlib.redirect_stdout(self.stdout)
        self.output.__enter__()

    def make_assistant_tab(self, floating=False):
        tab = mock.Mock()
        tab.type.return_value = "python_panel"
        tab.activeInterface.return_value = self.interface
        tab.isFloating.return_value = floating
        return tab

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.modules.stop()
        sys.path[:] = self.old_path

    def assert_no_default_panel_or_preferences(self):
        self.network.pane.assert_not_called()
        self.network.setType.assert_not_called()
        self.desktop.createFloatingPaneTab.assert_not_called()
        self.desktop.createFloatingPanel.assert_not_called()
        self.hou.ui.curDesktop.assert_not_called()
        self.hou.ui.paneTabUnderCursor.assert_not_called()
        self.hou.pypanel.setMenuInterfaces.assert_not_called()

    def test_shell_defers_qt_import_and_registration(self):
        actual_import = builtins.__import__

        def safe_shell_import(name, *args, **kwargs):
            if name.startswith("PySide2") or name.startswith("houlm.panel"):
                self.fail("The Python Shell imported Qt/panel before the UI callback")
            return actual_import(name, *args, **kwargs)

        self.hou.pypanel.interfaceByName.side_effect = (None, self.interface)
        with mock.patch("builtins.__import__", side_effect=safe_shell_import):
            runpy.run_path(str(LAUNCHER), run_name="__main__")
        self.assertEqual(len(self.queued), 1)
        self.hou.pypanel.interfaceByName.assert_not_called()
        self.hou.pypanel.installFile.assert_not_called()
        self.queued.pop(0)()
        self.hou.pypanel.installFile.assert_called_once_with(str(
            LAUNCHER.parents[1] / "houdini" / "python_panels" / "hou_lm.pypanel"))
        self.assert_no_default_panel_or_preferences()

    def test_registered_interface_has_explicit_native_menu_instruction(self):
        namespace = runpy.run_path(str(LAUNCHER))
        self.assertIsNone(namespace["_launch_on_ui_thread"]())
        self.hou.pypanel.installFile.assert_not_called()
        self.hou.ui.setStatusMessage.assert_called_once()
        message = self.hou.ui.setStatusMessage.call_args.args[0]
        self.assertIn("click +", message)
        self.assertIn("New Pane Tab Type > Hou-lm Assistant", message)
        self.assertIn("not Misc > Python Panel", message)
        self.assertIn(message, self.stdout.getvalue())
        self.assert_no_default_panel_or_preferences()

    def test_existing_pane_is_focused_without_reinstall_or_recreation(self):
        self.hou.ui.paneTabs.return_value = (self.network, self.tab)
        namespace = runpy.run_path(str(LAUNCHER))
        namespace["launch"]()
        self.tab.setIsCurrentTab.assert_not_called()
        self.queued.pop(0)()
        self.tab.setIsCurrentTab.assert_called_once()
        self.tab.reloadActiveInterface.assert_not_called()
        self.tab.setActiveInterface.assert_not_called()
        self.tab.close.assert_not_called()
        self.hou.pypanel.installFile.assert_not_called()
        self.hou.pypanel.interfaceByName.assert_not_called()
        self.assert_no_default_panel_or_preferences()

    def test_repeated_launch_does_not_reinstall_registered_interface(self):
        namespace = runpy.run_path(str(LAUNCHER))
        namespace["_launch_on_ui_thread"]()
        namespace["_launch_on_ui_thread"]()
        self.hou.pypanel.installFile.assert_not_called()
        self.assert_no_default_panel_or_preferences()

    def test_prefers_embedded_assistant_over_deliberately_floating_one(self):
        floating = self.make_assistant_tab(floating=True)
        self.hou.ui.paneTabs.return_value = (floating, self.tab)
        namespace = runpy.run_path(str(LAUNCHER))
        self.assertIs(namespace["_launch_on_ui_thread"](), self.tab)
        self.tab.setIsCurrentTab.assert_called_once()
        floating.setIsCurrentTab.assert_not_called()
        floating.close.assert_not_called()

    def test_reuses_existing_native_floating_pane_without_creating_another(self):
        self.tab.isFloating.return_value = True
        self.hou.ui.paneTabs.return_value = (self.tab,)
        namespace = runpy.run_path(str(LAUNCHER))
        self.assertIs(namespace["_launch_on_ui_thread"](), self.tab)
        self.tab.setIsCurrentTab.assert_called_once()
        self.hou.pypanel.installFile.assert_not_called()
        self.assert_no_default_panel_or_preferences()

    def test_unrelated_and_unassigned_python_panels_are_left_alone(self):
        unrelated = self.make_assistant_tab()
        unrelated.activeInterface.return_value = mock.Mock()
        unrelated.activeInterface.return_value.name.return_value = "another_tool"
        unassigned = self.make_assistant_tab()
        unassigned.activeInterface.return_value = None
        self.hou.ui.paneTabs.return_value = (self.network, unrelated, unassigned)
        namespace = runpy.run_path(str(LAUNCHER))
        namespace["_launch_on_ui_thread"]()
        for tab in (unrelated, unassigned):
            tab.setActiveInterface.assert_not_called()
            tab.reloadActiveInterface.assert_not_called()
            tab.setIsCurrentTab.assert_not_called()
            tab.close.assert_not_called()
        self.assert_no_default_panel_or_preferences()

    def test_failed_registration_never_falls_back_to_default_browser(self):
        self.hou.pypanel.interfaceByName.return_value = None
        namespace = runpy.run_path(str(LAUNCHER))
        with self.assertRaisesRegex(RuntimeError, "did not register"):
            namespace["_launch_on_ui_thread"]()
        self.hou.pypanel.installFile.assert_called_once()
        self.hou.ui.setStatusMessage.assert_not_called()
        self.assert_no_default_panel_or_preferences()

    def test_worker_thread_is_rejected_before_touching_native_panels(self):
        namespace = runpy.run_path(str(LAUNCHER))
        self.qtcore.QThread.currentThread.return_value = object()
        with self.assertRaisesRegex(RuntimeError, "UI thread"):
            namespace["_launch_on_ui_thread"]()
        self.hou.ui.paneTabs.assert_not_called()
        self.hou.pypanel.installFile.assert_not_called()

    def test_no_qt_application_is_rejected(self):
        namespace = runpy.run_path(str(LAUNCHER))
        self.qtwidgets.QApplication.instance.return_value = None
        with self.assertRaisesRegex(RuntimeError, "UI thread"):
            namespace["_launch_on_ui_thread"]()
        self.hou.ui.paneTabs.assert_not_called()

    def test_headless_session_is_rejected_before_scheduling(self):
        namespace = runpy.run_path(str(LAUNCHER))
        self.hou.isUIAvailable.return_value = False
        with self.assertRaisesRegex(RuntimeError, "graphical application"):
            namespace["launch"]()
        self.assertEqual(self.queued, [])
        self.hou.pypanel.installFile.assert_not_called()

    def test_launch_returns_immediately_without_waiting_for_ui_callback(self):
        namespace = runpy.run_path(str(LAUNCHER))
        self.assertIsNone(namespace["launch"]())
        self.assertEqual(len(self.queued), 1)
        self.hou.ui.paneTabs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
