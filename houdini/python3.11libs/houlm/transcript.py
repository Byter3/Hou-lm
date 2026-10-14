"""Inert chat transcript with collapsible activity and command receipts.

Saved/model content is text, never HTML or executable code. Only locally
generated node-navigation and disclosure links are actionable.
"""

import copy
import html
import re
from urllib.parse import quote, unquote, urlsplit

from PySide2 import QtCore, QtGui, QtWidgets

from . import chat_format, node_paths


COMMAND_MARKER = "\x1eHOULM_COMMAND:"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,256}\Z")
_STATUSES = {
    "pending": ("Awaiting execution", "#b5a37e"),
    "running": ("Running", "#94d7df"),
    "completed": ("Completed", "#96c6ab"),
    "failed": ("Failed — inspect result", "#e0a589"),
    "rejected": ("Not run — rejected", "#b2a190"),
    "cancelled": ("Not run — cancelled", "#a3aab2"),
    "unknown": ("Execution outcome unknown", "#d3b38a"),
}


def _valid_record(record):
    if not isinstance(record, dict):
        return None
    identifier = record.get("id")
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        return None
    if not all(isinstance(record.get(key), str) for key in ("title", "source", "status")):
        return None
    if not isinstance(record.get("result_text", ""), str):
        return None
    return {
        "id": identifier,
        "title": record["title"],
        "source": record["source"],
        "status": record["status"] if record["status"] in _STATUSES else "unknown",
        "result_text": record.get("result_text", ""),
    }


def _command_url(identifier, action):
    return "houlm-command:///" + action + "/" + quote(identifier, safe="")


def _activity_url(index):
    return "houlm-activity:///toggle/" + str(index)


def _activity_body(value):
    """Recognize saved legacy activity without rewriting its archived text."""
    title, separator, body = value.partition("\n")
    if separator and title in ("Scene tools:", "Tool activity:", "Activity:"):
        return body.rstrip("\n")
    return None


def plain_text(parts, commands):
    """Human-readable history export, including source for command markers."""
    records = {}
    for value in commands:
        record = _valid_record(value)
        if record is not None:
            records[record["id"]] = record
    blocks = []
    for value in parts:
        if value.startswith(COMMAND_MARKER):
            record = records.get(value[len(COMMAND_MARKER):])
            if record:
                label = _STATUSES[record["status"]][0]
                text = "Command: " + record["title"] + " [" + label + "]\n" + record["source"]
                if record["result_text"]:
                    text += "\nResult:\n" + record["result_text"]
                blocks.append(text)
            else:
                blocks.append("Command details unavailable.")
        else:
            blocks.append(value)
    return "\n".join(blocks)


class Transcript(QtWidgets.QTextBrowser):
    """Keep legacy text blocks while recording commands independently.

    ``_parts`` is the ordered, JSON-compatible sequence of chat blocks and
    command markers. Persist it together with ``export_commands()``. Restored
    receipts are always collapsed, and neither restoration nor expansion runs
    their contents.
    """

    changed = QtCore.Signal(bool)
    nodeActivated = QtCore.Signal(QtCore.QUrl)

    def __init__(self, parent=None):
        super(Transcript, self).__init__(parent)
        self._parts = []
        self._commands = {}
        self._expanded = set()
        self._expanded_activity = set()
        self.setObjectName("transcript")
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setReadOnly(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.document().setDefaultFont(self.font())
        self.document().setDocumentMargin(2)
        option = self.document().defaultTextOption()
        option.setWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)
        self.anchorClicked.connect(self._activate_link)

    def changeEvent(self, event):
        super(Transcript, self).changeEvent(event)
        if (event.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.ApplicationFontChange)
                and hasattr(self, "_parts")):
            # Follow Houdini's inherited UI font, including its UI scale. HTML
            # never supplies a smaller fixed size for transcript metadata.
            self.document().setDefaultFont(self.font())
            self._render(notify=False, preserve_position=True)

    def loadResource(self, resource_type, url):
        return QtCore.QByteArray()

    def appendPlainText(self, value):
        self._parts.append(value)
        self._render()

    def setPlainText(self, value):
        self._parts = [value] if value else []
        self._commands.clear()
        self._expanded.clear()
        self._expanded_activity.clear()
        self._render()

    def toPlainText(self):
        return plain_text(self._parts, self._commands.values())

    def clear(self):
        self._parts = []
        self._commands.clear()
        self._expanded.clear()
        self._expanded_activity.clear()
        self._render()

    def append_command(self, identifier, title, source, status="pending"):
        """Insert one exact-source receipt; repeated IDs update in place."""
        record = _valid_record({"id": identifier, "title": title,
                                "source": source, "status": status})
        if record is None:
            raise ValueError("A command receipt requires a valid ID and text fields.")
        if identifier in self._commands:
            record["result_text"] = self._commands[identifier]["result_text"]
        self._commands[identifier] = record
        marker = COMMAND_MARKER + identifier
        if marker not in self._parts:
            self._parts.append(marker)
        self._render()

    def update_command(self, identifier, status, result_text=""):
        if identifier not in self._commands:
            return False
        if not isinstance(result_text, str):
            raise TypeError("Command results must be text.")
        record = self._commands[identifier]
        record["status"] = status if status in _STATUSES else "unknown"
        record["result_text"] = result_text
        self._render()
        return True

    def set_commands(self, records):
        """Load only known text fields; saved records cannot supply markup."""
        self._commands = {}
        self._expanded.clear()
        self._expanded_activity.clear()
        if isinstance(records, list):
            for value in records:
                record = _valid_record(value)
                if record is not None and record["id"] not in self._commands:
                    if record["status"] == "running":
                        record["status"] = "unknown"
                    elif record["status"] == "pending":
                        record["status"] = "cancelled"
                    self._commands[record["id"]] = record
        self._render()

    def export_commands(self):
        return copy.deepcopy(list(self._commands.values()))

    def _activate_link(self, url):
        value = url.toString()
        if node_paths.path_from_url(value) is not None:
            self.nodeActivated.emit(url)
            return
        try:
            parsed = urlsplit(value)
        except ValueError:
            return
        if parsed.scheme == "houlm-activity":
            if parsed.netloc or parsed.query or parsed.fragment:
                return
            match = re.fullmatch(r"/toggle/(0|[1-9][0-9]{0,8})", parsed.path)
            if match is None:
                return
            index = int(match.group(1))
            if index >= len(self._parts) or _activity_body(self._parts[index]) is None:
                return
            if index in self._expanded_activity:
                self._expanded_activity.remove(index)
            else:
                self._expanded_activity.add(index)
            self._render(notify=False, preserve_position=True)
            return
        if (parsed.scheme != "houlm-command" or parsed.netloc
                or parsed.query or parsed.fragment):
            return
        pieces = parsed.path.split("/")
        if len(pieces) != 3 or pieces[0] or pieces[1] not in ("toggle", "copy"):
            return
        identifier = unquote(pieces[2])
        if identifier not in self._commands:
            return
        if pieces[1] == "copy":
            QtWidgets.QApplication.clipboard().setText(self._commands[identifier]["source"])
            return
        if identifier in self._expanded:
            self._expanded.remove(identifier)
        else:
            self._expanded.add(identifier)
        # Expanding a receipt changes presentation only, not saved chat state.
        self._render(notify=False, preserve_position=True)

    def _command_card(self, record):
        identifier = record["id"]
        expanded = identifier in self._expanded
        label, accent = _STATUSES[record["status"]]
        toggle = html.escape(_command_url(identifier, "toggle"), quote=True)
        copy_url = html.escape(_command_url(identifier, "copy"), quote=True)
        source = record["source"]
        line_count = source.count("\n") + (1 if source else 0)
        heading = (
            '<a href="' + toggle + '" style="color:#c8d5dd;text-decoration:none;">'
            + ('[-] ' if expanded else '[+] ') + html.escape(record["title"])
            + '</a><br><span style="color:' + accent + ';">'
            + label + '</span><span style="color:#a0b0ba;">'
            + ' &nbsp; · &nbsp; ' + str(line_count) + (' line' if line_count == 1 else ' lines')
            + ' &nbsp; · &nbsp; </span><a href="' + toggle + '" style="color:#9bc7d6;text-decoration:none;">'
            + ('Click to collapse' if expanded else 'Inspect commands') + '</a>'
        )
        body = ""
        if expanded:
            body = ('<p style="margin:10px 0 6px 0;color:#a0b3bf;">EXACT COMMANDS'
                    ' &nbsp; <a href="' + copy_url + '" style="color:#8bcbd4;text-decoration:none;">Copy</a></p>'
                    '<pre style="white-space:pre-wrap;font-family:Consolas,monospace;'
                    'color:#cfdee6;margin:0px;">' + html.escape(source) + '</pre>')
            if record["result_text"]:
                body += ('<p style="margin:12px 0 5px 0;color:#a0b3bf;">EXECUTION RESULT</p>'
                         '<pre style="white-space:pre-wrap;font-family:Consolas,monospace;'
                         'color:#a6b4bc;margin:0px;">' + html.escape(record["result_text"]) + '</pre>')
        return ('<table width="100%" cellspacing="0" cellpadding="0"><tr>'
                '<td width="3%"></td><td width="3" bgcolor="#527989"></td>'
                '<td bgcolor="#19252e"><table width="100%" cellspacing="0" cellpadding="9">'
                '<tr><td>' + heading + body + '</td></tr></table></td>'
                '<td width="3%"></td></tr></table><p style="line-height:50%;margin:0px;">&nbsp;</p>')

    def _activity_card(self, body, index):
        expanded = index in self._expanded_activity
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        first_line = lines[0] if lines else "Scene activity"
        summary = first_line if len(first_line) <= 140 else first_line[:137] + "…"
        toggle = html.escape(_activity_url(index), quote=True)
        heading = (
            '<p style="margin:0px 0px 5px 0px;font-weight:600;color:#a0b3bf;">'
            'TOOL ACTIVITY</p><a href="' + toggle + '" style="color:#c8d0d6;text-decoration:none;">'
            + ('[-] ' if expanded else '[+] ') + html.escape(summary) + '</a>'
            '<br><span style="color:#a0b0ba;">'
            + str(len(lines)) + (' detail line' if len(lines) == 1 else ' detail lines')
            + ' &nbsp; · &nbsp; </span><a href="' + toggle + '" '
            'style="color:#9bc7d6;text-decoration:none;">'
            + ('Hide details' if expanded else 'Show details') + '</a>'
        )
        details = ('<p style="margin:9px 0px 0px 0px;">'
                   + html.escape(body).replace("\n", "<br>") + '</p>') if expanded else ""
        return ('<table width="100%" cellspacing="0" cellpadding="0"><tr>'
                '<td width="3%"></td><td width="2" bgcolor="#4d5c66"></td>'
                '<td bgcolor="#1b252d"><table width="100%" cellspacing="0" cellpadding="9">'
                '<tr><td style="color:#c8d0d6;">' + heading + details
                + '</td></tr></table></td><td width="3%"></td></tr></table>'
                '<p style="line-height:50%;margin:0px;">&nbsp;</p>')

    def _text_card(self, value):
        title, separator, body = value.partition("\n")
        body = body.rstrip("\n") if separator else value
        user = title.startswith("You")
        failed = title.startswith("[Request failed]")
        tool_activity = title == "Scene tools:"
        if failed:
            body = value.removeprefix("[Request failed]").strip()
        heading = "YOU" if user else "REQUEST FAILED" if failed else "TOOL ACTIVITY" if tool_activity else "HOULM"
        accent = "#a8d5da" if user else "#e0ad87" if failed else "#a0b3bf" if tool_activity else "#9dd6df"
        surface = "#294048" if user else "#342c28" if failed else "#1b252d" if tool_activity else "#202b33"
        body_color = "#c8d0d6" if tool_activity else "#e4edf1"
        # Activity remains literal text. Only assistant prose supports the
        # inert Markdown subset; URLs and HTML from either remain inert.
        safe_body = ('<p style="margin:0px;">' + html.escape(body).replace("\n", "<br>") + '</p>'
                     if user or failed or tool_activity else chat_format.render_markdown(body))
        context = " &nbsp; · &nbsp; SELECTED NODES" if "[selected node text included]" in title else ""
        left_margin = "10%" if user else "3%" if tool_activity else "0"
        right_margin = "3%" if not user else "0"
        return (
            '<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            '<td width="' + left_margin + '"></td>'
            + ('<td width="2" bgcolor="#4d5c66"></td>' if tool_activity else '')
            + '<td bgcolor="' + surface + '"><table width="100%" cellspacing="0" cellpadding="'
            + ('9' if tool_activity else '11') + '"><tr><td style="color:' + body_color + ';">'
            '<p style="margin:0px 0px 7px 0px;font-weight:600;color:' + accent + ';">'
            + heading + context + '</p>' + safe_body + '</td></tr></table></td>'
            '<td width="' + right_margin + '"></td>'
            '</tr></table><p style="line-height:50%;margin:0px;">&nbsp;</p>'
        )

    def _render(self, notify=True, preserve_position=False):
        scrollbar = self.verticalScrollBar()
        previous_position = scrollbar.value()
        follow_tail = not preserve_position and scrollbar.maximum() - previous_position < 32
        cards = []
        for index, value in enumerate(self._parts):
            if value.startswith(COMMAND_MARKER):
                record = self._commands.get(value[len(COMMAND_MARKER):])
                if record:
                    cards.append(self._command_card(record))
                else:
                    cards.append(self._text_card("Scene tools:\nCommand details unavailable."))
            else:
                body = _activity_body(value)
                cards.append(self._activity_card(body, index) if body is not None else self._text_card(value))
        self.document().setDefaultFont(self.font())
        super(Transcript, self).setHtml("".join(cards))
        # Qt's HTML importer treats <pre> blocks as unbreakable even when CSS
        # says pre-wrap. Keep the original text and indentation, but permit
        # visual wrapping so a long Python line cannot widen a docked pane.
        block = self.document().begin()
        while block.isValid():
            if block.blockFormat().nonBreakableLines():
                cursor = QtGui.QTextCursor(block)
                formatting = block.blockFormat()
                formatting.setNonBreakableLines(False)
                cursor.setBlockFormat(formatting)
            block = block.next()
        scrollbar.setValue(scrollbar.maximum() if follow_tail else previous_position)
        if notify:
            self.changed.emit(bool(self._parts))
