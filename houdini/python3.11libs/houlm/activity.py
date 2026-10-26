"""Bounded, factual activity text for the local scene tools.

These labels describe requests and observed outcomes, not the model's reasoning.
Only target identifiers, purpose summaries and metadata are displayed; source,
parameter values, stdout, returned Python data and exception text stay out.
The caller must render the returned strings as plain text.
"""
import json


MAX_TITLE = 180
MAX_DETAIL = 320
MAX_BATCH = 8
_PROPOSALS = {"propose_edits", "propose_python"}


def _text(value, limit=96):
    if not isinstance(value, str):
        return ""
    value = " ".join("".join(char for char in value if char.isprintable() or char.isspace()).split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _object(value, limit=128 * 1024):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or len(value) > limit:
        return {}
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError, RecursionError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _count(value):
    return value if type(value) is int and 0 <= value <= 10**9 else None


def _items(value):
    return value if isinstance(value, list) else []


def _quantity(count, singular, plural=None):
    return "%d %s" % (count, singular if count == 1 else plural or singular + "s")


def _row(phase, title, detail=""):
    return {"phase": phase, "title": _text(title, MAX_TITLE), "detail": _text(detail, MAX_DETAIL)}


def _scope(arguments):
    category = _text(arguments.get("category"))
    return category or _text(arguments.get("parent")) or "requested category"


def _filter(arguments):
    query = _text(arguments.get("query"), 64)
    return " matching ‘%s’" % query if query else ""


def _names(values, key="name"):
    names = [_text(item.get(key), 60) for item in _items(values)[:3] if isinstance(item, dict)]
    names = [name for name in names if name]
    return ", ".join(names) + (", …" if len(_items(values)) > 3 else "")


def _quality(result):
    if result.get("read_failures"):
        return " Some data could not be read."
    if result.get("truncated") or result.get("next_offset") is not None or result.get("next_doc_offset") is not None:
        return " Partial result; more data is available."
    return ""


def _pending(name, arguments):
    path = _text(arguments.get("path")) or "requested node"
    suffix = _filter(arguments)
    if name == "capture_viewport":
        return _row("inspecting", "Capturing the current-frame viewport", "Native single-frame flipbook; current-frame evaluation may cook the scene.")
    if name == "inspect_node":
        return _row("inspecting", "Reading %s" % path, "Checking existing parameters and wiring; no scene changes.")
    if name == "list_children":
        return _row("inspecting", "Looking inside %s" % path, "Checking which nodes already exist in this network.")
    if name == "get_selection":
        return _row("inspecting", "Checking your selected nodes")
    if name == "session_info":
        return _row("inspecting", "Checking the current Houdini session")
    if name == "list_node_types":
        return _row("discovering", "Finding %s node types%s" % (_scope(arguments), suffix), "Checking installed node definitions; no nodes are created.")
    if name == "node_type_schema":
        return _row("discovering", "Checking %s parameters%s" % (_text(arguments.get("node_type")) or "node type", suffix),
                    "Installed %s definition; no node instance is needed." % _scope(arguments))
    if name == "parameter_schema":
        return _row("inspecting", "Checking controls on %s%s" % (path, suffix), "Reading existing parameter definitions and authored values.")
    if name == "hom_help":
        return _row("discovering", "Looking up %s%s" % (_text(arguments.get("symbol")) or "Houdini API", suffix),
                    "Reading the installed API documentation; no scene changes.")
    if name in _PROPOSALS:
        return _row("preparing", "Preparing Python for review" if name == "propose_python" else "Preparing scene changes for review",
                    _text(arguments.get("summary"), 220) or "Nothing has been applied yet.")
    return _row("inspecting", "Checking a scene request")


def _edit_outcome(name, arguments, data):
    python = name == "propose_python"
    automatic = data.get("authorization") == "auto_run"
    prefix = "Houdini Python" if python else "Scene changes"
    purpose = _text(arguments.get("summary"), 180)
    if data.get("success") is True:
        if python:
            title = "Ran Houdini Python automatically" if automatic else "Ran reviewed Houdini Python"
            return _row("applied", title, purpose + (" · " if purpose else "") + "Execution completed; scene verification follows.")
        count = _count(data.get("completed_operations"))
        detail = (_quantity(count, "operation") + " completed. " if count is not None else "")
        paths = _names([{"name": item} for item in _items(data.get("changed_paths"))])
        title = "Applied scene changes automatically" if automatic else "Applied reviewed scene changes"
        return _row("applied", title, detail + (paths or purpose) + " · Scene verification follows.")
    status = data.get("status")
    if status == "rejected":
        return _row("rejected", prefix + ": proposal rejected", "Nothing from this proposal was executed.")
    if status in ("invalid", "not_permitted", "stale", "cancelled") and data.get("execution_started") is not True:
        descriptions = {"invalid": "The proposal could not be prepared.", "not_permitted": "The proposal was not permitted.",
                        "stale": "The scene changed after review; a fresh proposal is needed.", "cancelled": "Cancelled before execution."}
        return _row("failed", prefix + ": not applied", descriptions[status] + " No action code was executed." if python else descriptions[status])
    if data.get("rollback") == "reverted_scene_fields" and data.get("manual_undo_needed") is False:
        return _row("failed", prefix + ": could not apply", "Recorded scene changes were reverted. Checking the scene before correcting the proposal.")
    if data.get("execution_started") is False:
        return _row("failed", prefix + ": did not run", "No action code was executed. The assistant will receive the failure details.")
    return _row("failed", prefix + ": could not complete", "Changes may remain. Inspect the scene before retrying; see the error details.")


def describe_call(call, result=None):
    """Return {phase, title, detail}; no result means pending, never success.

    Accept the existing tool-call and role:tool result envelopes, or a decoded
    result object. Read success requires ``ok: true``; edit success requires
    ``success: true``. Missing or malformed outcomes never imply success.
    """
    function = _object(_object(call).get("function"))
    name = function.get("name", "")
    name = name[6:] if isinstance(name, str) and name.startswith("houlm_") else ""
    arguments = _object(function.get("arguments"), 32 * 1024)
    pending = _pending(name, arguments)
    if result is None:
        return pending
    data = _object(result)
    if data.get("role") == "tool":
        data = _object(data.get("content"))
    if name == "capture_viewport":
        return _row("inspected" if data.get("success") is True else "failed",
                    "Captured the current-frame viewport" if data.get("success") is True else "Viewport capture unavailable",
                    _text(data.get("message"), MAX_DETAIL))
    if name in _PROPOSALS:
        return _edit_outcome(name, arguments, data)
    if data.get("ok") is not True or not isinstance(data.get("result"), dict):
        return _row("failed", pending["title"] + " — unavailable",
                    "The read could not be completed. The target may be missing or its data unavailable; no scene changes.")
    observed = data["result"]
    quality = _quality(observed)
    path = _text(arguments.get("path")) or "requested node"
    if name == "list_children":
        count = _count(observed.get("child_count"))
        children = _items(observed.get("children"))
        if count == 0:
            return _row("inspected", "%s is empty" % path, "No child nodes were found. No scene changes." + quality)
        detail = "Read " + _quantity(len(children), "child node")
        if count is not None:
            detail += " of %d" % count
        names = _names(children, "path")
        return _row("inspected", "Checked %s" % path, detail + (": " + names if names else ".") + quality)
    if name == "inspect_node":
        node = _object(observed.get("node"))
        details = [_text(node.get("type")) or "Node metadata"]
        details.append(_quantity(len(_items(node.get("parameters"))), "parameter value") + " read")
        if isinstance(node.get("inputs"), list):
            linked = sum(isinstance(item, str) and bool(item) for item in node["inputs"])
            details.append(_quantity(linked, "input link") + " read")
        diagnostics = _object(node.get("last_cook_diagnostics"))
        errors, warnings = len(_items(diagnostics.get("errors"))), len(_items(diagnostics.get("warnings")))
        if errors or warnings:
            details.append("last cook: %d errors, %d warnings" % (errors, warnings))
        details.append("no cook requested")
        return _row("inspected", "Read %s" % path, " · ".join(details) + quality + _quality(node))
    if name == "get_selection":
        count = _count(observed.get("selected_node_count"))
        return _row("inspected", "No nodes selected" if count == 0 else "Read the current selection",
                    ("%d nodes selected. " % count if count is not None else "") + _names([{"name": item} for item in _items(observed.get("paths"))]) + quality)
    if name == "session_info":
        return _row("inspected", "Read the current Houdini session", "Session metadata is available to the assistant." + quality)
    if name == "hom_help":
        return _row("discovered", "Read API documentation: %s%s" % (_text(arguments.get("symbol")) or "Houdini API", _filter(arguments)),
                    "Installed API documentation; no scene commands were executed." + quality)
    if name in ("list_node_types", "node_type_schema", "parameter_schema"):
        key = "node_types" if name == "list_node_types" else "parameters"
        items = _items(observed.get(key))
        count = _count(observed.get("matched_count"))
        title = ("Found %s node types%s" % (_scope(arguments), _filter(arguments)) if name == "list_node_types" else
                 "Read %s type parameters%s" % (_text(arguments.get("node_type")), _filter(arguments)) if name == "node_type_schema" else
                 "Read controls on %s%s" % (path, _filter(arguments)))
        if count == 0:
            title = ("No matching %s node types found%s" % (_scope(arguments), _filter(arguments)) if name == "list_node_types" else
                     "No matching parameters on %s%s" % (_text(arguments.get("node_type")) or path, _filter(arguments)))
        detail = _quantity(len(items), "entry", "entries") + " returned"
        if count is not None:
            detail += " of %d matching" % count
        names = _names(items)
        detail += (": " + names if names else ".")
        if name != "parameter_schema":
            detail += " No nodes created."
        return _row("discovered" if name != "parameter_schema" else "inspected", title, detail + quality)
    return _row("inspected", "Scene query completed", "The assistant received the read result." + quality)


def summarize_batch(calls, results=None):
    """Summarize up to eight calls, pairing outcomes by ID, not list order."""
    by_id = {item.get("tool_call_id"): item for item in _items(results) if isinstance(item, dict) and isinstance(item.get("tool_call_id"), str)}
    rows = []
    for call in _items(calls)[:MAX_BATCH]:
        result = None if results is None else by_id.get(_object(call).get("id"), {})
        row = describe_call(call, result)
        # Group only adjacent identical entries; do not conceal ordering.
        if rows and all(rows[-1][key] == row[key] for key in ("phase", "title", "detail")):
            rows[-1]["count"] = rows[-1].get("count", 1) + 1
        else:
            rows.append(row)
    return rows


def format_batch(calls, results=None):
    """Compact plain-text rows for a transcript card; never Markdown/code."""
    lines = []
    for row in summarize_batch(calls, results):
        repeated = " (×%d)" % row["count"] if row.get("count", 1) > 1 else ""
        lines.append(row["title"] + repeated + ("\n" + row["detail"] if row["detail"] else ""))
    return "\n\n".join(lines)
