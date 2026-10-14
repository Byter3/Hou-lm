"""Houdini 20.5 assistant: central inference and reviewed local scene tools."""

import json
import unicodedata
import copy
import uuid
import weakref
from datetime import datetime, timezone

import hou
from PySide2 import QtCore, QtGui, QtNetwork, QtWidgets

from . import (activity, core, credentials, edits, python_actions, scene, toolchat,
              conversations, context_index, node_ui, change_review, request_context,
              transcript as chat_transcript)


MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MODEL_LIST_TIMEOUT_MS = 180000
RESPONSE_MAX_DURATION_MS = 30 * 60 * 1000
DEFAULT_RESPONSE_WAIT_MINUTES = 10
MAX_AGENT_ROUNDS = 24
MAX_AGENT_TOOL_CALLS = 96
PROPOSAL_TOOLS = {"houlm_propose_edits", "houlm_propose_python"}

STYLES = """
QWidget#assistantRoot, QDialog { background: #171e25; color: #e5edf2; }
QWidget#assistantRoot { background: transparent; }
QLabel { background: transparent; color: #e5e7e9; }
QLabel#brand { font-weight: 600; }
QLabel#brandCaption { color: #a4b0b9; letter-spacing: 1px; }
QLabel#logo { color: #a0dfdf; background: rgba(97, 166, 179, 28); border: 1px solid rgba(160, 213, 226, 55); border-radius: 11px; font-weight: 600; }
QLabel#connectionIndicator { color: #838991; }
QLabel#connectionIndicator[connected="true"] { color: #87c7bb; }
QFrame#headerRule { background: rgba(185, 215, 229, 24); border: none; max-height: 1px; }
QLabel#welcomeTitle { font-weight: 600; color: #f0f1f2; }
QLabel#welcomeSubtitle { color: #858b93; }
QLabel#welcomeEyebrow { color: #86b7c5; letter-spacing: 2px; }
QPushButton { background: rgba(91, 120, 139, 28); color: #dbe7ee; border: 1px solid rgba(175, 208, 224, 40); border-radius: 9px; padding: 8px 12px; }
QPushButton:hover { background: rgba(99, 151, 171, 45); border-color: rgba(163, 220, 230, 80); }
QPushButton:pressed { background: #1c1e22; }
QPushButton:disabled { color: #666b72; background: #1b1d20; border-color: #282b30; }
QPushButton#ghost { border: 1px solid transparent; background: transparent; color: #aeb3ba; padding: 7px 9px; }
QPushButton#ghost:hover { background: rgba(136, 180, 201, 20); color: #f0f1f2; }
QPushButton#suggestion { background: rgba(133, 172, 192, 12); border: 1px solid rgba(168, 205, 224, 31); border-radius: 12px; padding: 0; text-align: left; }
QPushButton#suggestion:hover { background: rgba(114, 174, 192, 28); border-color: rgba(133, 211, 222, 70); }
QLabel#suggestionTitle { font-weight: 600; color: #dce0e3; }
QLabel#suggestionHint { color: #a4b0b9; }
QFrame#composer { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(107, 145, 165, 31), stop:1 rgba(76, 112, 134, 21)); border: 1px solid rgba(161, 199, 220, 62); border-radius: 17px; }
QPlainTextEdit#prompt { background: transparent; color: #e7e9eb; border: none; padding: 0; selection-background-color: #41586a; }
QTextBrowser#transcript { background: transparent; color: #e5e7e9; border: none; padding: 0; selection-background-color: #41586a; }
QPushButton#send { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #87bfc8, stop:1 #5e97b2); border: 1px solid #98ced6; border-radius: 10px; padding: 0; }
QPushButton#send:hover { background: #9ad7df; }
QPushButton#send:disabled { background: rgba(104, 152, 172, 38); border-color: rgba(142, 195, 212, 40); }
QPushButton#connectPrimary { background: #81bbc5; color: #13252c; border-color: #a5dbe0; font-weight: 600; }
QPushButton#connectPrimary:hover { background: #a4dbe0; }
QPushButton#connectPrimary:disabled { background: #2c444e; border-color: #3a5660; color: #788e98; }
QComboBox#models { color: #b6c6d0; background: rgba(17, 28, 38, 65); border: 1px solid rgba(151, 190, 209, 32); border-radius: 7px; padding: 5px 24px 5px 9px; }
QComboBox#models:disabled { color: #646a72; }
QComboBox#models::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView { background: #24272b; color: #e5e7e9; selection-background-color: #414b54; border: 1px solid #454a51; padding: 4px; }
QCheckBox#selectionChip { color: #8b9da8; background: rgba(81, 119, 138, 12); border: 1px solid rgba(157, 194, 215, 24); border-radius: 7px; padding: 4px 6px; spacing: 5px; }
QCheckBox#selectionChip:checked { color: #a0d2dc; background: rgba(92, 166, 182, 24); border-color: rgba(120, 195, 209, 57); }
QCheckBox#selectionChip::indicator { width: 10px; height: 10px; border: 1px solid #657078; border-radius: 3px; background: transparent; }
QCheckBox#selectionChip::indicator:checked { background: #83b8ab; border-color: #83b8ab; }
QLabel#status { color: #929aa3; }
QLabel#contextStatus, QLabel#footnote { color: #a4b0b9; }
QPushButton#historyStatus { color: #aab9c1; background: transparent; border: none; padding: 4px 6px; }
QPushButton#historyStatus[saved="true"] { color: #95d2bf; }
QPushButton#historyStatus:hover { background: rgba(136, 180, 201, 20); }
QPushButton#historyStatus[saveError="true"] { color: #edbe8c; }
QFrame#activityBanner { background: rgba(75, 130, 142, 32); border: 1px solid rgba(124, 194, 205, 75); border-radius: 9px; }
QLabel#activityTitle { color: #b6e2e6; font-weight: 600; }
QLabel#activityDetail { color: #c0cbd1; }
QLabel#activityElapsed { color: #95b0bb; }
QProgressBar#activityProgress { border: none; background: rgba(95, 153, 168, 25); border-radius: 2px; min-height: 3px; max-height: 3px; }
QProgressBar#activityProgress::chunk { background: #80c9d2; border-radius: 2px; }
QPushButton#errorDetails { color: #e2ae87; border: none; background: #30271f; padding: 5px 8px; }
QLineEdit, QSpinBox { color: #e7e9eb; background: rgba(9, 20, 30, 90); border: 1px solid rgba(157, 194, 215, 50); border-radius: 9px; padding: 10px; selection-background-color: #41586a; }
QLineEdit:focus { border-color: #76afbf; }
QLabel#dialogTitle { font-weight: 600; }
QLabel#dialogHint { color: #8b939d; }
QLabel#fieldLabel { color: #b5bdc6; }
QPlainTextEdit#details { background: #1a1c20; border: 1px solid #363b42; border-radius: 6px; color: #d8dde2; font-family: Consolas; padding: 10px; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }
QScrollBar::handle:vertical { background: #3d4248; border-radius: 2px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QPushButton#nodeContext { padding: 5px 20px 5px 8px; }
QPushButton#nodeContext::menu-indicator { subcontrol-position: center right; right: 7px; }
QPushButton#nodeContext[attached="true"] { color: #a0d2dc; border-color: #78c3d1; }
QMenu { background: #242c33; color: #e5edf2; border: 1px solid #4a5b67; padding: 5px; }
QMenu::item { padding: 6px 18px; }
QMenu::item:selected { background: #364d59; }
"""


def _icon(name, color="#aab2ba", size=20):
    """Small vector icons, independent of Houdini's icon search path."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.scale(size / 20.0, size / 20.0)
    painter.setPen(QtGui.QPen(QtGui.QColor(color), 1.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    if name == "plus":
        painter.drawLine(10, 4, 10, 16)
        painter.drawLine(4, 10, 16, 10)
    elif name == "arrow":
        painter.drawLine(10, 15, 10, 5)
        painter.drawLine(5, 10, 10, 5)
        painter.drawLine(10, 5, 15, 10)
    elif name == "settings":
        for y, x in ((5, 7), (10, 13), (15, 8)):
            painter.drawLine(3, y, 17, y)
            painter.setBrush(QtGui.QColor("#141516"))
            painter.drawEllipse(QtCore.QPointF(x, y), 2, 2)
    elif name == "history":
        for y, end in ((5, 17), (10, 17), (15, 13)):
            painter.drawLine(3, y, end, y)
    elif name == "spark":
        path = QtGui.QPainterPath()
        path.moveTo(10, 2)
        for x, y in ((12, 8), (18, 10), (12, 12), (10, 18), (8, 12), (2, 10), (8, 8), (10, 2)):
            path.lineTo(x, y)
        painter.drawPath(path)
    painter.end()
    return QtGui.QIcon(pixmap)


class _StatusLabel(QtWidgets.QLabel):
    updated = QtCore.Signal(str)

    def setText(self, value):
        super(_StatusLabel, self).setText(value)
        self.updated.emit(value)


class _PromptEdit(node_ui.PromptEdit):
    def __init__(self, parent=None):
        super(_PromptEdit, self).__init__(parent, hou_module=hou)


class _ModelCombo(QtWidgets.QComboBox):
    def paintEvent(self, event):
        super(_ModelCombo, self).paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor("#8d969f"), 1.2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        x, y = self.width() - 12, self.height() // 2
        painter.drawLine(x - 3, y - 1, x, y + 2)
        painter.drawLine(x, y + 2, x + 3, y - 1)
        if self.currentIndex() < 0:
            painter.setPen(QtGui.QColor("#777f88"))
            text = self.fontMetrics().elidedText("Connect to load models", QtCore.Qt.ElideRight, self.width() - 31)
            painter.drawText(QtCore.QRect(9, 0, self.width() - 31, self.height()), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)
        painter.end()


class _ActivityBanner(QtWidgets.QFrame):
    """Visible turn state and elapsed time; never represents hidden reasoning."""
    stopped = QtCore.Signal()

    def __init__(self, parent=None):
        super(_ActivityBanner, self).__init__(parent)
        self.setObjectName("activityBanner")
        self._clock = QtCore.QElapsedTimer()
        self._pulse = 0
        self._title = ""
        self._waiting = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        row = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel()
        self.title.setObjectName("activityTitle")
        self.title.setTextFormat(QtCore.Qt.PlainText)
        self.title.setWordWrap(True)
        row.addWidget(self.title, 1)
        self.elapsed = QtWidgets.QLabel()
        self.elapsed.setObjectName("activityElapsed")
        row.addWidget(self.elapsed)
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setObjectName("ghost")
        self.stop_button.setAccessibleName("Stop assistant request")
        self.stop_button.setToolTip("Stop this turn. Changes already applied remain in Houdini.")
        self.stop_button.clicked.connect(self.stopped.emit)
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        self.detail = QtWidgets.QLabel()
        self.detail.setObjectName("activityDetail")
        self.detail.setTextFormat(QtCore.Qt.PlainText)
        self.detail.setWordWrap(True)
        self.detail.setMinimumWidth(0)
        self.detail.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        layout.addWidget(self.detail)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setObjectName("activityProgress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        self.hide()

    def show_stage(self, title, detail, waiting=False):
        if not self._clock.isValid():
            self._clock.start()
        self._title, self._waiting = title, waiting
        self.detail.setText(detail)
        self.progress.setVisible(not waiting)
        self._timer.start()
        self._tick()
        self.show()

    def _tick(self):
        self._pulse = (self._pulse + 1) % 4
        self.title.setText(self._title + ("" if self._waiting else "." * self._pulse))
        seconds = self._clock.elapsed() // 1000 if self._clock.isValid() else 0
        self.elapsed.setText("%d:%02d" % (seconds // 60, seconds % 60))

    def finish(self):
        self._timer.stop()
        self._clock.invalidate()
        self.hide()


_Transcript = chat_transcript.Transcript


class AssistantPanel(QtWidgets.QWidget):
    """All Houdini reads occur on the UI thread; HTTP uses Qt's event loop."""

    def __init__(self, parent=None):
        app = QtWidgets.QApplication.instance()
        if app is None or QtCore.QThread.currentThread() != app.thread():
            raise RuntimeError(
                "Create the Hou-lm panel on Houdini's UI thread using the deferred launcher."
            )
        super(AssistantPanel, self).__init__(parent)
        self._manager = QtNetwork.QNetworkAccessManager(self)
        self._reply = None
        self._response = bytearray()
        self._request_kind = None
        self._connected = False
        self._server = ""
        self._messages = []
        self._pending_messages = None
        self._pending_prompt = ""
        self._generation = 0
        self._agent_history = None
        self._agent_model_id = None
        self._agent_rounds = 0
        self._agent_tool_count = 0
        self._completion_check_used = False
        self._proposal_attempted = False
        self._turn_has_edits = False
        self._recovery_pending = False
        self._recovery_used = False
        self._argument_retry_used = False
        self._empty_response_retry_used = False
        self._response_retry_details = ""
        self._pending_review = None
        self._queued_action = None
        self._review_dialog = None
        self._has_saved_key = False
        self._last_error_details = ""
        self._last_edit_details = ""
        self._details_dialog = None
        self._edit_details_dialog = None
        self._history = None
        self._history_bound = False
        self._restoring_history = False
        self._context = context_index.SceneContext(hou)
        self._resume_note = ""
        self._omitted_messages = 0
        self._compacted_tool_results = 0
        self._context_reduction_announced = False
        self._history_timer = QtCore.QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.setInterval(500)
        self._history_timer.timeout.connect(self._checkpoint)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        self._request_clock = QtCore.QElapsedTimer()
        self._last_response_data_ms = 0
        self._request_bytes = -1
        self._request_uploaded_bytes = -1
        self._response_headers_ms = -1
        self._response_first_byte_ms = -1
        self._request_phase_purpose = ""
        self._request_idle_limit_ms = 0
        self._request_duration_limit_ms = 0
        self._build_ui()
        try:
            preferences = core.load_preferences()
        except (OSError, ValueError):
            preferences = {}
        self.server_url.setText(preferences.get("server_url", ""))
        self._preferred_model = preferences.get("model_id", "")
        self.auto_run_commands.blockSignals(True)
        self.auto_run_commands.setChecked(preferences.get("auto_run_commands") is True)
        self.auto_run_commands.blockSignals(False)
        wait_minutes = preferences.get("response_wait_minutes", DEFAULT_RESPONSE_WAIT_MINUTES)
        if type(wait_minutes) is not int or not 1 <= wait_minutes <= 30:
            wait_minutes = DEFAULT_RESPONSE_WAIT_MINUTES
        self.response_wait_minutes.setValue(wait_minutes)
        self.response_wait_minutes.valueChanged.connect(self._response_wait_changed)
        self._setup_history()
        self.server_url.textEdited.connect(self._server_edited)
        self.api_key.textEdited.connect(self._key_edited)
        self._restore_saved_key()
        if self.api_key.text():
            server, generation = self.server_url.text(), self._generation
            QtCore.QTimer.singleShot(0, lambda: self._auto_connect(server, generation))
        self._update_controls()

    def paintEvent(self, event):
        # Native Qt glass-like layers: painted light beneath translucent surfaces.
        painter = QtGui.QPainter(self)
        gradient = QtGui.QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QtGui.QColor("#292e32"))
        gradient.setColorAt(0.48, QtGui.QColor("#232a30"))
        gradient.setColorAt(1, QtGui.QColor("#20272d"))
        painter.fillRect(self.rect(), gradient)
        for x, y, radius, color in ((0.1, 0.05, 0.85, "#427e8a"), (0.95, 0.5, 0.75, "#3d638d")):
            glow = QtGui.QRadialGradient(self.width() * x, self.height() * y, self.width() * radius)
            tint = QtGui.QColor(color)
            tint.setAlpha(17)
            glow.setColorAt(0, tint)
            tint.setAlpha(0)
            glow.setColorAt(1, tint)
            painter.fillRect(self.rect(), glow)
        sheen = QtGui.QLinearGradient(0, 0, self.width(), self.height() * 0.65)
        sheen.setColorAt(0, QtGui.QColor(255, 255, 255, 11))
        sheen.setColorAt(0.42, QtGui.QColor(255, 255, 255, 5))
        sheen.setColorAt(0.6, QtGui.QColor(255, 255, 255, 0))
        painter.fillRect(self.rect(), sheen)
        painter.end()

    def _build_ui(self):
        self.setWindowTitle("Hou-lm Assistant")
        self.setObjectName("assistantRoot")
        self.setStyleSheet(STYLES)
        self.setMinimumSize(320, 400)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        logo = QtWidgets.QLabel("h")
        logo.setObjectName("logo")
        logo.setAlignment(QtCore.Qt.AlignCenter)
        logo.setFixedSize(30, 30)
        header.addWidget(logo)
        title = QtWidgets.QLabel("Hou-lm")
        title.setObjectName("brand")
        header.addWidget(title)
        header.addStretch(1)
        self.connection_indicator = QtWidgets.QLabel("●")
        self.connection_indicator.setObjectName("connectionIndicator")
        self.connection_indicator.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
        header.addWidget(self.connection_indicator)
        self.new_chat_button = QtWidgets.QPushButton()
        self.new_chat_button.setObjectName("ghost")
        self.new_chat_button.setIcon(_icon("plus"))
        self.new_chat_button.setToolTip("New chat")
        self.new_chat_button.setAccessibleName("New chat")
        self.new_chat_button.setFixedSize(32, 32)
        self.new_chat_button.clicked.connect(self._new_chat)
        header.addWidget(self.new_chat_button)
        self.history_button = QtWidgets.QPushButton()
        self.history_button.setObjectName("ghost")
        self.history_button.setIcon(_icon("history"))
        self.history_button.setFixedSize(30, 32)
        self.history_button.setAccessibleName("Conversation history")
        self.history_button.setToolTip("Conversations and changes saved with this HIP")
        self.history_button.clicked.connect(self._show_history)
        header.addWidget(self.history_button)
        self.connections_button = QtWidgets.QPushButton()
        self.connections_button.setObjectName("ghost")
        self.connections_button.setIcon(_icon("settings"))
        self.connections_button.setToolTip("Connections")
        self.connections_button.setAccessibleName("Connections")
        self.connections_button.setFixedSize(32, 32)
        self.connections_button.clicked.connect(self._show_connection_dialog)
        header.addWidget(self.connections_button)
        layout.addLayout(header)
        rule = QtWidgets.QFrame()
        rule.setObjectName("headerRule")
        rule.setFixedHeight(1)
        layout.addWidget(rule)

        self.conversation_stack = QtWidgets.QStackedWidget()
        self.conversation_stack.setMinimumSize(0, 80)
        self.conversation_stack.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Ignored)
        self.welcome = self._build_welcome()
        self.transcript = _Transcript()
        self.transcript.changed.connect(self._conversation_changed)
        self.transcript.nodeActivated.connect(self._node_link)
        self.conversation_stack.addWidget(self.welcome)
        self.conversation_stack.addWidget(self.transcript)
        layout.addWidget(self.conversation_stack, 1)

        self.activity_banner = _ActivityBanner()
        self.activity_banner.stopped.connect(self._cancel_turn)
        layout.addWidget(self.activity_banner)

        status_row = QtWidgets.QHBoxLayout()
        self.status = _StatusLabel("Connect your studio server to begin.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        status_row.addWidget(self.status, 1)
        self.error_details_button = QtWidgets.QPushButton("Error details")
        self.error_details_button.setObjectName("errorDetails")
        self.error_details_button.clicked.connect(self._show_error_details)
        self.error_details_button.hide()
        status_row.addWidget(self.error_details_button)
        self.edit_details_button = QtWidgets.QPushButton("Edit details")
        self.edit_details_button.setObjectName("errorDetails")
        self.edit_details_button.clicked.connect(self._show_edit_details)
        self.edit_details_button.hide()
        status_row.addWidget(self.edit_details_button)
        layout.addLayout(status_row)

        composer = QtWidgets.QFrame()
        composer.setObjectName("composer")
        composer_layout = QtWidgets.QVBoxLayout(composer)
        composer_layout.setContentsMargins(14, 12, 12, 10)
        composer_layout.setSpacing(10)
        self.prompt = _PromptEdit()
        self.prompt.setObjectName("prompt")
        self.prompt.setPlaceholderText("Ask about your scene… Drop nodes here or type @ to reference them.")
        self.prompt.setMinimumHeight(self.fontMetrics().lineSpacing() * 3)
        self.prompt.setMaximumHeight(self.fontMetrics().lineSpacing() * 5)
        self.prompt.textChanged.connect(self._update_controls)
        self.prompt.submitted.connect(self._send)
        composer_layout.addWidget(self.prompt)
        composer_bottom = QtWidgets.QHBoxLayout()
        self.models = _ModelCombo()
        self.models.setObjectName("models")
        self.models.setMinimumContentsLength(10)
        self.models.setMinimumWidth(90)
        self.models.setMaximumWidth(250)
        self.models.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.models.setToolTip("Models available through your studio account")
        self.models.setPlaceholderText("Choose a model")
        self.models.currentIndexChanged.connect(self._model_changed)
        composer_bottom.addWidget(self.models)
        self.node_context_button = QtWidgets.QPushButton("Nodes")
        self.node_context_button.setObjectName("nodeContext")
        self.node_context_button.setAccessibleName("Node context options")
        self.node_context_menu = QtWidgets.QMenu(self.node_context_button)
        self.node_context_menu.setToolTipsVisible(True)
        self.include_selection = QtWidgets.QAction("Attach selected node details on send", self.node_context_menu)
        self.include_selection.setCheckable(True)
        self.include_selection.setToolTip(
            "Attach a fresh, bounded text snapshot of your selection with each message. "
            "Dropping nodes only inserts their paths; with Scene access enabled, the assistant can inspect them as needed."
        )
        self.node_context_menu.addAction(self.include_selection)
        self.preview_button = self.node_context_menu.addAction("Preview selected node details…")
        self.preview_button.setToolTip("View the selection snapshot locally, without sending it")
        self.preview_button.triggered.connect(self._preview_selection)
        self.node_context_button.setMenu(self.node_context_menu)
        composer_bottom.addWidget(self.node_context_button)
        composer_bottom.addStretch(1)
        self.send_button = QtWidgets.QPushButton()
        self.send_button.setObjectName("send")
        self.send_button.setIcon(_icon("arrow", "#102630"))
        self.send_button.setToolTip("Send message (Ctrl + Enter)")
        self.send_button.setAccessibleName("Send message")
        self.send_button.setFixedSize(35, 35)
        self.send_button.clicked.connect(self._send)
        composer_bottom.addWidget(self.send_button)
        composer_layout.addLayout(composer_bottom)
        layout.addWidget(composer)

        context_row = QtWidgets.QHBoxLayout()
        context_row.setSpacing(6)
        self.scene_tools = QtWidgets.QCheckBox("Scene")
        self.scene_tools.setAccessibleName("Scene access")
        self.scene_tools.setObjectName("selectionChip")
        self.scene_tools.setChecked(True)
        self.scene_tools.setToolTip("Allow this model to inspect and change the open scene. Commands wait for Apply unless Auto-run commands is enabled. No screenshots. Changing access starts a new chat.")
        self.scene_tools.toggled.connect(self._scene_toggled)
        context_row.addWidget(self.scene_tools)
        self.auto_run_commands = QtWidgets.QCheckBox("Auto-run")
        self.auto_run_commands.setAccessibleName("Auto-run commands")
        self.auto_run_commands.setObjectName("selectionChip")
        self.auto_run_commands.setToolTip("Always allow Python and scene edits without Apply popups. Saved on this workstation, not in the HIP or chat. Turn off to review future commands.")
        self.auto_run_commands.toggled.connect(self._auto_run_changed)
        context_row.addWidget(self.auto_run_commands)
        context_row.addStretch(1)
        self._history_status_text = ""
        self.history_status = QtWidgets.QPushButton("Log")
        self.history_status.setObjectName("historyStatus")
        self.history_location_button = self.history_status
        self.history_location_button.clicked.connect(self._show_history_location)
        context_row.addWidget(self.history_location_button)
        layout.addLayout(context_row)
        self.include_selection.toggled.connect(self._context_changed)
        self._context_changed()
        self._build_connection_dialog()

    def _setup_history(self):
        self._history = conversations.Conversations(hou)
        self._restore_history()
        reference = weakref.ref(self)
        def invoke(name):
            def callback():
                widget = reference()
                if widget is not None:
                    getattr(widget, name)()
            return callback
        self._history.listen(invoke("_before_scene_switch"), invoke("_after_scene_switch"),
                             invoke("_checkpoint"), invoke("_history_notice"))
        # The native host may delete the child without closeEvent. No HIP or
        # node callbacks may retain a dead/reloaded QObject.
        history, context = self._history, self._context
        self.destroyed.connect(lambda *unused: (history.close(), context.close()))
        self.transcript.changed.connect(self._schedule_checkpoint)
        self.prompt.textChanged.connect(self._schedule_checkpoint)

    def _schedule_checkpoint(self, *unused):
        if self._history_bound and not self._restoring_history:
            self._history_timer.start()

    def _history_notice(self):
        if self._history is None:
            return
        if self._history.error:
            text = "JSON not saved · " + self._history.error
        elif self._history.saved:
            text = "JSON saved · " + self._history.path.name
        elif self._history.path:
            text = "JSON not saved yet · " + self._history.path.name
        else:
            text = "Unsaved HIP · save the scene to save chat history."
        if self._connected and not self._route_matches():
            text += " · Choose the saved model or start New chat to use this connection."
        if self._omitted_messages or self._compacted_tool_results:
            text += " · Full history retained; older context shortened for this request."
        self._history_status_text = text
        self.history_status.setText("Log !" if self._history.error else "Log")
        self.history_status.setAccessibleName("Chat history: " + text)
        self.history_status.setToolTip(text + ("\n" + str(self._history.path) if self._history.path else "") + "\nClick for the file location and save details.")
        self.history_status.setProperty("saved", self._history.saved and not self._history.error)
        self.history_status.setProperty("saveError", bool(self._history.error))
        self.history_status.style().unpolish(self.history_status)
        self.history_status.style().polish(self.history_status)

    def _show_history_location(self):
        self._checkpoint()
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Chat history file")
        dialog.resize(650, 230)
        layout = QtWidgets.QVBoxLayout(dialog)
        status = QtWidgets.QLabel()
        status.setWordWrap(True)
        status.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(status)
        location = QtWidgets.QLineEdit()
        location.setReadOnly(True)
        location.setAccessibleName("Chat history JSON path")
        layout.addWidget(location)
        hint = QtWidgets.QLabel("Conversations, commands and scene context are saved locally in .houlm beside your HIP. Save As carries the history into the new HIP's JSON file.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QtWidgets.QHBoxLayout()
        copy_path = QtWidgets.QPushButton("Copy path")
        open_folder = QtWidgets.QPushButton("Open folder")
        save_now = QtWidgets.QPushButton("Save now")
        close = QtWidgets.QPushButton("Close")
        for button in (copy_path, open_folder, save_now, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        def refresh():
            self._checkpoint()
            status.setText(self._history_status_text)
            path = self._history.path
            location.setText(str(path) if path else "Save your HIP first to choose its history location.")
            copy_path.setEnabled(path is not None)
            open_folder.setEnabled(path is not None and path.parent.is_dir())
        copy_path.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(str(self._history.path)))
        open_folder.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(self._history.path.parent))) if self._history.path else None)
        save_now.clicked.connect(refresh)
        close.clicked.connect(dialog.accept)
        refresh()
        dialog.exec_()

    def _import_legacy_conversation(self, snapshot):
        """Transfer an older panel's in-memory chat; never replay its requests."""
        data = json.loads(json.dumps(snapshot, ensure_ascii=False))
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            raise ValueError("Invalid legacy conversation")
        if not isinstance(data.get("transcript"), list) or not all(isinstance(p, str) for p in data["transcript"]):
            raise ValueError("Invalid legacy transcript")
        for field in ("draft", "server", "model", "turn_state"):
            if not isinstance(data.get(field), str):
                raise ValueError("Invalid legacy conversation field")
        # Validate before changing the new widget. Existing disk conversations
        # are retained when a pre-history panel carries its own live chat in.
        if self._reply is not None or self._agent_history is not None:
            raise ValueError("Cannot import over an active turn")
        if any(self._history.active.get(field) for field in ("messages", "transcript", "draft")):
            self._history.new(server=data.get("server", ""), model=data.get("model", ""))
        item = self._history.active
        item.update({field: data[field] for field in ("messages", "transcript", "draft", "server", "model", "turn_state")})
        item["scene_access"] = data.get("scene_access") is True
        item["scene_context"] = {}
        item["commands"] = []
        item["actions"] = []
        self._restore_history()
        self._checkpoint()
        self.status.setText("Previous chat recovered. No commands were replayed.")
        self._update_controls()

    def _checkpoint(self):
        self._history_timer.stop()
        if self._history is None or not self._history_bound or self._restoring_history:
            return
        item = self._history.active
        history = self._agent_history if self._agent_history is not None else self._pending_messages
        history = self._messages if history is None else history
        item.update(messages=copy.deepcopy(history), transcript=list(self.transcript._parts),
                    commands=self.transcript.export_commands(),
                    draft=self.prompt.toPlainText() or self._pending_prompt,
                    scene_context=self._context.export(),
                    turn_state="review" if self._pending_review else "working" if self._reply is not None or self._agent_history is not None else "interrupted" if self._resume_note else "idle")
        if self._server and not item.get("server"):
            item["server"] = self._server
        if self.models.currentData() and not item.get("model"):
            item["model"] = self.models.currentData()
        item.setdefault("scene_access", self.scene_tools.isChecked())
        self._history.save()
        self._history_notice()

    def _restore_history(self):
        self._restoring_history = True
        self._history_bound = True
        self._omitted_messages = 0
        self._compacted_tool_results = 0
        self._context_reduction_announced = False
        try:
            item = self._history.active
            self._messages = copy.deepcopy(item.get("messages", []))
            self.transcript._parts = list(item.get("transcript", []))
            self.transcript.set_commands(item.get("commands", []))
            self.prompt.setPlainText(item.get("draft", ""))
            self._context.restore(item.get("scene_context", {}))
            self.scene_tools.blockSignals(True)
            self.scene_tools.setChecked(item.get("scene_access", True))
            self.scene_tools.blockSignals(False)
            self._resume_note = ""
            if any(a.get("status") == "unknown" for a in item.get("actions", [])):
                self._resume_note = ("The previous turn was interrupted. No saved action was replayed. "
                                     "An interrupted action may have changed the scene: inspect current nodes before proposing more changes.")
                self.status.setText(self._resume_note)
            elif item.get("turn_state") == "interrupted":
                self._resume_note = "The previous turn was interrupted. No saved action was replayed. Continue from the saved results; unapplied proposals need a new review."
                self.status.setText(self._resume_note)
            if item.get("model") and item.get("server") == self.server_url.text().rstrip("/"):
                self._preferred_model = item["model"]
                index = self.models.findData(item["model"])
                if index >= 0:
                    self.models.blockSignals(True)
                    self.models.setCurrentIndex(index)
                    self.models.blockSignals(False)
            self._context_changed()
        finally:
            self._restoring_history = False
        self._history_notice()

    def _before_scene_switch(self):
        self._checkpoint()
        self._history_bound = False
        self._abort()
        self._context.restore({})

    def _after_scene_switch(self):
        self._restore_history()
        self._update_controls()

    def _node_link(self, url):
        try:
            self.status.setText(node_ui.focus_node(hou, url))
        except Exception:
            self.status.setText("That node is no longer available in this scene.")

    def _show_history(self):
        self._checkpoint()
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Conversations saved with this HIP")
        dialog.resize(800, 620)
        layout = QtWidgets.QVBoxLayout(dialog)
        hint = QtWidgets.QLabel("History is local to this HIP. Resume restores context and never replays commands.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        listing = QtWidgets.QListWidget()
        listing.setMaximumHeight(155)
        items = list(reversed(self._history.document["conversations"]))
        for item in items:
            first = next((m.get("content", "") for m in item.get("messages", []) if m.get("role") == "user"), "New conversation")
            row = QtWidgets.QListWidgetItem(first.split("\n")[0][:110] + " · " + (item.get("model") or "No model"))
            row.setData(QtCore.Qt.UserRole, item["id"])
            listing.addItem(row)
        layout.addWidget(listing)
        tabs = QtWidgets.QTabWidget()
        transcript = QtWidgets.QPlainTextEdit()
        changes = QtWidgets.QPlainTextEdit()
        for text in (transcript, changes):
            text.setReadOnly(True)
            text.setObjectName("details")
        tabs.addTab(transcript, "Conversation")
        tabs.addTab(changes, "Changes")
        layout.addWidget(tabs, 1)
        resume = QtWidgets.QPushButton("Resume selected conversation")
        layout.addWidget(resume)
        def selected():
            index = listing.currentRow()
            return items[index] if 0 <= index < len(items) else None
        def preview():
            item = selected()
            if item is None:
                return
            transcript.setPlainText(chat_transcript.plain_text(item.get("transcript", []), item.get("commands", [])))
            changes.setPlainText("\n\n".join(
                action.get("status", "unknown").upper() + " · " + action.get("time", "") + "\n"
                + action.get("summary", "") + "\n" + action.get("changes", "")
                + ("\nResult:\n" + json.dumps(action["result"], ensure_ascii=False, indent=2) if "result" in action else "")
                for action in item.get("actions", [])) or "No reviewed actions in this conversation.")
            allowed = not (self._reply is not None or self._agent_history is not None)
            allowed = allowed and (not self._connected or (
                item.get("server", "") in ("", self._server) and self.models.findData(item.get("model")) >= 0))
            resume.setEnabled(allowed)
        def restore():
            item = selected()
            if item is None or not resume.isEnabled():
                return
            self._checkpoint()
            self._abort()
            self._history.select(item["id"])
            self._restore_history()
            index = self.models.findData(item.get("model"))
            if index >= 0:
                self.models.blockSignals(True)
                self.models.setCurrentIndex(index)
                self.models.blockSignals(False)
            self._update_controls()
            self._history.save()
            dialog.accept()
        listing.currentRowChanged.connect(preview)
        resume.clicked.connect(restore)
        listing.setCurrentRow(0)
        dialog.exec_()

    def _build_welcome(self):
        welcome = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(welcome)
        layout.setContentsMargins(18, 12, 18, 18)
        layout.setSpacing(12)
        layout.addStretch(2)
        emblem = QtWidgets.QLabel()
        emblem.setPixmap(_icon("spark", "#8dc9d5", 40).pixmap(40, 40))
        emblem.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(emblem)
        eyebrow = QtWidgets.QLabel("YOUR SCENE. A LITTLE MORE POSSIBLE.")
        eyebrow.setObjectName("welcomeEyebrow")
        eyebrow.setAlignment(QtCore.Qt.AlignCenter)
        eyebrow.setWordWrap(True)
        layout.addWidget(eyebrow)
        title = QtWidgets.QLabel("What are we making?")
        title.setObjectName("welcomeTitle")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)
        subtitle = QtWidgets.QLabel("Understand your scene, untangle VEX, or turn an idea into nodes.")
        subtitle.setObjectName("welcomeSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(12)
        suggestions = (
            ("Explain my selection", "Understand the nodes and how they connect", "Explain the selected nodes, their parameters, and their connections.", True),
            ("Help with VEX", "Find the cause of a wrangle error", "Review the selected wrangle's VEX and help diagnose any errors.", True),
            ("Build something new", "Describe a setup to create", "Help me build a procedural setup in Houdini for ", False),
        )
        self.suggestion_buttons = []
        for title, hint, prompt, selection in suggestions:
            button = QtWidgets.QPushButton()
            button.setObjectName("suggestion")
            button.setMinimumHeight(61)
            content = QtWidgets.QVBoxLayout(button)
            content.setContentsMargins(16, 11, 16, 11)
            content.setSpacing(3)
            for value, name in ((title, "suggestionTitle"), (hint, "suggestionHint")):
                label = QtWidgets.QLabel(value)
                label.setObjectName(name)
                label.setWordWrap(True)
                label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
                content.addWidget(label)
            button.setAccessibleName(title)
            button.clicked.connect(lambda checked=False, text=prompt, context=selection: self._use_suggestion(text, context))
            self.suggestion_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(3)
        scroll = QtWidgets.QScrollArea()
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(welcome)
        return scroll

    def _use_suggestion(self, prompt, selection):
        self.prompt.setPlainText(prompt)
        self.include_selection.setChecked(selection)
        self.prompt.setFocus()
        cursor = self.prompt.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.prompt.setTextCursor(cursor)

    def _conversation_changed(self, has_messages):
        self.conversation_stack.setCurrentWidget(self.transcript if has_messages else self.welcome)

    def _build_connection_dialog(self):
        self.connection_dialog = QtWidgets.QDialog(self)
        self.connection_dialog.setWindowTitle("Studio connection")
        self.connection_dialog.setWindowModality(QtCore.Qt.WindowModal)
        self.connection_dialog.resize(450, 370)
        layout = QtWidgets.QVBoxLayout(self.connection_dialog)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("Connect to your studio")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        hint = QtWidgets.QLabel("Use your Open WebUI server and personal API key.")
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(6)
        self.server_url = QtWidgets.QLineEdit()
        self.server_url.setObjectName("serverUrl")
        self.server_url.setPlaceholderText("http://your-studio-server:8088")
        self.api_key = QtWidgets.QLineEdit()
        self.api_key.setObjectName("apiKey")
        self.api_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key.setPlaceholderText("Open WebUI API key")
        for text, field in (("SERVER URL", self.server_url), ("API KEY", self.api_key)):
            label = QtWidgets.QLabel(text)
            label.setObjectName("fieldLabel")
            layout.addWidget(label)
            layout.addWidget(field)
        note = QtWidgets.QLabel("Stay signed in on this PC. Windows encrypts your key for your account and this server. Sign out removes it.")
        note.setObjectName("dialogHint")
        note.setWordWrap(True)
        layout.addWidget(note)
        wait_row = QtWidgets.QHBoxLayout()
        wait_label = QtWidgets.QLabel("Wait without response data")
        self.response_wait_minutes = QtWidgets.QSpinBox()
        self.response_wait_minutes.setRange(1, 30)
        self.response_wait_minutes.setValue(DEFAULT_RESPONSE_WAIT_MINUTES)
        self.response_wait_minutes.setSuffix(" min")
        self.response_wait_minutes.setAccessibleName("Response inactivity timeout in minutes")
        self.response_wait_minutes.setToolTip(
            "How long to wait for the first data or a pause in the response. "
            "Receiving data restarts this wait. Each model response has a 30-minute total limit. "
            "Saved on this PC; changes apply to the next request."
        )
        wait_label.setBuddy(self.response_wait_minutes)
        wait_row.addWidget(wait_label, 1)
        wait_row.addWidget(self.response_wait_minutes)
        layout.addLayout(wait_row)
        self.connection_status = QtWidgets.QLabel("Enter your connection details to begin.")
        self.connection_status.setObjectName("status")
        self.connection_status.setTextFormat(QtCore.Qt.PlainText)
        self.connection_status.setWordWrap(True)
        layout.addWidget(self.connection_status)
        self.status.updated.connect(self.connection_status.setText)
        layout.addStretch(1)
        actions = QtWidgets.QHBoxLayout()
        self.disconnect_button = QtWidgets.QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self.disconnect)
        actions.addWidget(self.disconnect_button)
        self.signout_button = QtWidgets.QPushButton("Sign out")
        self.signout_button.setObjectName("ghost")
        self.signout_button.clicked.connect(self._sign_out)
        actions.addWidget(self.signout_button)
        actions.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.connection_dialog.hide)
        actions.addWidget(close)
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.setObjectName("connectPrimary")
        self.connect_button.clicked.connect(self._connect)
        actions.addWidget(self.connect_button)
        layout.addLayout(actions)
        self.api_key.returnPressed.connect(self._connect)

    def _show_connection_dialog(self):
        if not self.api_key.text():
            self._restore_saved_key()
        self.connection_dialog.show()
        self.connection_dialog.raise_()
        self.connection_dialog.activateWindow()
        (self.api_key if self.server_url.text() else self.server_url).setFocus()

    def _clear_error_details(self):
        self._last_error_details = ""
        self.error_details_button.hide()
        if self._details_dialog is not None:
            self._details_dialog.close()
            self._details_dialog.deleteLater()
            self._details_dialog = None

    def _show_error_details(self):
        if not self._last_error_details:
            return
        self._show_details_dialog("_details_dialog", "Request details", "Server response", self._last_error_details)

    def _clear_edit_details(self):
        self._last_edit_details = ""
        self.edit_details_button.hide()
        if self._edit_details_dialog is not None:
            self._edit_details_dialog.close()
            self._edit_details_dialog.deleteLater()
            self._edit_details_dialog = None

    def _show_edit_details(self):
        if self._last_edit_details:
            self._show_details_dialog("_edit_details_dialog", "Scene edit details", "Scene edit result", self._last_edit_details)

    def _show_details_dialog(self, attribute, title_text, heading, details):
        previous = getattr(self, attribute)
        if previous is not None:
            previous.close()
            previous.deleteLater()
        dialog = QtWidgets.QDialog(self)
        setattr(self, attribute, dialog)
        dialog.setWindowTitle(title_text)
        dialog.resize(620, 390)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QtWidgets.QLabel(heading)
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        detail = QtWidgets.QPlainTextEdit()
        detail.setObjectName("details")
        detail.setReadOnly(True)
        detail.setPlainText(details)
        layout.addWidget(detail, 1)
        actions = QtWidgets.QHBoxLayout()
        copy = QtWidgets.QPushButton("Copy details")
        copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(detail.toPlainText()))
        actions.addWidget(copy)
        actions.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.hide)
        actions.addWidget(close)
        layout.addLayout(actions)
        dialog.show()
        dialog.raise_()

    def _safe_edit_text(self, value, limit=2000):
        if not isinstance(value, str):
            return ""
        key = self.api_key.text().strip()
        value = value[:limit + len(key)]
        if key:
            value = value.replace(key, "[redacted]")
        value = value[:limit]
        return "".join(character for character in value
                       if character == "\n" or not unicodedata.category(character).startswith("C"))

    def _edit_failure_description(self, result):
        """Format only the local executor's structured fields, never logs."""
        failure = result.get("failed_operation")
        failure = failure if isinstance(failure, dict) else {}
        error = result.get("error")
        error = error if isinstance(error, dict) else {}
        names = {"create_node": "Create node", "set_parameter": "Set parameter",
                 "connect_nodes": "Connect nodes", "set_flags": "Set node flags",
                 "set_position": "Set node position", "add_spare_parameter": "Add spare parameter"}
        operation_name = failure.get("op")
        operation = names.get(operation_name, "Scene edit") if isinstance(operation_name, str) else "Scene edit"
        index = failure.get("index", result.get("failed_operation_index"))
        number = str(index) if type(index) is int and 1 <= index <= 32 else "unknown"
        path = self._safe_edit_text(failure.get("path"), 1024).replace("\n", " ")
        parameter = self._safe_edit_text(failure.get("parameter"), 128).replace("\n", " ")
        code = self._safe_edit_text(error.get("code"), 100).replace("\n", " ")
        message = self._safe_edit_text(error.get("message")) or self._safe_edit_text(result.get("message"))
        if not message:
            message = "The reviewed scene changes could not be completed. Inspect the affected nodes before retrying."
        rollback = result.get("rollback")
        if rollback == "incomplete" or result.get("manual_undo_needed") is True:
            rollback_text = "The local executor could not reverse every change. Inspect the scene and Undo history."
        elif rollback == "reverted_scene_fields":
            rollback_text = "The local executor reversed supported scene changes. Inspect the scene before retrying."
        elif rollback == "not_needed" or result.get("status") in ("invalid", "stale", "not_permitted"):
            rollback_text = "No scene changes were applied by this proposal."
        else:
            rollback_text = "Rollback status is unavailable. Inspect the scene before retrying."
        target = operation + (" " + path if path else "") + (" / " + parameter if parameter else "")
        heading = "Could not apply operation " + number + ": " + target
        diagnostic = ("[" + code + "] " if code else "") + message
        details = [heading, "", diagnostic, "", rollback_text]
        completed = result.get("completed_operations")
        if type(completed) is int and 0 <= completed <= 32:
            details.append("Completed operations before the failure: " + str(completed))
        paths = result.get("changed_paths")
        if isinstance(paths, list):
            visible_paths = [self._safe_edit_text(path, 1024).replace("\n", " ") for path in paths[:32] if isinstance(path, str)]
            if visible_paths:
                details += ["", "Affected paths to inspect:"] + ["- " + path for path in visible_paths]
                if len(paths) > 32:
                    details.append("[Additional paths omitted]")
        schema = result.get("available_schema")
        if isinstance(schema, dict) and isinstance(schema.get("parameters"), list):
            label = "Captured parameter schema"
            if schema.get("captured_before_rollback") is True:
                label += " (before rollback)"
            details += ["", label + ":"]
            for parameter_info in schema["parameters"][:48]:
                if isinstance(parameter_info, dict):
                    name = self._safe_edit_text(parameter_info.get("name"), 128).replace("\n", " ")
                    kind = self._safe_edit_text(parameter_info.get("type"), 64).replace("\n", " ")
                    if name:
                        details.append("- " + name + (" (" + kind + ")" if kind else ""))
            if schema.get("truncated") is True or len(schema["parameters"]) > 48:
                details.append("[Parameter schema truncated]")
        formatted = "\n".join(details)
        if len(formatted) > 12000:
            formatted = formatted[:12000] + "\n[Details truncated]"
        activity = heading + "\n" + diagnostic[:650] + "\n" + rollback_text
        return activity, formatted

    def _context_changed(self, checked=None):
        if self.scene_tools.isChecked():
            text = ("Auto-run enabled. Python and scene edits run without popups."
                    if self.auto_run_commands.isChecked() else "Scene access enabled. Proposed changes wait for your review.")
            if self.include_selection.isChecked():
                text += " Selected node text will also be attached."
        elif self.include_selection.isChecked():
            text = "Scene context: selected node text will be attached when you send."
        else:
            text = "Scene context: not attached. Use Nodes to attach selected node details."
        self.node_context_button.setText("Nodes +" if self.include_selection.isChecked() else "Nodes")
        self.node_context_button.setProperty("attached", self.include_selection.isChecked())
        self.node_context_button.setToolTip(text + "\n\n" + self.include_selection.toolTip())
        self.node_context_button.style().unpolish(self.node_context_button)
        self.node_context_button.style().polish(self.node_context_button)

    def _auto_run_changed(self, enabled):
        try:
            core.save_preferences(self.server_url.text(), self.models.currentData() or self._preferred_model,
                                  auto_run_commands=enabled,
                                  response_wait_minutes=self.response_wait_minutes.value())
        except (OSError, ValueError):
            self.status.setText("Command preference applies for this session, but could not be saved on this PC.")
        self._context_changed()
        if enabled and self._pending_review is not None:
            self._pending_review["automatic"] = True
            self._resolve_review(True, self._generation)

    def _response_wait_changed(self, unused_minutes):
        try:
            core.save_preferences(self.server_url.text(), self.models.currentData() or self._preferred_model,
                                  auto_run_commands=self.auto_run_commands.isChecked(),
                                  response_wait_minutes=self.response_wait_minutes.value())
        except (OSError, ValueError):
            self.status.setText("Response wait applies for this session, but could not be saved on this PC.")

    def _python_failure_description(self, result):
        error = result.get("error")
        error = error if isinstance(error, dict) else {}
        code = self._safe_edit_text(error.get("code"), 100).replace("\n", " ")
        message = self._safe_edit_text(error.get("message")) or self._safe_edit_text(result.get("message"))
        message = message or "The reviewed Houdini Python could not be completed."
        started = result.get("execution_started") is True
        state = ("Python execution started. Changes may remain; no automatic rollback was attempted. "
                 "Inspect the scene and Undo history before another run." if started else
                 "The proposed Python was not executed.")
        heading = "Houdini Python did not complete"
        diagnostic = ("[" + code + "] " if code else "") + message
        details = [heading, "", diagnostic, "", state]
        output = self._safe_edit_text(result.get("stdout"), 6000)
        if output:
            details += ["", "Captured output:", output]
        return heading + "\n" + diagnostic[:650] + "\n" + state, "\n".join(details)

    def _scene_toggled(self, enabled):
        self._new_chat()
        self._context_changed()
        self.status.setText(
            "Scene access enabled. Ask a question or describe a change."
            if enabled else "Scene tools disabled. Started a new text-only chat."
        )

    def _update_controls(self):
        busy = self._reply is not None or self._agent_history is not None
        self.connect_button.setEnabled(not busy and not self._connected)
        self.disconnect_button.setEnabled(busy or self._connected)
        self.signout_button.setEnabled(bool(self.api_key.text()) or self._has_saved_key)
        self.models.setEnabled(self._connected and not busy)
        self.send_button.setEnabled(
            self._connected and not busy and self.models.currentIndex() >= 0
            and bool(self.prompt.toPlainText().strip())
            and self._route_matches()
        )
        self.prompt.setEnabled(not busy)
        self.include_selection.setEnabled(not busy)
        self.auto_run_commands.setEnabled(self.scene_tools.isChecked())
        for suggestion in self.suggestion_buttons:
            suggestion.setEnabled(not busy)
        connection_state = "Review" if self._pending_review else "Working" if busy else "Connected" if self._connected else "Offline"
        self.connection_indicator.setToolTip(connection_state)
        self.connection_indicator.setAccessibleName("Connection: " + connection_state)
        self.connection_indicator.setProperty("connected", self._connected)
        self.connection_indicator.style().unpolish(self.connection_indicator)
        self.connection_indicator.style().polish(self.connection_indicator)
        self.error_details_button.setVisible(bool(self._last_error_details))
        self.edit_details_button.setVisible(bool(self._last_edit_details))
        self.status.setVisible(not busy)
        if not busy:
            self.activity_banner.finish()
        elif self._pending_review:
            self.activity_banner.show_stage("Waiting for your review", "Review the proposal, then Apply or Reject. Nothing in this proposal has run yet.", waiting=True)

    def _cancel_turn(self):
        changed = self._turn_has_edits
        self._checkpoint()
        self._abort()
        message = "Stopped this turn."
        if changed:
            message += " Changes already applied remain in Houdini."
        self.transcript.appendPlainText("Scene tools:\n" + message + "\n")
        self.status.setText(message + " You can send another message.")
        self._update_controls()

    def _route_matches(self):
        if self._history is None or not self._history.active.get("messages"):
            return True
        item = self._history.active
        return item.get("server", "") in ("", self._server) and item.get("model", "") in ("", self.models.currentData())

    def _abort(self):
        self._generation += 1
        self._turn_has_edits = False
        self._recovery_pending = False
        self._recovery_used = False
        self._argument_retry_used = False
        self._empty_response_retry_used = False
        self._response_retry_details = ""
        for pending in (self._pending_review, self._queued_action):
            if isinstance(pending, dict) and pending.get("action"):
                action = pending["action"]
                # A started Python action can pump Qt; cancellation cannot
                # truthfully promise that all of its effects were rolled back.
                action["status"] = "unknown" if action.get("status") == "running" else "cancelled"
                self.transcript.update_command(action["id"], action["status"],
                    "Stopped after starting; inspect the scene." if action["status"] == "unknown" else "Cancelled before execution.")
        # Store the cancellation against the old conversation before callers
        # clear its UI or bind to another HIP. Do not rewrite its message list.
        if self._history is not None and (self._pending_review or self._queued_action):
            self._history.active.update(transcript=list(self.transcript._parts),
                                        commands=self.transcript.export_commands(), turn_state="interrupted")
            self._history.save()
        self._pending_review = None
        self._queued_action = None
        if self._review_dialog is not None:
            self._review_dialog.blockSignals(True)
            self._review_dialog.close()
            self._review_dialog.deleteLater()
            self._review_dialog = None
        self._agent_history = None
        self._agent_model_id = None
        self._agent_rounds = 0
        self._agent_tool_count = 0
        self._completion_check_used = False
        self._proposal_attempted = False
        self._timer.stop()
        self._request_clock.invalidate()
        reply, self._reply = self._reply, None
        self._response = bytearray()
        self._pending_messages = None
        self._pending_prompt = ""
        self.activity_banner.finish()
        if reply is not None:
            reply.abort()
            reply.deleteLater()

    def _invalidate(self, clear_key):
        self._checkpoint()
        self._history_bound = False
        self._abort()
        self._clear_error_details()
        self._clear_edit_details()
        self._connected = False
        self._server = ""
        self.models.blockSignals(True)
        self.models.clear()
        self.models.blockSignals(False)
        self._messages = []
        self._context_changed()
        self.transcript.clear()
        self.prompt.clear()
        if clear_key:
            self.api_key.clear()
        self._update_controls()

    def _server_edited(self, unused_text):
        self._invalidate(clear_key=True)
        self._has_saved_key = False
        self._restore_saved_key()
        self.status.setText("Saved login found for this server. Connect to continue." if self.api_key.text() else "Server changed. Enter its API key, then Connect.")
        self._update_controls()

    def _key_edited(self, unused_text):
        self._invalidate(clear_key=False)
        self.status.setText("API key changed. Connect to load permitted models.")

    def disconnect(self):
        """Clear RAM credentials and callbacks; retain the encrypted login."""
        self._invalidate(clear_key=True)
        self.status.setText("Disconnected. Your saved login is available in Connections.")

    def _restore_saved_key(self):
        try:
            server = core.normalize_server_url(self.server_url.text())
            key = credentials.load_key(server)
        except (OSError, ValueError):
            return
        self._has_saved_key = bool(key)
        if key:
            self.api_key.setText(key)

    def _auto_connect(self, server, generation):
        if generation == self._generation and server == self.server_url.text() and self.api_key.text():
            self._connect()

    def _sign_out(self):
        removed = True
        try:
            credentials.delete_key(core.normalize_server_url(self.server_url.text()))
        except (OSError, ValueError):
            removed = False
        self._has_saved_key = not removed
        self.disconnect()
        self.status.setText("Signed out. Saved login removed." if removed else "Disconnected, but Windows could not remove the saved login. Try Sign out again.")
        self._update_controls()

    def _connect(self):
        if self._reply is not None or self._connected:
            return
        try:
            self._server = core.normalize_server_url(self.server_url.text())
        except ValueError:
            self.status.setText("Enter a valid http:// or https:// Open WebUI URL.")
            return
        key = self.api_key.text().strip()
        if not key or any(ord(character) <= 32 or ord(character) > 126 for character in key):
            self.status.setText("Enter your Open WebUI API key without spaces or line breaks.")
            return
        self.server_url.setText(self._server)
        self.status.setText("Connecting and loading permitted models...")
        self._request("models", "/api/models")

    def _request(self, kind, path, payload=None):
        self._clear_error_details()
        self._request_bytes = 0
        self._request_uploaded_bytes = -1
        self._response_headers_ms = -1
        self._response_first_byte_ms = -1
        self._request_phase_purpose = ("Continuing from %d completed tool calls. " % self._agent_tool_count
                                       if self._agent_tool_count and kind == "agent" else "")
        if kind == "models":
            self.activity_banner.show_stage("Connecting", "Loading the models available through your studio account.")
        else:
            self.activity_banner.show_stage("Waiting for server", self._request_phase_purpose
                                            + "Request queued. Waiting for HTTP response headers.")
        request = QtNetwork.QNetworkRequest(QtCore.QUrl(self._server + path))
        request.setAttribute(
            QtNetwork.QNetworkRequest.RedirectPolicyAttribute,
            QtNetwork.QNetworkRequest.ManualRedirectPolicy,
        )
        request.setRawHeader(b"Authorization", b"Bearer " + self.api_key.text().strip().encode("ascii"))
        request.setRawHeader(b"Accept", b"application/json, text/event-stream")
        self._response = bytearray()
        self._request_kind = kind
        if payload is None:
            reply = self._manager.get(request)
        else:
            request.setHeader(QtNetwork.QNetworkRequest.ContentTypeHeader, "application/json")
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._request_bytes = len(body)
            reply = self._manager.post(request, QtCore.QByteArray(body))
        self._reply = reply
        self._request_clock.start()
        self._last_response_data_ms = 0
        self._request_idle_limit_ms = (MODEL_LIST_TIMEOUT_MS if kind == "models"
                                       else self.response_wait_minutes.value() * 60 * 1000)
        self._request_duration_limit_ms = (MODEL_LIST_TIMEOUT_MS if kind == "models"
                                           else RESPONSE_MAX_DURATION_MS)
        reply.setReadBufferSize(MAX_RESPONSE_BYTES + 1)
        reply.readyRead.connect(lambda: self._read_reply(reply))
        reply.finished.connect(lambda: self._finished(reply))
        # Some test/host adapters expose only the basic reply signals. Metrics
        # remain unknown when phase notifications are unavailable.
        metadata_signal = getattr(reply, "metaDataChanged", None)
        if metadata_signal is not None:
            metadata_signal.connect(lambda: self._response_headers_changed(reply))
        upload_signal = getattr(reply, "uploadProgress", None)
        if upload_signal is not None:
            upload_signal.connect(lambda sent, total: self._request_upload_progress(reply, sent, total))
        self._arm_timeout()
        self._update_controls()

    def _show_request_phase(self):
        if self._reply is None or self._request_kind == "models":
            return
        if self._response_first_byte_ms >= 0:
            self.activity_banner.show_stage("Receiving response", "Data is arriving from the studio server. Scene actions wait for a complete, validated response.")
            return
        if self._response_headers_ms >= 0:
            detail = "HTTP response headers received. Waiting for response data."
        elif self._request_bytes and self._request_uploaded_bytes >= self._request_bytes:
            detail = "Request uploaded. Waiting for HTTP response headers."
        elif self._request_uploaded_bytes > 0:
            detail = "Sending request (%d of %d bytes). Waiting for HTTP response headers." % (self._request_uploaded_bytes, self._request_bytes)
        else:
            detail = "Request queued. Waiting for HTTP response headers."
        self.activity_banner.show_stage("Waiting for server", self._request_phase_purpose + detail)

    def _request_upload_progress(self, reply, sent, unused_total):
        if reply is not self._reply or not self._request_clock.isValid():
            return
        self._request_uploaded_bytes = max(self._request_uploaded_bytes,
                                            min(self._request_bytes, max(0, int(sent))))
        self._show_request_phase()

    def _response_headers_changed(self, reply):
        if reply is not self._reply or not self._request_clock.isValid():
            return
        status = reply.attribute(QtNetwork.QNetworkRequest.HttpStatusCodeAttribute)
        if status is not None and 100 <= int(status) <= 599 and self._response_headers_ms < 0:
            self._response_headers_ms = self._request_clock.elapsed()
        self._show_request_phase()

    def _read_reply(self, reply):
        if reply is not self._reply:
            return
        remaining = MAX_RESPONSE_BYTES + 1 - len(self._response)
        chunk = bytes(reply.read(remaining))
        if chunk and self._response_first_byte_ms < 0:
            self._response_first_byte_ms = self._request_clock.elapsed()
            self._response_headers_changed(reply)
            self._show_request_phase()
        self._response.extend(chunk)
        if chunk:
            self._last_response_data_ms = self._request_clock.elapsed()
            self._arm_timeout()
        if len(self._response) > MAX_RESPONSE_BYTES:
            self._failed("The server response exceeded the 4 MiB limit. Start a shorter chat.")

    def _finished(self, reply):
        if reply is not self._reply:
            return
        self._read_reply(reply)
        if reply is not self._reply:
            return
        status = reply.attribute(QtNetwork.QNetworkRequest.HttpStatusCodeAttribute)
        status = int(status or 0)
        if 300 <= status < 400:
            self._failed("The server redirected the request. Enter the final Open WebUI URL and reconnect.")
            return
        if status < 200 or status >= 300:
            self._last_error_details = core.server_error_details(
                status, bytes(self._response), secrets=(self.api_key.text().strip(),)
            )
            self._failed(core.sanitized_http_error(
                status, bytes(self._response), secrets=(self.api_key.text().strip(),)
            ))
            return
        if reply.error() != QtNetwork.QNetworkReply.NoError:
            self._failed("The connection ended before the complete response arrived. Try again.")
            return
        try:
            if self._request_kind == "models":
                value = core.parse_models(json.loads(bytes(self._response).decode("utf-8")))
            elif self._request_kind == "agent":
                value = toolchat.parse_reply(bytes(self._response))
            else:
                value = core.parse_chat_body(bytes(self._response))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
            diagnostic = core.response_error_details(status, self._request_kind, error, bytes(self._response))
            self._last_error_details = diagnostic["details"]
            if self._retry_model_response(reply, error, diagnostic):
                return
            self._failed(diagnostic["message"])
            return
        self._timer.stop()
        self._request_clock.invalidate()
        self._reply = None
        self._response = bytearray()
        reply.deleteLater()
        if self._request_kind == "models":
            self._models_loaded(value)
        elif self._request_kind == "agent":
            self._agent_reply(value)
        else:
            self._messages = self._pending_messages + [{"role": "assistant", "content": value}]
            self._pending_messages = None
            self._pending_prompt = ""
            self._turn_has_edits = False
            self._resume_note = ""
            self.transcript.appendPlainText("Assistant:\n" + value + "\n")
            self.status.setText("Reply received. Scene access is off; no scene commands ran for this message.")
        self._checkpoint()
        self._update_controls()

    def _retry_model_response(self, reply, error, diagnostic):
        empty = isinstance(error, core.ProtocolError) and error.code == "empty_response"
        if (not isinstance(error, core.ProtocolError) or not (empty or error.code in core.TOOL_ARGUMENT_ERROR_CODES)
                or self._request_kind != "agent" or not self._agent_history
                or not self._connected or not self.scene_tools.isChecked()
                or self._argument_retry_used or self._empty_response_retry_used
                or self._agent_rounds >= MAX_AGENT_ROUNDS
                or (not empty and self._agent_tool_count >= MAX_AGENT_TOOL_CALLS)):
            return False
        self._timer.stop()
        self._request_clock.invalidate()
        self._reply = None
        self._response = bytearray()
        reply.deleteLater()
        self._argument_retry_used = not empty
        self._empty_response_retry_used = empty
        self._response_retry_details = diagnostic["details"]
        generation, retry_code = self._generation, error.code
        if empty:
            self.transcript.appendPlainText("Scene tools:\nThe server returned no answer or commands. Retrying this model step once from the saved results; completed commands are not replayed.\n")
            self.status.setText("Empty model reply. Recovering once from the completed work...")
            self.activity_banner.show_stage("Recovering empty reply", "Keeping completed tool results and requesting the next response once.")
        else:
            self.transcript.appendPlainText("Scene tools:\nRetrying this model step once. No tools from the rejected response were executed.\n")
            self.status.setText("A tool call arrived with invalid arguments. Retrying this step once...")
        self._update_controls()
        options = {"empty_response_retry": True} if empty else {"tool_argument_retry": retry_code}
        QtCore.QTimer.singleShot(0, lambda: self._next_agent_round(generation, **options))
        return True

    def _agent_reply(self, reply):
        """Qt dispatches this on the UI thread, so HOM reads stay in Houdini."""
        if self._agent_history is None:
            return
        calls = reply["tool_calls"]
        assistant_message = {"role": "assistant", "content": reply["content"]}
        # Echo provider-minted continuation state without exposing it in chat.
        if reply.get("reasoning_items"):
            assistant_message["reasoning_items"] = reply["reasoning_items"]
        if not calls:
            if (self._recovery_pending and not self._recovery_used
                    and self._agent_rounds < MAX_AGENT_ROUNDS
                    and self._agent_tool_count < MAX_AGENT_TOOL_CALLS):
                self._agent_history.append(assistant_message)
                self._recovery_pending = False
                self._recovery_used = True
                generation = self._generation
                self.status.setText("The assistant is checking a correction after the failed edit...")
                QtCore.QTimer.singleShot(0, lambda: self._next_agent_round(generation, recovery_reminder=True))
                return
            if (self._agent_tool_count > 0 and not self._proposal_attempted
                    and not self._completion_check_used and not self._turn_has_edits
                    and not self._empty_response_retry_used
                    and self._connected and self.scene_tools.isChecked()
                    and self._agent_rounds < MAX_AGENT_ROUNDS - 1
                    and self._agent_tool_count < MAX_AGENT_TOOL_CALLS):
                # A text-only response after discovery can be a premature handoff.
                # Let the model check the original request once; do not infer edit
                # intent from keywords or force actions for read-only questions.
                self._agent_history.append(assistant_message)
                self._completion_check_used = True
                generation = self._generation
                self.activity_banner.show_stage("Checking task completion", "Checking whether your request needs another step before finishing.")
                self.status.setText("Checking the original request against the inspection results...")
                QtCore.QTimer.singleShot(0, lambda: self._next_agent_round(generation, completion_check=True))
                return
            answer = reply["content"]
            if not answer:
                self._failed("The model finished without an answer. Try a more specific question.")
                return
            self._messages = self._agent_history + [assistant_message]
            self._agent_history = None
            self._agent_model_id = None
            self._pending_messages = None
            self._pending_prompt = ""
            self._turn_has_edits = False
            self._recovery_pending = False
            self._response_retry_details = ""
            self._resume_note = ""
            if not self._agent_tool_count:
                self.transcript.appendPlainText(
                    "Scene tools:\nText reply only. No scene tools were called for this message.\n")
            self.transcript.appendPlainText("Assistant:\n" + answer + "\n")
            self.status.setText(
                "Text reply only. No scene tools were called for this message."
                if not self._agent_tool_count else
                "Reply received after %d scene tool calls. %s" % (self._agent_tool_count,
                    "See Activity for execution results." if self._proposal_attempted else
                    "Inspection only; no changes were proposed or applied."))
            return
        if self._agent_rounds >= MAX_AGENT_ROUNDS:
            self._failed("The scene task reached its %d-round limit. Ask a narrower question." % MAX_AGENT_ROUNDS)
            return
        if self._agent_tool_count + len(calls) > MAX_AGENT_TOOL_CALLS:
            self._failed("The scene task reached its %d-query limit. Ask a narrower question." % MAX_AGENT_TOOL_CALLS)
            return
        if any(call["function"]["name"] in PROPOSAL_TOOLS for call in calls):
            self._proposal_attempted = True
        # This is the model's public explanation, not its private reasoning.
        # Previously it was silently discarded whenever the reply also called tools.
        if reply["content"].strip():
            self.transcript.appendPlainText("Assistant:\n" + reply["content"] + "\n")
        generation = self._generation
        assistant_message["tool_calls"] = calls
        reads = [call for call in calls if call["function"]["name"] not in PROPOSAL_TOOLS]
        self._run_read_calls(assistant_message, calls, reads, [], generation)

    def _run_read_calls(self, assistant_message, calls, reads, results, generation):
        if generation != self._generation or self._agent_history is None:
            return
        if not reads:
            self._prepare_agent_proposal(assistant_message, calls, results, generation)
            return
        current = activity.describe_call(reads[0])
        self.activity_banner.show_stage(current["title"], current["detail"])
        # Yield to Qt so the actual operation is visible before the HOM read.
        QtCore.QTimer.singleShot(0, lambda: self._execute_read_call(assistant_message, calls, reads, results, generation))

    def _execute_read_call(self, assistant_message, calls, reads, results, generation):
        if generation != self._generation or self._agent_history is None:
            return
        try:
            fresh_results = toolchat.execute_calls(hou, reads[:1])
            results.extend(fresh_results)
            for call, result in zip(reads[:1], fresh_results):
                self._context.observe_tool(call, result)
        except Exception:
            self._failed("The requested scene inspection could not be completed safely.")
            return
        self._run_read_calls(assistant_message, calls, reads[1:], results, generation)

    def _prepare_agent_proposal(self, assistant_message, calls, results, generation):
        if generation != self._generation or self._agent_history is None:
            return
        proposals = [call for call in calls if call["function"]["name"] in PROPOSAL_TOOLS]
        if proposals:
            self._recovery_pending = False
        if len(proposals) == 1 and self.scene_tools.isChecked():
            self.activity_banner.show_stage("Checking the proposed command", "Checking the proposed action against the current Houdini scene.")
            is_python = proposals[0]["function"]["name"] == "houlm_propose_python"
            executor = python_actions if is_python else edits
            try:
                plan = executor.prepare(hou, json.loads(proposals[0]["function"]["arguments"]))
            except (edits.EditError, python_actions.PythonActionError) as error:
                failure = {
                    "status": "invalid", "success": False,
                    "message": str(error)[:2000],
                }
                if is_python:
                    failure["execution_started"] = False
                metadata = error.details()
                failure.update({key: metadata[key] for key in ("error", "failed_operation", "available_schema") if key in metadata})
                results.append(self._edit_result(proposals[0], failure))
            except Exception:
                results.append(self._edit_result(proposals[0], {
                    "status": "invalid", "success": False,
                    "execution_started": False,
                    "message": ("The Python proposal could not be prepared. Inspect the installed Houdini API and submit valid source."
                                if is_python else "The edit proposal could not be prepared. Inspect the node types and parameters, then submit valid operations."),
                }))
            else:
                self._show_edit_review(plan, proposals[0], calls, results, assistant_message, generation)
                return
        elif proposals:
            for call in proposals:
                results.append(self._edit_result(call, {
                    "status": "not_permitted", "success": False,
                    "execution_started": False,
                    "message": "Scene actions require scene access and exactly one typed or Python proposal per response. No proposals were executed.",
                }))
        self._finish_agent_calls(assistant_message, calls, results, generation)

    @staticmethod
    def _edit_result(call, result):
        return {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)}

    @staticmethod
    def _command_result_text(result):
        # Show documented local executor output, not arbitrary exception or
        # request-log fields accidentally attached to a result envelope.
        fields = {"status", "success", "execution_started", "authorization", "message",
                  "completed_operations", "changed_paths", "target_paths", "target_paths_truncated",
                  "rollback", "manual_undo_needed", "undo_group", "undo_recorded",
                  "external_side_effects_covered", "result", "stdout", "output_truncated"}
        visible = {key: value for key, value in result.items() if key in fields}
        for field, allowed in (("error", {"code", "message", "code_line", "exception_type", "identifier"}),
                               ("failed_operation", {"index", "op", "path", "parameter"})):
            if isinstance(result.get(field), dict):
                visible[field] = {key: value for key, value in result[field].items() if key in allowed}
        return json.dumps(visible, ensure_ascii=False, indent=2)

    def _show_edit_review(self, plan, call, calls, results, assistant_message, generation):
        is_python = call["function"]["name"] == "houlm_propose_python"
        arguments = json.loads(call["function"]["arguments"])
        before = change_review.snapshot_targets(hou, arguments)
        action = {"id": str(uuid.uuid4()), "time": datetime.now(timezone.utc).isoformat(),
                  "status": "review", "tool": call["function"]["name"], "arguments": arguments,
                  "summary": plan.summary, "before": before}
        if self._history is not None:
            self._history.active.setdefault("actions", []).append(action)
        self._pending_review = {
            "plan": plan, "call": call, "calls": calls, "results": results,
            "assistant": assistant_message, "generation": generation, "is_python": is_python,
            "action": action, "automatic": self.auto_run_commands.isChecked(),
        }
        source = arguments.get("code", "") if is_python else call["function"]["arguments"]
        title = " ".join(arguments.get("summary", plan.summary.split("\n", 1)[0]).split())[:180]
        self.transcript.append_command(action["id"],
            ("Python · " if is_python else "Scene edits · ") + title,
            source, status="pending")
        if self._pending_review["automatic"]:
            self._resolve_review(True, generation)
            return
        self._open_review_dialog(self._pending_review)

    def _open_review_dialog(self, pending):
        self._pending_review = pending
        plan, action = pending["plan"], pending["action"]
        arguments, before = action["arguments"], action["before"]
        is_python, generation = pending["is_python"], pending["generation"]
        dialog = QtWidgets.QDialog(self)
        self._review_dialog = dialog
        dialog.setWindowTitle("Review Houdini Python" if is_python else "Review scene changes")
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.resize(680, 540)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("Review Houdini Python" if is_python else "Review before applying")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        hint = QtWidgets.QLabel(
            "This exact source runs with Houdini's permissions after Apply. Undo groups supported scene actions; "
            "it does not guarantee rollback of Python, files or external effects. Nothing has run yet."
            if is_python else "These changes apply to your open Houdini scene. Nothing has been changed yet.")
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        detail = QtWidgets.QPlainTextEdit()
        detail.setObjectName("details")
        detail.setReadOnly(True)
        detail.setPlainText(change_review.review_text(plan.summary, arguments, before))
        layout.addWidget(detail, 1)
        actions = QtWidgets.QHBoxLayout()
        self.review_reject_button = QtWidgets.QPushButton("Reject")
        self.review_reject_button.clicked.connect(lambda: self._resolve_review(False, generation))
        actions.addWidget(self.review_reject_button)
        actions.addStretch(1)
        self.review_apply_button = QtWidgets.QPushButton("Apply Python" if is_python else "Apply changes")
        self.review_apply_button.setObjectName("connectPrimary")
        self.review_apply_button.clicked.connect(lambda: self._resolve_review(True, generation))
        actions.addWidget(self.review_apply_button)
        layout.addLayout(actions)
        dialog.finished.connect(lambda unused_result: self._resolve_review(False, generation))
        self.status.setText("Review the exact Python source. Apply or Reject to continue." if is_python else
                            "Review the proposed changes. Apply or Reject to continue.")
        self._update_controls()
        self._checkpoint()
        dialog.show()
        dialog.raise_()

    def _resolve_review(self, approved, generation):
        pending = self._pending_review
        if pending is None or generation != self._generation or generation != pending["generation"]:
            return
        self._pending_review = None
        dialog, self._review_dialog = self._review_dialog, None
        if dialog is not None:
            dialog.blockSignals(True)
            dialog.close()
            dialog.deleteLater()
        if approved:
            automatic = pending.get("automatic", False)
            self._queued_action = pending
            self.activity_banner.show_stage(
                ("Running Python automatically" if pending["is_python"] else "Applying changes automatically") if automatic else
                ("Running approved Python" if pending["is_python"] else "Applying approved changes"),
                "Executing in Houdini. Some cooks may pause the interface until they return.")
            self._update_controls()
            self._checkpoint()
            QtCore.QTimer.singleShot(0, lambda: self._complete_review(pending, True, generation))
        else:
            self._complete_review(pending, False, generation)

    def _complete_review(self, pending, approved, generation):
        if generation != self._generation or self._agent_history is None:
            return
        if approved and pending.get("automatic") and not self.auto_run_commands.isChecked():
            self._queued_action = None
            pending["automatic"] = False
            self._open_review_dialog(pending)
            return
        action = pending.get("action")
        if approved:
            if action is not None:
                action["status"] = "running"
                action["authorization"] = "auto_run" if pending.get("automatic") else "review"
                self.transcript.update_command(action["id"], "running")
                self._checkpoint()
            try:
                if pending["is_python"]:
                    result = python_actions.apply(hou, pending["plan"], cancelled=lambda:
                                                  generation != self._generation or not self.scene_tools.isChecked())
                else:
                    result = edits.apply(hou, pending["plan"])
            except Exception:
                result = {"status": "failed", "success": False, "manual_undo_needed": True,
                          "message": "The action could not be completed. Inspect the scene before retrying."}
                if pending["is_python"]:
                    result.update(execution_started=True, rollback="not_attempted")
        else:
            result = {"status": "rejected", "success": False, "execution_started": False,
                      "message": "The artist rejected this proposal. It was not executed. Ask for revised instructions."}
        if approved and pending.get("automatic"):
            result["authorization"] = "auto_run"
            if pending["is_python"] and result.get("success") is True:
                result["message"] = "Python completed with Auto-run enabled. Inspect the intended scene result; Undo covers only operations Houdini records."
        if self._queued_action is pending:
            self._queued_action = None
        if action is not None:
            action["status"] = result.get("status", "unknown")
            action["result"] = copy.deepcopy(result)
            receipt_status = "completed" if result.get("success") is True else (
                result.get("status") if result.get("status") in ("rejected", "cancelled", "unknown") else "failed")
            self.transcript.update_command(action["id"], receipt_status,
                                           self._command_result_text(result))
            if approved and generation == self._generation:
                after = change_review.snapshot_targets(hou, action["arguments"])
                action["after"] = after
                action["changes"] = change_review.format_changes(action["before"], after)
                self._context.invalidate()
            self._checkpoint()
        if generation != self._generation or self._agent_history is None:
            if pending["is_python"] and result.get("execution_started") is True:
                self.status.setText("Houdini Python was interrupted after starting. Changes may remain; inspect the scene and Undo history.")
            return
        if (approved and not pending["is_python"] and result.get("success") is False and result.get("status") == "failed"
                and result.get("rollback") == "reverted_scene_fields"
                and result.get("manual_undo_needed") is False):
            self._recovery_pending = True
        if approved and (result.get("execution_started") is True or result.get("success") is True or result.get("completed_operations", 0) > 0
                         or result.get("manual_undo_needed") is True):
            self._turn_has_edits = True
        pending["results"].append(self._edit_result(pending["call"], result))
        self._finish_agent_calls(pending["assistant"], pending["calls"], pending["results"], generation)
        self._update_controls()

    def _finish_agent_calls(self, assistant_message, calls, results, generation):
        if generation != self._generation or self._agent_history is None:
            return
        by_id = {result["tool_call_id"]: result for result in results}
        results = [by_id[call["id"]] for call in calls]
        self._agent_tool_count += len(calls)
        self._agent_history.append(assistant_message)
        self._agent_history.extend(results)
        # Keep complete read and action batches after later network failures.
        # The full paired history is persisted, never a pending tool call.
        self._messages = list(self._agent_history)
        failure_notes = []
        for call, result in zip(calls, results):
            try:
                data = json.loads(result["content"])
            except (ValueError, TypeError, KeyError, AttributeError):
                data = {}
            if data.get("success") is not True and data.get("status") != "rejected":
                if call["function"]["name"] == "houlm_propose_python":
                    summary, self._last_edit_details = self._python_failure_description(data)
                    failure_notes.append(summary)
                    self.edit_details_button.show()
                elif call["function"]["name"] == "houlm_propose_edits":
                    summary, self._last_edit_details = self._edit_failure_description(data)
                    failure_notes.append(summary)
                    self.edit_details_button.show()
        report = activity.format_batch(calls, results)
        if failure_notes:
            report += "\n\n" + "\n".join(failure_notes)
        self.transcript.appendPlainText("Scene tools:\n" + report + "\n")
        queries = [(call, result) for call, result in zip(calls, results)
                   if call["function"]["name"] not in PROPOSAL_TOOLS]
        if queries:
            identifier = str(uuid.uuid4())
            source = "\n\n".join(call["function"]["name"] + "\n" + call["function"]["arguments"]
                                  for call, result in queries)
            output = "\n\n".join(call["function"]["name"] + "\n" + result["content"]
                                  for call, result in queries)
            failed = any(json.loads(result["content"]).get("error") for call, result in queries)
            self.transcript.append_command(identifier, "Scene queries · %d call%s" %
                                           (len(queries), "s" if len(queries) != 1 else ""), source)
            self.transcript.update_command(identifier, "failed" if failed else "completed", output)
        self.activity_banner.show_stage("Preparing request", "Preparing the completed scene results for the studio server.")
        self.status.setText("Scene results returned. Waiting for the central model...")
        self._checkpoint()
        QtCore.QTimer.singleShot(0, lambda: self._next_agent_round(generation))

    def _turn_budget(self, new_turn=False):
        rounds = 0 if new_turn else self._agent_rounds
        tools = 0 if new_turn else self._agent_tool_count
        remaining = max(0, MAX_AGENT_ROUNDS - rounds)
        return {
            "request_limit": MAX_AGENT_ROUNDS,
            "tool_call_limit": MAX_AGENT_TOOL_CALLS,
            "requests_remaining": remaining,
            # The existing round guard reserves the last response for a final
            # answer; don't advertise tools the client would refuse to run.
            "tool_calls_remaining": max(0, MAX_AGENT_TOOL_CALLS - tools) if remaining > 1 else 0,
        }

    def _next_agent_round(self, generation, recovery_reminder=False, tool_argument_retry=None, completion_check=False, empty_response_retry=False):
        # A queued continuation must never survive New Chat, mode changes,
        # disconnect, or closing the panel; these all increment the generation.
        if generation != self._generation or self._agent_history is None:
            return
        if not self._connected or not self.scene_tools.isChecked():
            return
        if self._agent_rounds >= MAX_AGENT_ROUNDS:
            self._failed("The scene task reached its %d-round limit. Ask a narrower question." % MAX_AGENT_ROUNDS)
            return
        try:
            options = {"turn_budget": self._turn_budget()}
            if recovery_reminder:
                options["recovery_reminder"] = True
            if tool_argument_retry is not None:
                options["tool_argument_retry"] = tool_argument_retry
            if completion_check:
                options["completion_check"] = True
            if empty_response_retry:
                options["empty_response_retry"] = True
            payload = self._build_payload(self._agent_model_id, self._agent_history, agent=True, **options)
        except core.RequestSizeError:
            self._failed("This request still exceeds the local size limit after reducing older context. The full history is retained. Shorten the current prompt or attach less selected-node text.")
            return
        except (ValueError, TypeError):
            self._failed("The conversation could not be prepared because its messages or tool results are invalid or incomplete. The full history is retained; no additional commands were run.")
            return
        self._agent_rounds += 1
        self._request("agent", "/api/chat/completions", payload)
        if completion_check and self._reply is not None:
            self._request_phase_purpose = "Checking task completion. "
            self._show_request_phase()
        elif empty_response_retry and self._reply is not None:
            self._request_phase_purpose = "Recovering once from completed work; no commands are replayed. "
            self._show_request_phase()

    def _failed(self, message):
        self._checkpoint()
        original_prompt = self._pending_prompt
        was_chat = bool(original_prompt)
        if self._response_retry_details:
            final_details = (self._last_error_details if self._last_error_details
                             and self._last_error_details != self._response_retry_details else message)
            self._last_error_details = ("First rejected response (one retry attempted)\n"
                                        + self._response_retry_details + "\n\nFailure after retry\n" + final_details)
        if self._turn_has_edits:
            message += " Changes already applied may remain in Houdini. Inspect the scene before retrying; this error does not undo them."
        self._abort()
        if original_prompt:
            self.prompt.setPlainText(original_prompt)
        if was_chat:
            self.transcript.appendPlainText("[Request failed] " + message + "\n")
        self.status.setText(message)
        self._update_controls()

    def _arm_timeout(self):
        if self._reply is None or not self._request_clock.isValid():
            return
        elapsed = self._request_clock.elapsed()
        remaining = min(self._request_idle_limit_ms - (elapsed - self._last_response_data_ms),
                        self._request_duration_limit_ms - elapsed)
        self._timer.start(max(1, remaining))

    def _timed_out(self):
        if self._reply is None or not self._request_clock.isValid():
            return
        elapsed = self._request_clock.elapsed()
        idle = max(0, elapsed - self._last_response_data_ms)
        if elapsed < self._request_duration_limit_ms and idle < self._request_idle_limit_ms:
            # A queued timer event from an earlier interval/request must not
            # abort an active reply after data reset its inactivity deadline.
            self._arm_timeout()
            return
        total_limit = elapsed >= self._request_duration_limit_ms
        code = "response_deadline_exceeded" if total_limit else "response_idle_timeout"
        status = int(self._reply.attribute(QtNetwork.QNetworkRequest.HttpStatusCodeAttribute) or 0)
        diagnostic = core.response_error_details(status, self._request_kind,
            core.ProtocolError(code, request_elapsed_ms=elapsed, idle_elapsed_ms=idle,
                               idle_limit_ms=self._request_idle_limit_ms,
                               response_limit_ms=self._request_duration_limit_ms,
                               request_bytes=self._request_bytes,
                               request_uploaded_bytes=self._request_uploaded_bytes,
                               response_headers_ms=self._response_headers_ms,
                               response_first_byte_ms=self._response_first_byte_ms), bytes(self._response))
        self._last_error_details = diagnostic["details"]
        if total_limit:
            message = "The response reached the %d-minute total wait limit." % (self._request_duration_limit_ms // 60000)
        elif self._response:
            message = "The response stopped arriving for %d minutes." % (self._request_idle_limit_ms // 60000)
        else:
            message = "No response data arrived for %d minutes." % (self._request_idle_limit_ms // 60000)
        self._failed(message + " See Error details and the server logs. This request was not retried automatically.")

    def _models_loaded(self, models):
        self.models.blockSignals(True)
        self.models.clear()
        for model in models:
            self.models.addItem(model["name"], model["id"])
        preferred = self.models.findData(self._preferred_model)
        if preferred >= 0:
            self.models.setCurrentIndex(preferred)
        elif models:
            self.models.setCurrentIndex(0)
        self.models.blockSignals(False)
        self._connected = True
        item = self._history.active if self._history is not None else {}
        if item.get("messages") and (item.get("server") not in ("", self._server) or item.get("model") not in ("", self.models.currentData())):
            self._history_bound = False
            self._new_chat()
        elif not self._history_bound:
            self._restore_history()
        self.connection_dialog.hide()
        self.status.setText(
            "Connected. Choose a model and ask a question."
            if models else "Connected, but this account has no available models. Check central model access."
        )
        self._save_preferences()
        try:
            credentials.save_key(self._server, self.api_key.text().strip())
            self._has_saved_key = True
        except (OSError, ValueError):
            self.status.setText("Connected for this session. Windows could not save the login on this PC.")
        self._checkpoint()

    def _save_preferences(self):
        self._preferred_model = self.models.currentData() or ""
        try:
            core.save_preferences(self._server, self._preferred_model,
                                  auto_run_commands=self.auto_run_commands.isChecked(),
                                  response_wait_minutes=self.response_wait_minutes.value())
        except (OSError, ValueError):
            self.status.setText("Connected, but could not save the server/model preference.")

    def _model_changed(self, unused_index):
        if self._connected:
            self._new_chat()
            self._save_preferences()

    def _new_chat(self):
        self._checkpoint()
        self._history_bound = False
        self._abort()
        self._clear_error_details()
        self._clear_edit_details()
        self._messages = []
        self.transcript.clear()
        self.prompt.clear()
        self._context.restore({})
        self._resume_note = ""
        self._omitted_messages = 0
        self._compacted_tool_results = 0
        self._context_reduction_announced = False
        if self._history is not None:
            self._history.new(server=self._server, model=self.models.currentData() or "")
            self._history_bound = True
            self._checkpoint()
        self.status.setText("New chat. Previous conversations remain in History.")
        self._update_controls()

    def _selection_text(self):
        snapshot = scene.snapshot_selection(hou)
        return json.dumps(snapshot, ensure_ascii=False, indent=2)

    def _preview_selection(self):
        try:
            preview = self._selection_text()
        except Exception:
            self.status.setText("Could not read the current selection. Select nodes and try again.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Selected node text preview")
        dialog.resize(700, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        explanation = QtWidgets.QLabel("Local preview only. Send captures the current selection again.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        text = QtWidgets.QPlainTextEdit()
        text.setObjectName("details")
        text.setReadOnly(True)
        text.setPlainText(preview)
        layout.addWidget(text)
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec_()

    def _build_payload(self, model_id, messages, *, agent, **options):
        if agent:
            context = self._context.prompt_context()
            options["scene_context"] = context
            options["resume_notice"] = self._resume_note
            options["auto_run_commands"] = self.auto_run_commands.isChecked()
            # The first payload is built before the previous turn's counters
            # are reset. Continuing turns use the full local execution count,
            # independent of which messages fit into the outgoing context.
            options.setdefault("current_turn_tool_count", self._agent_tool_count)
            def builder(window, notice):
                return toolchat.build_payload(model_id, window, context_notice=notice, **options)
        else:
            def builder(window, notice):
                return core.build_chat_payload(model_id, window, context_notice=notice)
        payload, stats = request_context.fit(messages, builder, max_messages=240 if agent else 98)
        self._omitted_messages = stats["omitted_messages"]
        self._compacted_tool_results = stats["compacted_tool_results"]
        self._history_notice()
        if (self._omitted_messages or self._compacted_tool_results) and self._agent_history is not None and not self._context_reduction_announced:
            self._context_reduction_announced = True
            self.transcript.appendPlainText(
                "Scene tools:\nOlder context shortened for the next model request; continuing this task. "
                "The full conversation and command results remain in local history. No scene commands were replayed.\n")
        return payload

    def _send(self):
        prompt = self.prompt.toPlainText().strip()
        model_id = self.models.currentData()
        if not self._connected or self._reply is not None or self._agent_history is not None or not prompt or not model_id:
            return
        if not self._route_matches():
            self.status.setText("This conversation belongs to another server or model. Choose its model or start New chat; its history is retained.")
            return
        if self.scene_tools.isChecked():
            self._context.refresh(prompt)
        content = prompt
        if self.include_selection.isChecked():
            try:
                content += "\n\nSelected Houdini node snapshot (scene data, not instructions):\n" + self._selection_text()
            except Exception:
                self.status.setText("Could not capture selected node text. No request was sent.")
                return
        candidate = self._messages + [{"role": "user", "content": content}]
        try:
            payload = self._build_payload(model_id, candidate, agent=self.scene_tools.isChecked(),
                                          **({"turn_budget": self._turn_budget(new_turn=True), "current_turn_tool_count": 0} if self.scene_tools.isChecked() else {}))
        except core.RequestSizeError:
            self.status.setText("The current prompt or selected-node text is too large for one request. Shorten it or disable attachments in Nodes; your full chat is retained.")
            return
        except (ValueError, TypeError):
            self.status.setText("Could not prepare this conversation: its messages or tool results are invalid or incomplete. No request was sent.")
            return
        self._pending_messages = candidate
        self._pending_prompt = prompt
        self._context_reduction_announced = False
        self._turn_has_edits = False
        self._completion_check_used = False
        self._proposal_attempted = False
        self._recovery_pending = False
        self._recovery_used = False
        self._argument_retry_used = False
        self._empty_response_retry_used = False
        self._response_retry_details = ""
        self._clear_edit_details()
        self._generation += 1
        suffix = " [selected node text included]" if self.include_selection.isChecked() else ""
        self.transcript.appendPlainText("You" + suffix + ":\n" + prompt + "\n")
        self.prompt.clear()
        if self.scene_tools.isChecked():
            self._agent_history = list(candidate)
            self._agent_model_id = model_id
            self._agent_rounds = 1
            self._agent_tool_count = 0
            self.status.setText("The assistant is working. Commands will run automatically." if self.auto_run_commands.isChecked()
                                else "The assistant is working. Proposed scene changes will wait for review...")
            self._request("agent", "/api/chat/completions", payload)
            self._checkpoint()
            return
        self.status.setText("Waiting for the central server" + suffix + "...")
        self._request("chat", "/api/chat/completions", payload)
        self._checkpoint()

    def closeEvent(self, event):
        self.disconnect()
        self.connection_dialog.close()
        super(AssistantPanel, self).closeEvent(event)


def create_interface():
    return AssistantPanel()
