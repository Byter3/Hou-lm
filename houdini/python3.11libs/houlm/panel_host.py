"""Stable Houdini-owned root. Reload only its disposable assistant child.

This module is intentionally excluded from live module reload. Houdini owns
the root widget's lifetime; Qt owns destruction of retired child widgets.
"""
from PySide2 import QtCore, QtWidgets


class PanelHost(QtWidgets.QWidget):
    _houlm_host_version = 1

    def __init__(self, parent=None):
        super(PanelHost, self).__init__(parent)
        self._assistant = None
        self._closed = False
        self._retiring = False
        self._retired_callback = None
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._notice = QtWidgets.QLabel("Loading Hou-lm…")
        self._notice.setWordWrap(True)
        self._notice.setTextFormat(QtCore.Qt.PlainText)
        self._layout.addWidget(self._notice)
        self.install_assistant()

    def assistant_widget(self):
        return self._assistant

    def install_assistant(self):
        if self._closed or self._retiring or self._assistant is not None:
            raise RuntimeError("The assistant host is not ready for a new child.")
        from . import panel
        child = panel.create_interface()
        self._assistant = child
        self._layout.addWidget(child)
        self._notice.hide()
        child.show()

    def retire_assistant(self, callback):
        """Call back only once the old QObject has actually been destroyed."""
        if self._closed or self._retiring:
            raise RuntimeError("The assistant host is closing or already reloading.")
        self._retiring = True
        self._retired_callback = callback
        child = self._assistant
        self._notice.setText("Reloading Hou-lm…")
        self._notice.show()
        if child is None:
            self._child_destroyed()
            return
        child.disconnect()
        child.connection_dialog.close()
        self._layout.removeWidget(child)
        child.hide()
        child.destroyed.connect(self._child_destroyed)
        child.deleteLater()

    def _child_destroyed(self, *unused):
        self._assistant = None
        self._retiring = False
        callback, self._retired_callback = self._retired_callback, None
        if callback is not None:
            callback(not self._closed)

    def show_reload_failure(self):
        if not self._closed:
            self._notice.setText("The assistant update could not load. Restart Houdini to load it cleanly.")
            self._notice.show()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        if self._assistant is not None:
            try:
                self._assistant.disconnect()
                self._assistant.connection_dialog.close()
            except RuntimeError:
                pass  # The containing native pane may already be destroying it.

    def closeEvent(self, event):
        self.shutdown()
        super(PanelHost, self).closeEvent(event)
