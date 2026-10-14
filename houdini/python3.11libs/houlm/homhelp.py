"""Bounded installed HOM documentation lookup; never invoke the documented API."""
import inspect
import json
import re
import types

from . import scene

MAX_DOCUMENT_CHARS = 12000
MAX_RESULT_BYTES = 64 * 1024
_SYMBOL = re.compile(r"hou(?:\.(?:[A-Za-z][A-Za-z0-9_]*|__init__)){0,6}\Z")


def _container(value):
    return isinstance(value, types.ModuleType) or inspect.isclass(value)


def _namespace(value):
    if isinstance(value, types.ModuleType):
        name = vars(value).get("__name__", "")
        if name != "hou" and not name.startswith("hou."):
            raise ValueError("Only installed Houdini API documentation is available.")
    return vars(value) if _container(value) else {}


def _docstring(value):
    # These built-in metadata descriptors cannot invoke a scene method or an
    # arbitrary user object's __getattribute__. Class/module lookups stay static.
    if isinstance(value, (types.FunctionType, types.BuiltinFunctionType,
                          types.MethodDescriptorType, types.WrapperDescriptorType, property)):
        text = value.__doc__
    else:
        text = inspect.getattr_static(value, "__doc__", "")
    return text if type(text) is str else ""


def lookup(hou, arguments):
    scene.require_main_thread(hou)
    if type(arguments) is not dict or not {"symbol"} <= set(arguments) <= {"symbol", "query", "offset", "limit", "doc_offset"}:
        raise ValueError("Supply symbol and optional query, offset, limit or doc_offset.")
    symbol = arguments["symbol"]
    if type(symbol) is not str or len(symbol) > 256 or not _SYMBOL.fullmatch(symbol):
        raise ValueError("Use a public HOM symbol such as hou.RampParmTemplate or hou.Parm.set.")
    query = arguments.get("query", "")
    if type(query) is not str or len(query) > 128 or any(ord(char) < 32 for char in query):
        raise ValueError("Use a short plain-text member filter.")
    offset, limit, doc_offset = arguments.get("offset", 0), arguments.get("limit", 32), arguments.get("doc_offset", 0)
    if type(offset) is not int or offset < 0 or type(doc_offset) is not int or doc_offset < 0 or type(limit) is not int or not 1 <= limit <= 32:
        raise ValueError("Use nonnegative offsets and a limit between 1 and 32.")
    value = hou
    for part in symbol.split(".")[1:]:
        if not _container(value):
            raise ValueError("Documentation paths cannot traverse live Houdini objects.")
        _namespace(value)
        try:
            value = inspect.getattr_static(value, part)
        except AttributeError:
            raise ValueError("This symbol is not present in the installed Houdini API.") from None
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
    members = _namespace(value)
    # Docstrings are metadata, not properties to evaluate. In particular, do
    # not invoke descriptors, objects' repr(), or live node methods.
    doc = _docstring(value)
    if inspect.isclass(value):
        initializer = inspect.getattr_static(value, "__init__", None)
        init_doc = _docstring(initializer) if initializer is not None else ""
        if type(init_doc) is str and init_doc and init_doc not in doc:
            doc += "\n\nConstructor:\n" + init_doc
    names = sorted(name for name in members if isinstance(name, str)
                   and (not name.startswith("_") or name == "__init__") and query.casefold() in name.casefold())
    # Include inherited class methods without executing descriptor lookups.
    if inspect.isclass(value):
        for parent in inspect.getmro(value)[1:]:
            names.extend(name for name in vars(parent) if not name.startswith("_") and query.casefold() in name.casefold())
        names = sorted(set(names))
    result = {"symbol": symbol, "source": "installed_houdini_docstrings",
              "documentation": scene._text(doc[doc_offset:doc_offset + MAX_DOCUMENT_CHARS], MAX_DOCUMENT_CHARS),
              "doc_offset": doc_offset, "next_doc_offset": doc_offset + MAX_DOCUMENT_CHARS if len(doc) > doc_offset + MAX_DOCUMENT_CHARS else None,
              "members": names[offset:offset + limit], "matched_count": len(names),
              "next_offset": offset + limit if len(names) > offset + limit else None,
              "note": "Documentation only; no scene API was executed. Query a member symbol for its full docstring."}
    # ASCII JSON can expand Unicode docstrings. Preserve explicit continuation.
    while len(json.dumps(result, ensure_ascii=True).encode()) > MAX_RESULT_BYTES:
        text = result["documentation"]
        if not text:
            raise ValueError("The installed documentation exceeds its size limit.")
        result["documentation"] = text[:len(text) // 2]
        result["next_doc_offset"] = doc_offset + len(result["documentation"])
    return result
