"""Synthetic images and offscreen Qt; no desktop capture, real model or scene."""
import copy
import json
import unittest
from unittest import mock

import test_panel as support
import test_panel_commands as commands

if support._qt_available:
    from PySide2 import QtCore, QtGui, QtWidgets
    vision, vision_ui, transcript = support.panel.vision, support.panel.vision_ui, support.panel.chat_transcript


@unittest.skipUnless(support._qt_available, support._qt_reason)
class PanelVisionTests(unittest.TestCase):
    setUpClass = classmethod(commands.PanelCommandTests.setUpClass.__func__)
    setUp = commands.PanelCommandTests.setUp
    tearDown = commands.PanelCommandTests.tearDown
    save_preferences = commands.PanelCommandTests.save_preferences
    prepare_connection = commands.PanelCommandTests.prepare_connection
    destroy_widget = commands.PanelCommandTests.destroy_widget
    saved_active = commands.PanelCommandTests.saved_active
    tool_call = staticmethod(support.PanelTests.tool_call)

    def image(self):
        image = QtGui.QImage(100, 60, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#7e2aff"))
        image.setText("private-test-metadata", "must-not-travel")
        return image

    def test_reference_wire_image_archive_manifest_and_removal(self):
        self.widget._attach_image(self.image())
        manifest = self.widget._reference_images[0]
        history = [{"role": "user", "content": "Describe this", "attachments": [manifest]}]
        original = copy.deepcopy(history)
        payload = self.widget._build_payload("synthetic-model", history, agent=False)
        self.assertEqual(history, original)
        self.assertTrue(any(part.get("type") == "image_url" for part in payload["messages"][-1]["content"]))
        saved = self.saved_active()
        self.assertEqual(saved["draft_attachments"], [manifest])
        self.assertNotIn("base64", json.dumps(saved))
        self.assertNotIn("must-not-travel", self.widget._asset_store.read(manifest).decode("latin1"))
        self.widget._remove_reference(manifest["id"])
        self.assertEqual(self.saved_active()["draft_attachments"], [])

    def test_capture_requires_permission_is_bounded_and_duplicate_id_is_not_replayed(self):
        call = self.tool_call("capture_0", "houlm_capture_viewport", {"reason": "Compare layout"})
        with mock.patch.object(vision_ui, "capture_viewport") as capture:
            result = self.widget._capture_viewport(call)
            self.assertFalse(json.loads(result["content"])["success"])
            capture.assert_not_called()
        self.widget.allow_viewport_capture.setChecked(True)
        data, mime, width, height = vision_ui.normalized_image(self.image())
        with mock.patch.object(vision_ui, "capture_viewport", return_value=(data, mime, width, height,
                {"method": "synthetic_native_viewport", "scene_viewer": "viewer1", "viewport": "persp1",
                 "freshness": "Synthetic displayed image", "fresh_evaluation_requested": False})) as capture:
            self.hou.frame = lambda: 1
            for index in range(1, 4):
                call = self.tool_call("capture_%d" % index, "houlm_capture_viewport", {"reason": "Compare layout"})
                result = self.widget._capture_viewport(call)
                payload = json.loads(result["content"])
                self.assertTrue(payload["success"])
                self.assertEqual(payload["metadata"]["method"], "synthetic_native_viewport")
                self.assertEqual(payload["metadata"]["viewport"], "persp1")
                self.assertNotIn("artist-selected", payload["message"])
                self.assertEqual(self.widget._capture_viewport(call), result)
            fourth = self.tool_call("capture_4", "houlm_capture_viewport", {"reason": "Again"})
            self.assertEqual(json.loads(self.widget._capture_viewport(fourth)["content"])["status"], "limit_reached")
            self.assertEqual(capture.call_count, 3)

    def test_restore_images_never_restores_capture_authorization_and_new_chat_clears_refs(self):
        self.widget._attach_image(self.image())
        self.widget.allow_viewport_capture.setChecked(True)
        self.widget._checkpoint()
        self.assertNotIn("allow_viewport_capture", self.saved_active())
        self.widget._new_chat()
        self.assertEqual(self.widget._reference_images, [])
        self.assertEqual(self.saved_active()["draft_attachments"], [])

    def test_save_as_copies_assets_before_destination_history_is_written(self):
        self.widget._attach_image(self.image())
        manifest = self.widget._reference_images[0]
        old_root = self.widget._asset_store.root
        expected = self.widget._asset_store.read(manifest)
        new_hip = self.folder / "next" / "next.hip"
        new_hip.parent.mkdir()
        self.hou.hipFile.save_as(new_hip)
        self.assertNotEqual(self.widget._asset_store.root, old_root)
        self.assertEqual(self.widget._asset_store.read(manifest), expected)
        self.assertEqual(vision.AssetStore(old_root).read(manifest), expected)
        self.assertEqual(self.saved_active()["draft_attachments"], [manifest])

    def test_unsaved_image_survives_first_save_while_panel_is_closed(self):
        self.hou.hipFile.clear(self.folder / "unsaved.hip")
        self.widget._attach_image(self.image())
        manifest = self.widget._reference_images[0]
        expected = self.widget._asset_store.read(manifest)
        self.assertIsNone(self.widget._asset_store.root)
        self.destroy_widget()
        self.hou.hipFile.save_as(self.folder / "first-save.hip")
        self.widget = support.panel.AssistantPanel()
        self.prepare_connection()
        self.assertEqual(self.widget._reference_images, [manifest])
        self.assertEqual(self.widget._asset_store.read(manifest), expected)
        self.assertIsNotNone(self.widget._asset_store.root)
        self.assertFalse(self.widget.allow_viewport_capture.isChecked())

    def test_missing_old_image_does_not_block_text_history_after_save_as(self):
        self.widget._attach_image(self.image())
        manifest = self.widget._reference_images[0]
        self.widget._messages = [{"role": "user", "content": "Old visual task", "attachments": [manifest]}]
        self.widget._remove_reference(manifest["id"])
        for image_path in self.widget._asset_store.root.glob(manifest["id"] + ".*"):
            image_path.unlink()
        self.hou.hipFile.save_as(self.folder / "different.hip")
        self.assertIn("missing", self.widget._image_archive_warning)
        self.assertEqual(self.saved_active()["messages"][0]["attachments"], [manifest])
        payload = self.widget._build_payload("synthetic-model", self.widget._messages + [{"role": "user", "content": "Answer in text"}], agent=False)
        self.assertEqual(payload["messages"][-1]["content"], "Answer in text")

    def test_transcript_image_resources_are_local_registered_and_collapsed(self):
        data, mime, width, height = vision_ui.normalized_image(self.image())
        manifest = self.widget._asset_store.add(data, mime, width, height, name="<b>reference</b>")
        widget = self.widget.transcript
        loader = mock.Mock(return_value=data)
        widget.set_images([manifest], loader)
        widget.append_image(manifest)
        loader.assert_not_called()
        widget._activate_link(QtCore.QUrl("houlm-image-toggle:///" + manifest["id"]))
        self.widget.show()
        self.app.processEvents()
        self.assertTrue(loader.called)
        loader.reset_mock()
        widget.loadResource(QtGui.QTextDocument.ImageResource, QtCore.QUrl("file:///private.png"))
        widget.loadResource(QtGui.QTextDocument.ImageResource, QtCore.QUrl("https://example.invalid/image.png"))
        loader.assert_not_called()

    def test_image_size_and_metadata_bounded_and_invalid_bytes_rejected(self):
        image = QtGui.QImage(2000, 1500, QtGui.QImage.Format_RGB32)
        image.fill(QtCore.Qt.blue)
        image.setText("private", "discard-this")
        data, mime, width, height = vision_ui.normalized_image(image)
        self.assertLessEqual(max(width, height), 1280)
        self.assertLessEqual(len(data), 512 * 1024)
        decoded = QtGui.QImage.fromData(data)
        self.assertEqual(decoded.text("private"), "")
        with self.assertRaises(ValueError):
            vision_ui.normalized_image(b"not an image")

    def test_viewport_permission_toggle_neither_selects_nor_captures(self):
        self.assertEqual(self.widget.allow_viewport_capture.text(), "Viewport access")
        self.assertFalse(self.widget.allow_viewport_capture.isChecked())
        self.assertFalse(hasattr(self.widget, "choose_view_button"))
        self.assertFalse(hasattr(self.widget, "_capture_binding"))
        with mock.patch.object(vision_ui, "capture_viewport") as capture:
            self.widget.allow_viewport_capture.setChecked(True)
            self.assertTrue(self.widget.allow_viewport_capture.isChecked())
            self.widget.allow_viewport_capture.setChecked(False)
            capture.assert_not_called()

    def test_native_viewport_failure_is_visible_without_a_region_picker_or_attachment(self):
        self.widget.allow_viewport_capture.setChecked(True)
        self.hou.frame = lambda: 1
        call = self.tool_call("ambiguous_view", "houlm_capture_viewport", {"reason": "Compare layout"})
        with mock.patch.object(vision_ui, "capture_viewport", side_effect=ValueError("More than one Scene Viewer is visible.")) as capture:
            result = self.widget._capture_viewport(call)
            payload = json.loads(result["content"])
            self.assertFalse(payload["success"])
            self.assertEqual(payload["status"], "unavailable")
            self.assertIn("More than one Scene Viewer", payload["message"])
            self.assertNotIn("attachments", result)
            self.assertEqual(self.widget._capture_viewport(call), result)
            capture.assert_called_once_with()

    def test_permission_revoked_during_capture_does_not_attach_pixels(self):
        self.widget.allow_viewport_capture.setChecked(True)
        self.hou.frame = lambda: 1
        data, mime, width, height = vision_ui.normalized_image(self.image())
        def revoke():
            self.widget.allow_viewport_capture.setChecked(False)
            return data, mime, width, height, {"method": "synthetic_native_viewport"}
        call = self.tool_call("revoked_view", "houlm_capture_viewport", {"reason": "Compare layout"})
        with mock.patch.object(vision_ui, "capture_viewport", side_effect=revoke):
            result = self.widget._capture_viewport(call)
        self.assertFalse(json.loads(result["content"])["success"])
        self.assertIn("turned off", json.loads(result["content"])["message"])
        self.assertNotIn("attachments", result)

    def test_project_switch_revokes_viewport_access(self):
        self.widget.allow_viewport_capture.setChecked(True)
        self.hou.hipFile.clear(self.folder / "other-project.hip")
        self.assertFalse(self.widget.allow_viewport_capture.isChecked())


if __name__ == "__main__":
    unittest.main()
