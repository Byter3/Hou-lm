"""Read-only Houdini query allowlist. Execute on the GUI thread, never a worker.

Only authored parameter text and already-available node metadata are inspected.
There is no code execution, evaluation, cooking, geometry, file or image access.
"""

from . import scene


MAX_RESULT_BYTES = 64 * 1024
MAX_SELECTION_PATHS = 64
MAX_NODE_PATH_CHARS = 1024
MAX_CHILDREN_PER_PAGE = 32
_ARGUMENTS = {
    "session_info": (frozenset(), frozenset()),
    "get_selection": (frozenset(), frozenset()),
    "inspect_node": (frozenset(("path",)), frozenset(("path",))),
    "list_children": (frozenset(("path",)), frozenset(("path", "offset", "limit"))),
    "list_node_types": (frozenset(), frozenset(("parent", "category", "query", "offset", "limit"))),
    "parameter_schema": (frozenset(("path",)), frozenset(("path", "query", "offset", "limit"))),
    "node_type_schema": (frozenset(("node_type",)), frozenset(("parent", "category", "node_type", "query", "offset", "limit"))),
}


def _validate(operation, arguments):
    if not isinstance(operation, str) or operation not in _ARGUMENTS:
        raise ValueError("Unsupported scene query.")
    if not isinstance(arguments, dict):
        raise ValueError("Scene query arguments must be an object.")
    required, allowed = _ARGUMENTS[operation]
    keys = set(arguments)
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise ValueError("Invalid arguments for this scene query.")
    if operation in ("list_node_types", "node_type_schema"):
        if ("parent" in arguments) == ("category" in arguments):
            raise ValueError("Supply exactly one existing parent path or node category.")
    if "category" in arguments:
        category = arguments["category"]
        if (not isinstance(category, str) or not 1 <= len(category) <= 64
                or not category.isascii() or not category[0].isalpha()
                or not all(char.isalnum() or char == "_" for char in category)):
            raise ValueError("Use an exact node category name, such as Sop, Dop or Lop.")
    for field in ("path", "parent"):
        if field not in arguments:
            continue
        path = arguments[field]
        if not isinstance(path, str) or not path.startswith("/") or len(path) > MAX_NODE_PATH_CHARS:
            raise ValueError("Use an absolute Houdini node path of at most 1024 characters.")
        if any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF or char in "\\*?[]{}" for char in path):
            raise ValueError("Node paths must not contain control characters or patterns.")
        if path != "/" and any(part in ("", ".", "..") for part in path[1:].split("/")):
            raise ValueError("Node paths must not contain empty or relative segments.")
    if "query" in arguments:
        query = arguments["query"]
        if not isinstance(query, str) or len(query) > 128 or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in query):
            raise ValueError("Use a short plain-text query.")
    if "node_type" in arguments:
        value = arguments["node_type"]
        if not isinstance(value, str) or not value or len(value) > 256 or any(char.isspace() or char in "/\\*?[]{}" or ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("Use an exact installed node type name.")
    if operation in ("list_children", "list_node_types", "parameter_schema", "node_type_schema"):
        offset, limit = arguments.get("offset", 0), arguments.get("limit", MAX_CHILDREN_PER_PAGE)
        if type(offset) is not int or offset < 0:
            raise ValueError("Child offset must be a non-negative integer.")
        if type(limit) is not int or not 1 <= limit <= MAX_CHILDREN_PER_PAGE:
            raise ValueError("Child limit must be an integer from 1 to 32.")


def _base(hou, operation):
    result = {"schema_version": 1, "operation": operation}
    result.update(scene.session_metadata(hou))
    return result


def _node(hou, path):
    try:
        node = hou.node(path)
    except Exception:
        raise ValueError("The requested node could not be read.") from None
    if node is None:
        raise ValueError("The requested node does not exist in this Houdini session.")
    return node


def _finish(result):
    try:
        size = scene._size(result)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Scene query result exceeded its text-size limit.") from None
    if size > MAX_RESULT_BYTES:
        raise ValueError("Scene query result exceeded its text-size limit.")
    return result


def _path(node, record):
    value = scene._read(node.path, None, record)
    if not isinstance(value, str):
        record["truncated"] = True
        return None
    if len(value) > MAX_NODE_PATH_CHARS:
        # Dropping a path is safer than returning a shortened, invalid identity.
        record["truncated"] = True
        return None
    safe = scene._text(value, MAX_NODE_PATH_CHARS)
    if safe != value:
        record["truncated"] = True
        return None
    return value


def _selection(hou):
    result = _base(hou, "get_selection")
    try:
        selected = hou.selectedNodes()
    except Exception:
        raise ValueError("The current selection could not be read.") from None
    result.update({
        "selected_node_count": len(selected), "paths": [],
        "limits": {"paths": MAX_SELECTION_PATHS, "node_path_characters": MAX_NODE_PATH_CHARS,
                   "serialized_bytes": MAX_RESULT_BYTES},
    })
    result["truncated"] = result["truncated"] or len(selected) > MAX_SELECTION_PATHS
    for node in selected[:MAX_SELECTION_PATHS]:
        path = _path(node, result)
        if path is None:
            continue
        result["paths"].append(path)
        if scene._size(result) > MAX_RESULT_BYTES - 512:
            result["paths"].pop()
            result["truncated"] = True
            break
    return _finish(result)


def _inspect(hou, path):
    node = _node(hou, path)
    snapshot = scene.snapshot_nodes(hou, (node,), path_limit=MAX_NODE_PATH_CHARS)
    result = {
        "schema_version": 1, "operation": "inspect_node",
        "session": snapshot["session"], "node": snapshot["nodes"][0],
        "limits": snapshot["limits"],
        "truncated": snapshot["truncated"], "read_failures": snapshot["read_failures"],
    }
    return _finish(result)


def _children(hou, arguments):
    node = _node(hou, arguments["path"])
    try:
        children = node.children()
    except Exception:
        raise ValueError("The requested network's children could not be read.") from None
    offset, limit = arguments.get("offset", 0), arguments.get("limit", MAX_CHILDREN_PER_PAGE)
    result = _base(hou, "list_children")
    result.update({
        "path": arguments["path"], "child_count": len(children), "offset": offset,
        "limit": limit, "children": [], "next_offset": None,
        "limits": {"children_per_page": MAX_CHILDREN_PER_PAGE, "node_path_characters": MAX_NODE_PATH_CHARS,
                   "serialized_bytes": MAX_RESULT_BYTES},
    })
    scanned = 0
    for child in children[offset:offset + limit]:
        record = {"path": "", "type": "", "child_count": 0, "truncated": False, "read_failures": 0}
        child_path = _path(child, record)
        if child_path is None:
            result["truncated"] = True
            result["read_failures"] += record["read_failures"]
            scanned += 1
            continue
        record["path"] = child_path
        node_type = scene._read(lambda: child.type().name(), "unavailable", record)
        record["type"] = scene._text(node_type, 128)
        record["truncated"] = record["truncated"] or len(str(node_type)) > 128
        record["child_count"] = len(scene._read(child.children, (), record))
        result["children"].append(record)
        if scene._size(result) > MAX_RESULT_BYTES - 512:
            result["children"].pop()
            result["truncated"] = True
            break
        scanned += 1
        result["truncated"] = result["truncated"] or record["truncated"]
    next_offset = offset + scanned
    if next_offset < len(children):
        result["next_offset"] = next_offset
        result["truncated"] = True
    if 0 < offset < len(children):
        result["truncated"] = True
    return _finish(result)


def execute(hou, operation, arguments):
    """Dispatch one validated read operation; never execute supplied code."""
    scene.require_main_thread(hou)
    _validate(operation, arguments)
    if operation == "session_info":
        return _finish(_base(hou, operation))
    if operation == "get_selection":
        return _selection(hou)
    if operation == "inspect_node":
        return _inspect(hou, arguments["path"])
    if operation in ("list_node_types", "parameter_schema", "node_type_schema"):
        from . import discovery
        return _finish(discovery.execute(hou, operation, arguments, _base(hou, operation)))
    return _children(hou, arguments)
