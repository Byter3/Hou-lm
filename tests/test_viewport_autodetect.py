"""Automatic Scene Viewer target selection; synthetic HOM and offscreen Qt only."""
import sys
import types
import unittest
from unittest import mock

import test_panel as support

if support._qt_available:
    from PySide2 import QtCore, QtGui, QtWidgets
    from houlm import vision_ui


class SyntheticViewport:
    def __init__(self, geometry=(100, 50, 400, 300), name="selected-perspective"):
        self.bounds = geometry
        self.label = name

    def geometry(self):
        return self.bounds

    def name(self):
        return self.label


class SyntheticViewer:
    def __init__(self, window, proxy, viewport=None, name="scene1", scene_size=(1000, 600)):
        self.window = window
        self.proxy = proxy
        self.viewport = viewport or SyntheticViewport()
        self.label = name
        self.scene_size = scene_size
        self.curViewport = mock.Mock(side_effect=AssertionError("Do not select by mouse position"))
        self.flipbook = mock.Mock(side_effect=AssertionError("No flipbook may run"))

    def type(self):
        return "scene"

    def name(self):
        return self.label

    def qtWindow(self):
        return self.proxy

    def qtParentWindow(self):
        return self.window

    def qtScreenGeometry(self):
        # Include simulated pane chrome. The viewport must stay inside this pane.
        return QtCore.QRect(self.window.mapToGlobal(QtCore.QPoint(150, 60)), QtCore.QSize(850, 550))

    def selectedViewport(self):
        return self.viewport

    def geometry(self):
        return (0, 0, self.scene_size[0], self.scene_size[1])

    def isCurrentTab(self):
        return True


class SyntheticContextTab:
    def __init__(self, viewer):
        self.viewer = viewer

    def type(self):
        return "context"

    def name(self):
        return "context1"

    def sceneViewer(self):
        return self.viewer

    def qtParentWindow(self):
        return self.viewer.qtParentWindow()

    def qtScreenGeometry(self):
        return self.viewer.qtScreenGeometry()

    def isCurrentTab(self):
        return True


def fake_hou(tabs):
    return types.SimpleNamespace(
        paneTabType=types.SimpleNamespace(SceneViewer="scene", ContextViewer="context"),
        ui=types.SimpleNamespace(currentPaneTabs=lambda: tuple(tabs)),
        isUIAvailable=lambda: True,
    )


@unittest.skipUnless(support._qt_available, support._qt_reason)
class ViewportAutodetectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = QtWidgets.QWidget()
        self.window.setGeometry(100, 80, 1200, 800)
        self.proxy = QtWidgets.QWidget(self.window)
        self.proxy.setGeometry(180, 100, 800, 480)
        self.window.show()
        self.proxy.show()
        self.application.processEvents()
        self.viewer = SyntheticViewer(self.window, self.proxy)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

    def discover(self, tabs=None):
        return vision_ui.discover_viewport(fake_hou([self.viewer] if tabs is None else tabs))

    def test_selected_split_viewport_uses_proxy_origin_and_scaled_lower_left_geometry(self):
        target = self.discover()
        self.assertIs(target["window"], self.window)
        self.assertIs(target["viewer"], self.viewer)
        self.assertIs(target["viewport"], self.viewer.viewport)
        self.assertEqual(target["rectangle"], QtCore.QRect(260, 300, 320, 240))
        self.viewer.curViewport.assert_not_called()
        self.viewer.flipbook.assert_not_called()

    def test_scene_viewer_origin_is_not_added_again_to_proxy_origin(self):
        self.viewer.geometry = lambda: (200, 100, 1000, 600)
        target = self.discover()
        self.assertEqual(target["rectangle"], QtCore.QRect(260, 300, 320, 240))

    def test_context_viewer_resolves_its_current_scene_viewer(self):
        tab = SyntheticContextTab(self.viewer)
        target = self.discover([tab])
        self.assertIs(target["viewer"], self.viewer)
        self.assertEqual(target["rectangle"], QtCore.QRect(260, 300, 320, 240))

    def test_context_viewer_without_scene_and_non_scene_panes_never_substitute(self):
        other = types.SimpleNamespace(type=lambda: "network", name=lambda: "network1", isCurrentTab=lambda: True)
        context = SyntheticContextTab(None)
        with self.assertRaises(ValueError):
            self.discover([context, other])

    def test_network_pane_is_ignored_beside_valid_scene_viewer(self):
        other = types.SimpleNamespace(type=lambda: "network", isCurrentTab=lambda: True)
        self.assertIs(self.discover([other, self.viewer])["viewer"], self.viewer)

    def test_new_wrapper_for_same_native_viewport_keeps_signature(self):
        before = self.discover()
        self.viewer.viewport = SyntheticViewport()
        self.assertEqual(self.discover()["signature"], before["signature"])

    def test_hidden_proxy_is_not_selected(self):
        self.proxy.hide()
        with self.assertRaises(ValueError):
            self.discover()

    def test_non_current_scene_tab_is_not_selected(self):
        self.viewer.isCurrentTab = lambda: False
        with self.assertRaises(ValueError):
            self.discover()

    def test_wrong_thread_is_rejected_before_hom(self):
        native = fake_hou([self.viewer])
        native.ui.currentPaneTabs = mock.Mock()
        with mock.patch.object(QtCore.QThread, "currentThread", return_value=object()):
            with self.assertRaisesRegex(ValueError, "UI thread"):
                vision_ui.discover_viewport(native)
        native.ui.currentPaneTabs.assert_not_called()

    def test_no_visible_scene_viewer_fails_explicitly(self):
        with self.assertRaises(ValueError):
            self.discover([])

    def test_multiple_visible_scene_viewers_are_not_chosen_by_order_or_area(self):
        other = SyntheticViewer(self.window, self.proxy, name="scene2")
        for tabs in ([self.viewer, other], [other, self.viewer]):
            with self.subTest(order=[item.name() for item in tabs]):
                with self.assertRaises(ValueError):
                    self.discover(tabs)

    def test_fresh_discovery_tracks_viewport_selection_and_layout_changes(self):
        before = self.discover()
        self.viewer.viewport = SyntheticViewport((0, 0, 250, 600), "left")
        self.proxy.move(190, 110)
        after = self.discover()
        self.assertNotEqual(before["signature"], after["signature"])
        self.assertEqual(after["rectangle"], QtCore.QRect(190, 110, 200, 480))

    def test_floating_window_is_taken_from_the_selected_tab(self):
        floating = QtWidgets.QWidget()
        floating.setGeometry(-1000, 80, 1200, 800)
        proxy = QtWidgets.QWidget(floating)
        proxy.setGeometry(180, 100, 800, 480)
        floating.show()
        proxy.show()
        self.application.processEvents()
        try:
            viewer = SyntheticViewer(floating, proxy)
            target = self.discover([viewer])
            self.assertIs(target["window"], floating)
            self.assertEqual(target["rectangle"], QtCore.QRect(260, 300, 320, 240))
        finally:
            floating.close()
            floating.deleteLater()
            self.application.processEvents()

    def test_hidden_or_minimized_container_cannot_produce_a_capture_target(self):
        self.window.hide()
        with self.assertRaises(ValueError):
            self.discover()
        self.window.showMinimized()
        self.application.processEvents()
        with self.assertRaises(ValueError):
            self.discover()

    def test_missing_selected_viewport_does_not_fall_back_to_mouse_or_first_view(self):
        self.viewer.viewport = None
        with self.assertRaises(ValueError):
            self.discover()
        self.viewer.curViewport.assert_not_called()

    def test_stale_native_viewport_handle_fails_before_pixel_access(self):
        self.viewer.selectedViewport = mock.Mock(side_effect=RuntimeError("Synthetic stale HOM handle"))
        with self.assertRaises(ValueError):
            self.discover()

    def test_viewport_geometry_must_fit_inside_scene_area(self):
        for bounds in ((-1, 0, 200, 200), (900, 0, 200, 200), (0, 500, 200, 200),
                       (0, 0, 0, 200), (0, 0, 200, -1)):
            with self.subTest(bounds=bounds):
                self.viewer.viewport.bounds = bounds
                with self.assertRaises(ValueError):
                    self.discover()

    def test_proxy_geometry_cannot_target_outside_its_pane_or_window(self):
        self.proxy.move(1100, 100)
        with self.assertRaises(ValueError):
            self.discover()

    def test_fractional_scaling_rounds_crop_inward(self):
        self.proxy.resize(803, 481)
        self.viewer.viewport.bounds = (101, 51, 401, 301)
        target = self.discover()
        # ceil/floor inward: [81.103,403.106] x [198.813...,440.115].
        self.assertEqual(target["rectangle"], QtCore.QRect(262, 299, 321, 241))

    def test_native_discovery_does_not_perform_model_or_render_work(self):
        self.viewer.flipbook = mock.Mock(side_effect=AssertionError("render work forbidden"))
        self.viewer.viewport.saveViewToImage = mock.Mock(side_effect=AssertionError("render work forbidden"))
        self.viewer.viewport.draw = mock.Mock(side_effect=AssertionError("drawing forbidden"))
        self.discover()
        self.viewer.flipbook.assert_not_called()
        self.viewer.viewport.saveViewToImage.assert_not_called()
        self.viewer.viewport.draw.assert_not_called()


@unittest.skipUnless(support._qt_available, support._qt_reason)
class ViewportCaptureValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from test_viewport_native import native_fixture
        self.binding, self.native, self.original = native_fixture(100, 80)
        self.native_patch = mock.patch.dict(sys.modules, {"hou": self.native})
        self.native_patch.start()
        self.addCleanup(self.native_patch.stop)

    def test_changed_target_before_capture_prevents_native_export(self):
        changed = dict(self.binding, signature=("top1",))
        with mock.patch.object(vision_ui, "discover_viewport", side_effect=[self.binding, changed]):
            with self.assertRaisesRegex(ValueError, "changed"):
                vision_ui.capture_viewport()
        self.binding["viewer"].flipbook.assert_not_called()

    def test_changed_target_after_capture_discards_native_output(self):
        changed = dict(self.binding, signature=("top1",))
        with mock.patch.object(vision_ui, "discover_viewport", side_effect=[self.binding, self.binding, changed]), \
                mock.patch.object(vision_ui, "normalized_image") as encode:
            with self.assertRaisesRegex(ValueError, "changed"):
                vision_ui.capture_viewport()
        self.binding["viewer"].flipbook.assert_called_once()
        encode.assert_not_called()

    def test_stable_target_returns_native_image_and_viewport_metadata(self):
        with mock.patch.object(vision_ui, "discover_viewport", return_value=self.binding) as discover:
            data, mime, width, height, metadata = vision_ui.capture_viewport()
        self.assertEqual(discover.call_count, 3)
        self.assertEqual((width, height), (100, 80))
        self.assertEqual(metadata["viewport"], "persp1")
        self.assertEqual(metadata["viewer"], "scene1")
        self.assertEqual(metadata["method"], "houdini_native_flipbook")
        self.assertTrue(metadata["fresh_evaluation_requested"])
        decoded = QtGui.QImage.fromData(data)
        self.assertEqual(decoded.pixelColor(1, 1), QtGui.QColor("#f1a044"))
        self.assertEqual(decoded.pixelColor(2, 1), QtGui.QColor("#123456"))
        self.binding["viewer"].flipbook.assert_called_once()


if __name__ == "__main__":
    unittest.main()
