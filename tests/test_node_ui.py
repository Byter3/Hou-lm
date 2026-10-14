"""Synthetic Qt/node references; no real Houdini session or network access."""

import types
import unittest
from unittest import mock

import test_panel as bootstrap

if bootstrap._qt_available:
    from PySide2 import QtCore, QtGui, QtWidgets
    from houlm import node_ui


@unittest.skipUnless(bootstrap._qt_available, bootstrap._qt_reason)
class NodeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.nodes = {}
        self.reads = []
        self.hou = types.SimpleNamespace(node=self.nodes.get)
        self.nodes["/obj"] = self.make_node("/obj")
        self.nodes["/obj/geo1"] = self.make_node("/obj/geo1")
        self.nodes["/obj/geo2"] = self.make_node("/obj/geo2")
        self.nodes["/obj"].children = lambda: self.children("/obj")

    def make_node(self, path):
        return types.SimpleNamespace(path=lambda: path, children=lambda: (),
                                     allSubChildren=mock.Mock(side_effect=AssertionError("No deep traversal")))

    def children(self, path):
        self.reads.append(path)
        return [node for name, node in self.nodes.items() if name.rpartition("/")[0] == path]

    def test_completion_reads_only_requested_network(self):
        self.assertEqual(node_ui.completion_paths(self.hou, "/obj/g"), ["/obj/geo1", "/obj/geo2"])
        self.assertEqual(self.reads, ["/obj"])
        self.assertEqual(node_ui.completion_paths(self.hou, "/o"), ["/obj"])
        self.assertEqual(node_ui.completion_paths(self.hou, "../"), [])

    def test_text_node_drop_and_reject_attachments(self):
        mime = QtCore.QMimeData()
        mime.setText("/obj/geo1 /obj/geo2 /obj/geo1")
        self.assertEqual(node_ui.dropped_paths(self.hou, mime), ["/obj/geo1", "/obj/geo2"])
        mime.setText("/obj/missing")
        self.assertEqual(node_ui.dropped_paths(self.hou, mime), [])
        mime.setText("/obj/geo1")
        mime.setUrls([QtCore.QUrl.fromLocalFile("C:/secret.png")])
        self.assertEqual(node_ui.dropped_paths(self.hou, mime), [])

    def test_completion_replaces_only_path_and_ctrl_enter_still_submits(self):
        widget = node_ui.PromptEdit(hou_module=self.hou)
        try:
            widget.setPlainText("Explain @/obj/g")
            cursor = widget.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            widget.setTextCursor(cursor)
            widget._refresh_completions()
            widget._insert_completion("/obj/geo1")
            self.assertEqual(widget.toPlainText(), "Explain @/obj/geo1")
            submitted = []
            widget.submitted.connect(lambda: submitted.append(True))
            event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, QtCore.Qt.ControlModifier)
            widget.keyPressEvent(event)
            self.assertEqual(submitted, [True])
        finally:
            widget.close()
            widget.deleteLater()

    def test_paste_is_plain_text_and_file_image_pastes_are_blocked(self):
        widget = node_ui.PromptEdit(hou_module=self.hou)
        try:
            mime = QtCore.QMimeData()
            mime.setText("<script>nothing()</script>")
            mime.setHtml("<img src='file:///secret'>")
            widget.insertFromMimeData(mime)
            self.assertEqual(widget.toPlainText(), "<script>nothing()</script>")
            mime.setImageData(QtGui.QImage(1, 1, QtGui.QImage.Format_RGB32))
            self.assertFalse(widget.canInsertFromMimeData(mime))
        finally:
            widget.close()
            widget.deleteLater()

    def test_drop_inserts_references_without_resolving_or_evaluating_content(self):
        widget = node_ui.PromptEdit(hou_module=self.hou)
        try:
            mime = QtCore.QMimeData()
            mime.setText("/obj/geo1 /obj/geo2")
            event = QtGui.QDropEvent(QtCore.QPointF(0, 0), QtCore.Qt.CopyAction, mime,
                                     QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
            widget.dropEvent(event)
            self.assertEqual(widget.toPlainText(), "@/obj/geo1 @/obj/geo2 ")
            self.assertTrue(event.isAccepted())
        finally:
            widget.close()
            widget.deleteLater()

    def test_internal_link_selects_frames_existing_editor_and_never_opens_a_tab(self):
        node = self.nodes["/obj/geo1"]
        node.parent = lambda: self.nodes["/obj"]
        node.position = lambda: (1, 2)
        node.setSelected = mock.Mock()
        editor = mock.Mock(spec=["setPwd", "setCurrentNode", "setVisibleBounds"])
        self.hou.ui = types.SimpleNamespace(paneTabOfType=lambda _: editor)
        self.hou.paneTabType = types.SimpleNamespace(NetworkEditor="network")
        self.hou.Vector2 = lambda x, y: (x, y)
        self.hou.BoundingRect = lambda a, b: (a, b)
        self.assertEqual(node_ui.focus_node(self.hou, "houlm-node:///obj/geo1"), "Selected /obj/geo1")
        node.setSelected.assert_called_once_with(True, clear_all_selected=True)
        editor.setVisibleBounds.assert_called_once()
        self.assertIn("not a Houdini", node_ui.focus_node(self.hou, "file:///obj/geo1"))
        self.assertIn("no longer exists", node_ui.focus_node(self.hou, "houlm-node:///obj/gone"))


if __name__ == "__main__":
    unittest.main()
