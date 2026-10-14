"""Small Qt interactions for node references; no scene execution or cooking."""

import re

from PySide2 import QtCore, QtGui, QtWidgets

from . import node_paths, scene


MAX_COMPLETIONS = 40
MAX_COMPLETION_SCAN = 1024
MAX_DROP_NODES = 32
_TOKEN = re.compile(r"(?:^|[\s(])@([/A-Za-z0-9_.:-]*)$")


def completion_paths(hou, token):
    """Read only one network level, on demand; never traverse the whole HIP."""
    scene.require_main_thread(hou)
    if not isinstance(token, str) or len(token) > 1024:
        return []
    if not token or token == "/":
        return ["/" + root for root in node_paths.ROOTS if hou.node("/" + root) is not None]
    if not token.startswith("/"):
        return []
    parent, _, prefix = token.rpartition("/")
    if not parent:
        return ["/" + root for root in node_paths.ROOTS
                if root.startswith(prefix) and hou.node("/" + root) is not None]
    if not node_paths.valid_path(parent):
        return []
    network = hou.node(parent)
    if network is None:
        return []
    matches = []
    for child in network.children()[:MAX_COMPLETION_SCAN]:
        path = child.path()
        if path.rsplit("/", 1)[-1].startswith(prefix) and node_paths.valid_path(path):
            matches.append(path)
            if len(matches) >= MAX_COMPLETIONS:
                break
    return sorted(matches)


def dropped_paths(hou, mime):
    """Accept only text made entirely of existing node paths, never files."""
    scene.require_main_thread(hou)
    if mime.hasUrls() or mime.hasImage() or not mime.hasText():
        return []
    text = mime.text()
    if len(text) > MAX_DROP_NODES * 1025:
        return []
    paths = text.split()
    if not 1 <= len(paths) <= MAX_DROP_NODES:
        return []
    result = []
    for path in paths:
        path = path[1:] if path.startswith("@") else path
        if not node_paths.valid_path(path) or hou.node(path) is None:
            return []
        if path not in result:
            result.append(path)
    return result


def focus_node(hou, url):
    """Select and frame an existing node in an existing network editor."""
    scene.require_main_thread(hou)
    value = url.toString() if hasattr(url, "toString") else url
    path = node_paths.path_from_url(value)
    if path is None:
        return "This is not a Houdini node link."
    node = hou.node(path)
    if node is None:
        return "Node no longer exists: " + path
    editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if editor is None:
        return "Open a Network Editor to frame " + path
    try:
        editor.setPwd(node.parent())
        editor.setCurrentNode(node)
        node.setSelected(True, clear_all_selected=True)
        pos = node.position()
        bounds = hou.BoundingRect(hou.Vector2(pos[0] - 3, pos[1] - 2),
                                  hou.Vector2(pos[0] + 4, pos[1] + 2))
        editor.setVisibleBounds(bounds)
    except Exception:
        return "Could not frame " + path + " in the current Network Editor."
    return "Selected " + path


class PromptEdit(QtWidgets.QPlainTextEdit):
    submitted = QtCore.Signal()

    def __init__(self, parent=None, *, hou_module=None):
        super(PromptEdit, self).__init__(parent)
        self._hou = hou_module
        self._completion_start = None
        self._completion_model = QtCore.QStringListModel(self)
        self._completer = QtWidgets.QCompleter(self._completion_model, self)
        self._completer.setWidget(self)
        self._completer.setCaseSensitivity(QtCore.Qt.CaseSensitive)
        self._completer.setCompletionMode(QtWidgets.QCompleter.UnfilteredPopupCompletion)
        self._completer.activated[str].connect(self._insert_completion)

    def _refresh_completions(self):
        if self._hou is None:
            return
        cursor = self.textCursor()
        cursor.movePosition(QtGui.QTextCursor.StartOfBlock, QtGui.QTextCursor.KeepAnchor)
        match = _TOKEN.search(cursor.selectedText())
        if match is None:
            self._completer.popup().hide()
            return
        try:
            choices = completion_paths(self._hou, match.group(1))
        except Exception:
            choices = []
        self._completion_start = self.textCursor().position() - len(match.group(1))
        self._completion_model.setStringList(choices)
        if choices:
            rectangle = self.cursorRect()
            rectangle.setWidth(max(260, self._completer.popup().sizeHintForColumn(0) + 24))
            self._completer.complete(rectangle)
        else:
            self._completer.popup().hide()

    def _insert_completion(self, path):
        if self._completion_start is None or not node_paths.valid_path(path):
            return
        cursor = self.textCursor()
        cursor.setPosition(self._completion_start, QtGui.QTextCursor.KeepAnchor)
        cursor.insertText(path)
        self.setTextCursor(cursor)
        self._completer.popup().hide()

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and event.modifiers() & QtCore.Qt.ControlModifier:
            self._completer.popup().hide()
            self.submitted.emit()
            event.accept()
            return
        if self._completer.popup().isVisible() and event.key() in (
                QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Escape, QtCore.Qt.Key_Tab):
            event.ignore()
            return
        super(PromptEdit, self).keyPressEvent(event)
        self._refresh_completions()

    def dragEnterEvent(self, event):
        try:
            paths = dropped_paths(self._hou, event.mimeData()) if self._hou is not None else []
        except Exception:
            paths = []
        if paths:
            event.acceptProposedAction()
        else:
            event.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, event):
        try:
            paths = dropped_paths(self._hou, event.mimeData()) if self._hou is not None else []
        except Exception:
            paths = []
        if not paths:
            event.ignore()
            return
        self.setTextCursor(self.cursorForPosition(event.pos()))
        self.insertPlainText(" ".join("@" + path for path in paths) + " ")
        event.acceptProposedAction()

    def canInsertFromMimeData(self, source):
        # Ordinary text paste remains available; attachments never do.
        return source.hasText() and not source.hasUrls() and not source.hasImage()

    def insertFromMimeData(self, source):
        if self.canInsertFromMimeData(source):
            self.insertPlainText(source.text())
