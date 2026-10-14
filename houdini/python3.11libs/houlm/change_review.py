"""Bounded authored-state snapshots and diffs for the local action history.

These are descriptions of observed changes, not scene verification. No geometry,
stage, cook, parameter evaluation or callback invocation is used here.
"""

import difflib
import json

from . import scene


MAX_TARGETS = 16
MAX_PARAMETERS = 64
MAX_TEXT = 8192
MAX_BYTES = 96 * 1024
MAX_DIFF = 24 * 1024


def target_paths(arguments):
    """Read proposal metadata; source code is deliberately never interpreted."""
    paths = []
    if type(arguments) is not dict:
        return paths
    if "code" in arguments:
        paths.extend(arguments.get("target_paths", []))
    else:
        for operation in arguments.get("operations", []):
            if type(operation) is not dict:
                continue
            path = operation.get("path")
            if operation.get("op") == "create_node":
                path = operation.get("parent", "").rstrip("/") + "/" + operation.get("name", "")
            if path:
                paths.append(path)
    return list(dict.fromkeys(path for path in paths
                              if isinstance(path, str) and path.startswith("/") and len(path) <= 1024))


def snapshot_targets(hou, arguments):
    """Capture declared targets only, with priority for explicitly edited parms."""
    scene.require_main_thread(hou)
    paths = target_paths(arguments)
    result = {"nodes": {}, "truncated": len(paths) > MAX_TARGETS,
              "read_failures": 0, "scope": "Declared targets; authored values only, no cook."}
    priority = {}
    for operation in arguments.get("operations", []):
        if operation.get("op") == "set_parameter":
            priority.setdefault(operation["path"], []).append(operation["parameter"])
    for path in paths[:MAX_TARGETS]:
        try:
            node = hou.node(path)
            if node is None:
                result["nodes"][path] = {"exists": False}
                continue
            record = {"exists": True, "type": node.type().name(), "parameters": {},
                      "inputs": [], "truncated": False, "read_failures": 0}
            result["nodes"][path] = record
            names = priority.get(path, []) + ["snippet"]
            parms = list(node.parms())
            ordered = [parm for name in names for parm in parms if parm.name() == name]
            ordered += [parm for parm in parms if parm not in ordered]
            record["truncated"] = len(ordered) > MAX_PARAMETERS
            for parm in ordered[:MAX_PARAMETERS]:
                try:
                    value = str(parm.rawValue())
                    if len(value) > MAX_TEXT:
                        record["truncated"] = True
                    record["parameters"][parm.name()] = value[:MAX_TEXT]
                    if len(json.dumps(result).encode("utf-8")) > MAX_BYTES - 8192:
                        record["parameters"].pop(parm.name())
                        record["truncated"] = True
                        break
                except Exception:
                    record["read_failures"] += 1
            connections = node.inputConnections()
            record["truncated"] |= len(connections) > 32
            for connection in connections[:32]:
                source = connection.inputNode()
                record["inputs"].append({"input": connection.inputIndex(),
                                         "source": source.path() if source is not None else None,
                                         "output": connection.outputIndex()})
            children_method = getattr(node, "children", None)
            if children_method is not None:
                children = children_method()
                record["children"] = [child.path() for child in children[:64]]
                record["children_truncated"] = len(children) > 64
                record["truncated"] |= record["children_truncated"]
            result["nodes"][path] = record
            if len(json.dumps(result).encode("utf-8")) > MAX_BYTES:
                # Never save clipped values without marking the partial scope.
                result["nodes"].pop(path)
                result["truncated"] = True
                break
        except Exception:
            result["read_failures"] += 1
    result["truncated"] |= any(node.get("truncated", False) for node in result["nodes"].values())
    return result


def _diff(path, before, after):
    # Split on literal newlines; keep a useful final line even without a newline.
    return "\n".join(difflib.unified_diff(str(before).splitlines(), str(after).splitlines(),
                                           fromfile=path + " (before)", tofile=path + " (after)",
                                           lineterm=""))


def review_text(summary, arguments, before):
    """Add real text diffs for typed edits; never guess a Python action's result."""
    if "code" in arguments:
        return summary + ("\n\nChange comparison: Python is executed exactly as shown above. "
                          "Its after-state is unknown until Apply; the action history records "
                          "bounded before/after snapshots of the declared targets.")
    values = {path: dict(node.get("parameters", {})) for path, node in before.get("nodes", {}).items()}
    diffs = []
    for operation in arguments.get("operations", []):
        if operation.get("op") != "set_parameter" or not isinstance(operation.get("value"), str):
            continue
        path, name, value = operation["path"], operation["parameter"], operation["value"]
        previous = values.setdefault(path, {}).get(name)
        if previous is not None and previous != value:
            diffs.append(_diff(path + "/" + name, previous, value))
        values[path][name] = value
    if not diffs:
        return summary
    return summary + "\n\nAuthored text changes\n" + _bounded("\n\n".join(diffs))


def _bounded(text):
    return text if len(text) <= MAX_DIFF else text[:MAX_DIFF] + "\n[Change description truncated]"


def format_changes(before, after):
    """Readable actual deltas, explicitly limited to what both snapshots read."""
    previous, current = before.get("nodes", {}), after.get("nodes", {})
    lines = []
    for path in sorted(set(previous) | set(current)):
        old, new = previous.get(path), current.get(path)
        if old is None or new is None:
            lines.append(path + ": comparison unavailable (not captured in both snapshots).")
            continue
        if not old.get("exists") and new.get("exists"):
            lines.append("Created " + path + " (" + new.get("type", "unknown type") + ").")
        elif old.get("exists") and not new.get("exists"):
            lines.append("Removed from " + path + " (deleted or renamed).")
        elif old.get("exists") and new.get("exists"):
            if old.get("type") != new.get("type"):
                lines.append(path + ": type " + str(old.get("type")) + " → " + str(new.get("type")))
            a, b = old.get("parameters", {}), new.get("parameters", {})
            for name in sorted(set(a) | set(b)):
                if name not in a or name not in b:
                    if old.get("truncated") or new.get("truncated") or old.get("read_failures") or new.get("read_failures"):
                        continue
                    lines.append(path + "/" + name + (": parameter added." if name in b else ": parameter removed."))
                elif a[name] != b[name]:
                    lines.append(_diff(path + "/" + name, a[name], b[name]))
            if old.get("inputs") != new.get("inputs"):
                lines.append(path + ": inputs changed\nBefore: " + json.dumps(old.get("inputs", []))
                             + "\nAfter: " + json.dumps(new.get("inputs", [])))
            if ("children" in old and "children" in new
                    and not old.get("children_truncated") and not new.get("children_truncated")):
                for child in sorted(set(new["children"]) - set(old["children"])):
                    lines.append("Appeared under " + path + ": " + child + " (created or moved here).")
                for child in sorted(set(old["children"]) - set(new["children"])):
                    lines.append("Left " + path + ": " + child + " (deleted, renamed or moved).")
    if not lines:
        lines.append("No authored changes observed in the captured target fields.")
    if (before.get("truncated") or after.get("truncated") or before.get("read_failures") or after.get("read_failures")
            or any(node.get("read_failures") for node in list(previous.values()) + list(current.values()))):
        lines.append("Capture was partial; uncaptured changes may exist.")
    lines.append("Declared targets only. No cook or functional verification was performed.")
    return _bounded("\n\n".join(lines))
