"""Bounded, text-only snapshots of the current Houdini selection.

Call ``snapshot_selection(hou)`` from Houdini's GUI main thread. This module
does not import hou, evaluate parameters, cook nodes, or read scene files.
The returned dict fits in 64 KiB when serialized with json.dumps(indent=2),
including ASCII escaping. Compact JSON and UTF-8 Unicode output are smaller.
"""

import json
import os
import sys
import threading
import uuid


MAX_NODES = 8
MAX_PARAMETERS = 64
MAX_CONNECTIONS = 16
MAX_DIAGNOSTICS = 16
MAX_STRING_CHARS = 8192
MAX_SNAPSHOT_BYTES = 64 * 1024
_TRUNCATION_MARKER = "...[truncated]"
_SESSION_PID = os.getpid()
_SESSION_ID = str(uuid.uuid4())


def require_main_thread(hou=None):
    """Reject unsafe calls before touching HOM, using Qt's actual UI thread."""
    message = "Read the Houdini scene on the GUI main thread."
    qt_core = sys.modules.get("PySide2.QtCore")
    qt_widgets = sys.modules.get("PySide2.QtWidgets")
    real_hou = getattr(hou, "__name__", None) == "hou"
    if real_hou and (qt_core is None or qt_widgets is None):
        try:
            from PySide2 import QtCore as qt_core, QtWidgets as qt_widgets
        except ImportError:
            raise RuntimeError(message) from None
    if qt_core is not None and qt_widgets is not None:
        app = qt_widgets.QApplication.instance()
        if (real_hou and app is None) or (app is not None and qt_core.QThread.currentThread() != app.thread()):
            raise RuntimeError(message)
        if app is not None:
            return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(message)


def session_metadata(hou):
    """Small process/session identity; the HIP's basename never its full path."""
    require_main_thread(hou)
    global _SESSION_PID, _SESSION_ID
    pid = os.getpid()
    if pid != _SESSION_PID:
        _SESSION_PID, _SESSION_ID = pid, str(uuid.uuid4())
    result = {"session": {"id": _SESSION_ID, "pid": pid}, "truncated": False, "read_failures": 0}
    for key, callback, limit in (
        ("houdini_version", hou.applicationVersionString, 64),
        ("scene_name", hou.hipFile.basename, 256),
    ):
        value = _read(callback, "unavailable", result)
        result["session"][key] = _text(value, limit)
        if len(str(value)) > limit:
            result["truncated"] = True
    return result


def _text(value, limit=MAX_STRING_CHARS):
    # HOM returns strings here. Replace lone surrogates so callers may safely
    # encode the returned JSON as UTF-8, including with ensure_ascii=False.
    value = str(value).encode("utf-8", errors="replace").decode("utf-8")
    if len(value) > limit:
        return value[: max(0, limit - len(_TRUNCATION_MARKER))] + _TRUNCATION_MARKER
    return value


def _size(snapshot):
    return len(json.dumps(snapshot, indent=2, ensure_ascii=True).encode("utf-8"))


def _mark_truncated(snapshot, record):
    snapshot["truncated"] = True
    record["truncated"] = True


def _read(callback, default, record):
    try:
        return callback()
    except Exception:
        # Exception messages may contain unrelated file paths or script text.
        # Report incomplete reads without serializing exception details.
        record["read_failures"] += 1
        return default


def _append(snapshot, record, target, value, text_key=None):
    """Append within the shared budget; shorten raw text, never node paths."""
    target.append(value)
    # Leave room for subsequent counter updates and truncation flags.
    budget = MAX_SNAPSHOT_BYTES - 512
    if _size(snapshot) <= budget:
        return True
    _mark_truncated(snapshot, record)
    if text_key is not None:
        original = value[text_key]
        value["raw_value_truncated"] = True
        low, high, best = 0, min(len(original), MAX_STRING_CHARS - len(_TRUNCATION_MARKER)), None
        while low <= high:
            mid = (low + high) // 2
            value[text_key] = original[:mid] + _TRUNCATION_MARKER
            if _size(snapshot) <= budget:
                best = value[text_key]
                low = mid + 1
            else:
                high = mid - 1
        if best is not None:
            value[text_key] = best
            return True
    target.pop()
    return False


def snapshot_selection(hou):
    """Return authored text for up to eight selected nodes, without cooking.

    ``raw_value`` is unevaluated authored text (including expressions), not
    the current evaluated parameter value. Diagnostics belong to the last
    cook and may be stale. Selection and scene state are never changed.
    """
    require_main_thread(hou)
    selection_status = {"read_failures": 0}
    selected = _read(hou.selectedNodes, (), selection_status)
    snapshot = snapshot_nodes(hou, selected)
    snapshot["selected_node_count"] = snapshot.pop("node_count")
    snapshot["read_failures"] += selection_status["read_failures"]
    return snapshot


def snapshot_nodes(hou, nodes, *, path_limit=256):
    """Capture supplied node references without changing Houdini's selection.

    ``path_limit`` is internal, bounded to 1024 characters for inspection tools.
    Parameter text remains authored/unevaluated and diagnostics are last-cook.
    """
    require_main_thread(hou)
    if type(path_limit) is not int or not 1 <= path_limit <= 1024:
        raise ValueError("Invalid node path text limit.")
    metadata = session_metadata(hou)
    snapshot = {
        "schema_version": 1,
        "session": metadata["session"],
        "node_count": len(nodes),
        "nodes": [],
        "limits": {
            "nodes": MAX_NODES,
            "parameters_per_node": MAX_PARAMETERS,
            "connections_per_direction": MAX_CONNECTIONS,
            "diagnostics_per_kind": MAX_DIAGNOSTICS,
            "string_characters": MAX_STRING_CHARS,
            "serialized_bytes": MAX_SNAPSHOT_BYTES,
        },
        "truncated": metadata["truncated"] or len(nodes) > MAX_NODES,
        "read_failures": metadata["read_failures"],
    }
    sources = []

    # Reserve identities for every included node before adding large values.
    for node in nodes[:MAX_NODES]:
        record = {
            "path": "",
            "type": "",
            "inputs": [],
            "outputs": [],
            "input_count": 0,
            "output_count": 0,
            "parameters": [],
            "parameter_count": 0,
            "last_cook_diagnostics": {"errors": [], "warnings": []},
            "truncated": False,
            "read_failures": 0,
        }
        raw_path = _read(node.path, "unavailable", record)
        raw_type = _read(lambda: node.type().name(), "unavailable", record)
        record["path"] = _text(raw_path, path_limit)
        record["type"] = _text(raw_type, 128)
        if len(str(raw_path)) > path_limit or len(str(raw_type)) > 128:
            _mark_truncated(snapshot, record)
        snapshot["nodes"].append(record)
        sources.append((node, record))

    # Port numbers matter when diagnosing/reconnecting multi-output networks.
    # Reserve these before potentially large VEX/parameter values consume space.
    for node, record in sources:
        callback = getattr(node, "inputConnections", None)
        if callback is not None:
            record["input_connections"] = []
            connections = _read(callback, (), record)
            if len(connections) > MAX_CONNECTIONS:
                _mark_truncated(snapshot, record)
            for connection in connections[:MAX_CONNECTIONS]:
                def connection_record():
                    source = connection.inputNode()
                    return {"input_index": connection.inputIndex(),
                            "source": source.path() if source is not None else None,
                            "output_index": connection.outputIndex()}
                entry = _read(connection_record, None, record)
                if entry is None:
                    continue
                if entry["source"] is not None and len(entry["source"]) > path_limit:
                    _mark_truncated(snapshot, record)
                    continue
                if not _append(snapshot, record, record["input_connections"], entry):
                    break

    # Give every selected wrangle's snippet priority over ordinary parameters.
    candidates = []
    for node, record in sources:
        parms = _read(node.parms, (), record)
        record["parameter_count"] = len(parms)
        snippet = _read(lambda: node.parm("snippet"), None, record)
        ordered = []
        if snippet is not None:
            ordered.append(snippet)
        for parm in parms:
            if len(ordered) >= MAX_PARAMETERS:
                break
            if snippet is not None and parm == snippet:
                continue
            ordered.append(parm)
        if len(parms) > len(ordered):
            _mark_truncated(snapshot, record)
        candidates.append((record, ordered))

    def add_parameter(record, parm):
        name = _read(parm.name, "unavailable", record)
        raw_value = _read(parm.rawValue, None, record)
        if raw_value is None:
            return
        entry = {"name": _text(name, 128), "raw_value": _text(raw_value)}
        if len(str(raw_value)) > MAX_STRING_CHARS:
            entry["raw_value_truncated"] = True
            _mark_truncated(snapshot, record)
        if len(str(name)) > 128:
            _mark_truncated(snapshot, record)
        _append(snapshot, record, record["parameters"], entry, "raw_value")

    # Round-robin prevents one selected node consuming the whole text budget.
    for index in range(MAX_PARAMETERS):
        for record, parms in candidates:
            if index < len(parms):
                add_parameter(record, parms[index])

    for node, record in sources:
        for field, callback in (("inputs", node.inputs), ("outputs", node.outputs)):
            connected = _read(callback, (), record)
            record[field[:-1] + "_count"] = len(connected)
            if len(connected) > MAX_CONNECTIONS:
                _mark_truncated(snapshot, record)
            for other in connected[:MAX_CONNECTIONS]:
                path = None if other is None else _read(other.path, "unavailable", record)
                if path is not None and len(str(path)) > path_limit:
                    _mark_truncated(snapshot, record)
                value = None if path is None else _text(path, path_limit)
                if not _append(snapshot, record, record[field], value):
                    break
        for field, callback in (("errors", node.errors), ("warnings", node.warnings)):
            messages = _read(callback, (), record)
            if len(messages) > MAX_DIAGNOSTICS:
                _mark_truncated(snapshot, record)
            for message in messages[:MAX_DIAGNOSTICS]:
                if len(str(message)) > MAX_STRING_CHARS:
                    _mark_truncated(snapshot, record)
                if not _append(
                    snapshot, record, record["last_cook_diagnostics"][field], _text(message)
                ):
                    break

    # Counter updates fit in the reserved space; this guards later edits too.
    if _size(snapshot) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Selection snapshot exceeded its text-size limit.")
    return snapshot
