"""Authorized model-generated Python actions in the artist's Houdini process.

This is an in-process scripting capability, NOT a security sandbox. Source
checks catch common mistakes and direct access to unrelated system facilities;
installed assets and callbacks may have effects that those checks cannot see.
The panel must authorize every apply(): either the artist reviews the exact
source, or explicitly enables automatic execution in the local panel.
Validation still runs in either case. Undo grouping is not a transaction.
"""

import ast
import builtins
from contextlib import nullcontext
import json
import math
import re
import sys
import time
import uuid

from . import scene


MAX_CODE_CHARS = 24 * 1024
MAX_TARGETS = 32
MAX_OUTPUT_BYTES = 32 * 1024
MAX_STDOUT_CHARS = 8192
MAX_RESULT_STRING = 8192
MAX_TRACE_EVENTS = 200000
MAX_SECONDS = 15.0
_MAX_AST_NODES = 12000
_MAX_ITEMS = 256
_IMPORTS = {"hou", "math", "json", "re", "collections", "itertools",
            "functools", "statistics", "random", "pxr"}
_BLOCKED_NAMES = {
    "open", "eval", "exec", "compile", "input", "breakpoint", "help",
    "globals", "locals", "vars", "dir", "memoryview", "bytes", "bytearray",
    "exit", "quit", "os", "sys", "subprocess", "socket", "requests",
    "urllib", "http", "ctypes", "pickle", "marshal", "importlib",
    "inspect", "builtins", "pathlib", "shutil", "winreg", "keyring",
    "multiprocessing", "threading", "asyncio",
}
_BLOCKED_ATTRIBUTES = {
    "os", "sys", "subprocess", "socket", "requests", "urllib", "http",
    "ctypes", "pickle", "marshal", "importlib", "inspect", "builtins",
    "pathlib", "shutil", "winreg", "keyring", "multiprocessing", "threading", "asyncio",
    "ui", "qt", "session", "hscript", "hscriptExpression", "pythonexprs",
    "getenv", "putenv", "unsetenv", "environ", "environment",
    "saveViewToImage", "saveViewToFile", "saveImageDataToFile", "imageData",
    "flipbook", "grab", "grabWindow", "screenshot", "screen", "screens",
    "mainWindow", "curDesktop", "paneTabs", "desktop", "settrace",
    "setprofile", "system", "popen", "getlogin", "userName",
}
_BINARY_TEXT = re.compile(r"data\s*:\s*image/|(?:[A-Za-z0-9+/]{256,}={0,2})", re.I)
_SECRET_TEXT = re.compile(r"\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{12,}", re.I)
_SECRET_KEY = re.compile(r"^(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|credential[s]?)$", re.I)
_BLOCKED_TEXT = "[binary/image or credential data omitted]"


class PythonActionError(ValueError):
    def __init__(self, message, code="invalid_python_action", *, code_line=None, identifier=None):
        super().__init__(message)
        self.code = code
        self.code_line = code_line
        self.identifier = identifier

    def details(self):
        error = {"code": self.code, "message": str(self)[:2000]}
        if type(self.code_line) is int:
            error["code_line"] = self.code_line
        if self.identifier is not None:
            error["identifier"] = self.identifier
        return {"error": error}


class _Stopped(BaseException):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Plan:
    __slots__ = ("summary", "_hou", "_source", "_compiled", "_filename",
                 "_targets", "_session", "_selection", "_used")

    def __init__(self, hou, source, compiled, filename, targets, session, selection, summary):
        self._hou, self._source, self._compiled, self._filename = hou, source, compiled, filename
        self._targets, self._session, self._selection = targets, session, selection
        self._used = False
        self.summary = summary


def _valid_text(value, maximum):
    return (type(value) is str and 0 < len(value) <= maximum
            and not any(ord(c) == 0 or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _public_name(name):
    return type(name) is str and (name == "_" or not name.startswith("_"))


def _local_name(name):
    return type(name) is str and not name.startswith("__")


def _source_error(message, code, node=None, identifier=None):
    """Report the rejected source identifier/line, never surrounding source text."""
    line = getattr(node, "lineno", None)
    if identifier is not None:
        identifier = str(identifier)[:128]
        message += " Identifier: " + identifier + "."
    if type(line) is int:
        message += " Source line: %d." % line
    return PythonActionError(message, code, code_line=line, identifier=identifier)


def _check_attribute(name, node=None):
    if not _public_name(name) or name in _BLOCKED_ATTRIBUTES:
        raise _source_error("Private/system/UI-capture access is not available in scene actions.",
                            "python_access_not_available", node, name if node is not None else None)


def _bound_names(nodes):
    """Identify harmless local bindings for the preflight name check.

    The execution namespace, not this static UX check, determines availability.
    Binding a name such as `input`, `open` or `sys` does not import a facility or
    restore a missing builtin. Nested scope resolution is left to Python.
    """
    bound = set()
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Import):
            bound.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(alias.asname or alias.name for alias in node.names)
    return bound


def _validate_source(source, filename):
    try:
        tree = ast.parse(source, filename=filename, mode="exec")
    except (SyntaxError, ValueError, RecursionError) as error:
        line = getattr(error, "lineno", None)
        suffix = " at line %d" % line if type(line) is int else ""
        raise PythonActionError("The Python action contains invalid syntax" + suffix + ".", "python_syntax_error", code_line=line) from None
    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_AST_NODES:
        raise PythonActionError("The Python action is too complex; use smaller reviewed steps.")
    bound = _bound_names(nodes)
    for node in nodes:
        if isinstance(node, ast.Name) and (not _local_name(node.id) or (node.id in _BLOCKED_NAMES and node.id not in bound)):
            raise _source_error("This builtin/system Python name is not supplied to scene actions. Use supported scene APIs or a locally defined helper.",
                                "python_access_not_available", node, node.id)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not _local_name(node.name):
            raise _source_error("Use ordinary public or local helper names, not Python protocol overrides.",
                                "python_access_not_available", node, node.name)
        if isinstance(node, ast.Attribute):
            _check_attribute(node.attr, node)
        if isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith, ast.Global, ast.Nonlocal)):
            raise _source_error("Keep the action synchronous and self-contained.", "invalid_python_action", node, type(node).__name__)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if isinstance(node, ast.ImportFrom) and node.level:
                raise _source_error("Relative imports are not available.", "python_import_not_available", node, node.module)
            for name in names:
                if name.split(".")[0] not in _IMPORTS or any(not _public_name(part) for part in name.split(".")):
                    raise _source_error("Only hou, math, json, re, collections, itertools, functools, statistics, random and pxr imports are available.",
                                        "python_import_not_available", node, name)
            for alias in node.names:
                if alias.name == "*" or (alias.asname and not _local_name(alias.asname)):
                    raise _source_error("Use explicit public import names.", "python_import_not_available", node, alias.asname or alias.name)
                if isinstance(node, ast.ImportFrom):
                    _check_attribute(alias.name, node)
    try:
        return compile(tree, filename, "exec", dont_inherit=True)
    except (SyntaxError, ValueError, RecursionError):
        raise PythonActionError("The Python action cannot be compiled.", "python_syntax_error") from None


def _identity(node):
    return (node.path(), node.sessionId(), node.type().name())


def _session(hou):
    metadata = scene.session_metadata(hou)["session"]
    return (metadata["id"], metadata["pid"], metadata.get("scene_name"))


def _selection(hou):
    return tuple(_identity(node) for node in hou.selectedNodes())


def _target(hou, path):
    if (not _valid_text(path, 1024) or not path.startswith("/")
            or any(ord(c) < 32 or c in "\\*?[]{}" for c in path)
            or (path != "/" and any(p in ("", ".", "..") for p in path[1:].split("/")))):
        raise PythonActionError("Use literal absolute existing node paths in target_paths.")
    node = hou.node(path)
    if node is None:
        raise PythonActionError("A declared target no longer exists; inspect the scene before proposing.", "python_target_unavailable")
    return _identity(node)


def prepare(hou, arguments):
    """Compile without execution and capture existing target/session identities."""
    scene.require_main_thread(hou)
    if type(arguments) is not dict or set(arguments) != {"summary", "code", "target_paths"}:
        raise PythonActionError("A Python proposal requires summary, code and target_paths only.")
    if not _valid_text(arguments["summary"], 1024) or not arguments["summary"].strip():
        raise PythonActionError("Supply a concise action summary of at most 1024 characters.")
    source = arguments["code"]
    if (not _valid_text(source, MAX_CODE_CHARS) or not source.strip()
            or len(source.encode("utf-8")) > MAX_CODE_CHARS):
        raise PythonActionError("Supply Python code of at most 24 KiB UTF-8 bytes.")
    paths = arguments["target_paths"]
    if type(paths) is not list or len(paths) > MAX_TARGETS or any(type(p) is not str for p in paths) or len(set(paths)) != len(paths):
        raise PythonActionError("Use up to 32 distinct existing target_paths; use a parent network for creation.")
    filename = "<houlm-reviewed-" + uuid.uuid4().hex + ">"
    compiled = _validate_source(source, filename)
    try:
        targets = tuple(_target(hou, path) for path in paths)
        session, selection = _session(hou), _selection(hou)
    except PythonActionError:
        raise
    except Exception:
        raise PythonActionError("The scene identity could not be read; inspect and prepare a fresh proposal.", "python_target_unavailable") from None
    summary = (arguments["summary"] + "\n\nTargets: " + (", ".join(paths) or "Session-wide action")
               + "\n\nPython runs in this Houdini session. Supported edits are grouped for Undo; "
               "callbacks, file writes and other side effects may not be undoable. "
               "Cancellation is cooperative; a native operation may block until it returns."
               + "\n\nExact Python source to run:\n\n" + source)
    return _Plan(hou, source, compiled, filename, targets, session, selection, summary)


def _clean_text(value, maximum):
    was_long = len(value) > maximum
    value = value[:maximum].encode("utf-8", errors="replace").decode("utf-8")
    if _BINARY_TEXT.search(value) or _SECRET_TEXT.search(value):
        return _BLOCKED_TEXT
    return value + "...[truncated]" if was_long else value


def _json_value(value, state, depth=0):
    state["items"] += 1
    if state["items"] > _MAX_ITEMS or depth > 8:
        state["truncated"] = True
        return "[result truncated]"
    if value is None or type(value) in (bool, int):
        if type(value) is int and value.bit_length() > 256:
            state["truncated"] = True
            return "[integer too large]"
        return value
    if type(value) is float:
        return value if math.isfinite(value) else "[non-finite number omitted]"
    if type(value) is str:
        text = _clean_text(value, MAX_RESULT_STRING)
        state["truncated"] |= text != value
        return text
    if type(value) in (bytes, bytearray, memoryview):
        state["truncated"] = True
        return "[binary data omitted]"
    if type(value) in (list, tuple):
        result = []
        for item in value:
            if state["items"] >= _MAX_ITEMS:
                state["truncated"] = True
                result.append("[result truncated]")
                break
            result.append(_json_value(item, state, depth + 1))
        return result
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            if state["items"] >= _MAX_ITEMS:
                state["truncated"] = True
                break
            if type(key) is not str or len(key) > 256:
                state["truncated"] = True
                continue
            safe_key = _clean_text(key, 256)
            if _SECRET_KEY.fullmatch(key):
                state["truncated"] = True
                result[safe_key] = _BLOCKED_TEXT
            else:
                result[safe_key] = _json_value(item, state, depth + 1)
        return result
    state["truncated"] = True
    return "[non-JSON object omitted]"


class _Output:
    def __init__(self):
        self.parts, self.count, self.truncated = [], 0, False

    def print(self, *values, sep=" ", end="\n", file=None, flush=False):
        if file is not None or type(sep) is not str or type(end) is not str:
            raise ValueError("print supports text output only; file output is unavailable.")
        if self.count >= MAX_STDOUT_CHARS:
            self.truncated = True
            return
        parts, length = [], 0
        for value in values[:_MAX_ITEMS]:
            state = {"items": 0, "truncated": False}
            safe = _json_value(value, state)
            text = safe if type(safe) is str else json.dumps(safe, ensure_ascii=True)
            parts.append(text[:MAX_STDOUT_CHARS])
            length += len(text)
            self.truncated |= state["truncated"]
            if length > MAX_STDOUT_CHARS:
                self.truncated = True
                break
        self.truncated |= len(values) > _MAX_ITEMS
        text = _clean_text(sep[:MAX_STDOUT_CHARS].join(parts) + end[:MAX_STDOUT_CHARS], MAX_STDOUT_CHARS)
        available = MAX_STDOUT_CHARS - self.count
        self.truncated |= len(text) > available
        text = text[:available]
        self.parts.append(text)
        self.count += len(text)

    def text(self):
        # Recheck the concatenation to catch encoded data split across prints.
        return _clean_text("".join(self.parts), MAX_STDOUT_CHARS)


def _namespace(hou, output, checkpoint):
    def controlled_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or type(name) is not str or name.split(".")[0] not in _IMPORTS:
            raise ImportError("This module is not available in Houdini scene actions.")
        if any(not _public_name(part) for part in name.split(".")):
            raise ImportError("Private module imports are unavailable.")
        if name == "hou":
            return hou
        return builtins.__import__(name, globals, locals, fromlist, 0)

    def safe_getattr(obj, name, *default):
        _check_attribute(name)
        return getattr(obj, name, *default)

    def safe_hasattr(obj, name):
        _check_attribute(name)
        return hasattr(obj, name)

    names = ("abs all any bin bool callable chr dict divmod enumerate filter float format frozenset "
             "hash hex int isinstance issubclass iter len list map max min next oct ord pow range "
             "reversed round set slice sorted str sum tuple type zip "
             "Exception RuntimeError ValueError TypeError KeyError IndexError AttributeError "
             "AssertionError StopIteration NotImplementedError ZeroDivisionError").split()
    safe = {name: getattr(builtins, name) for name in names}
    safe.update({"__import__": controlled_import, "__build_class__": builtins.__build_class__,
                 "getattr": safe_getattr, "hasattr": safe_hasattr, "print": output.print})
    return {"__builtins__": safe, "__name__": "houlm_reviewed_action", "hou": hou,
            "result": None, "checkpoint": checkpoint}


def _failure(error, filename):
    code = getattr(error, "code", "python_execution_failed") if isinstance(error, (_Stopped, PythonActionError)) else "python_execution_failed"
    name = type(error).__name__
    if name == "OperationInterrupted":
        code = "python_interrupted"
    message = _clean_text(str(error), 1800)
    # HOM or import exceptions may include implementation/filesystem paths.
    message = re.sub(r"[A-Za-z]:[\\/][^\r\n\"']+", "[local path omitted]", message)
    message = re.sub(r"(?<![\w/])/(?:Users|home|tmp|var|usr|opt|mnt)/[^\s\"']+", "[local path omitted]", message)
    line = None
    tb = error.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == filename:
            line = tb.tb_lineno
        tb = tb.tb_next
    return {"code": code, "exception_type": name[:128], "message": message, "code_line": line}


def _bounded(result):
    if len(json.dumps(result, ensure_ascii=True).encode("utf-8")) > MAX_OUTPUT_BYTES:
        result["result"] = "[result omitted: output size limit]"
        result["stdout"] = _clean_text(result.get("stdout", ""), 2000)
        result["output_truncated"] = True
    # Long Unicode target paths alone can exceed the serialized result budget.
    # Do not shorten a path into a different valid-looking node identity.
    while len(json.dumps(result, ensure_ascii=True).encode("utf-8")) > MAX_OUTPUT_BYTES and result.get("target_paths"):
        result["target_paths"].pop()
        result["target_paths_truncated"] = True
        result["output_truncated"] = True
    return result


def apply(hou, plan, *, cancelled=None):
    """Execute a reviewed plan once. Failures never invoke a global Undo.

    `cancelled` is a host callback, not part of the model's execution namespace.
    The time/event budgets interrupt generated Python at trace checkpoints;
    they cannot forcibly terminate a native HOM call or callback.
    """
    scene.require_main_thread(hou)
    if not isinstance(plan, _Plan) or plan._hou is not hou:
        raise PythonActionError("Use a plan prepared in this Houdini session.")
    if plan._used:
        raise PythonActionError("This Python plan was already used; prepare a fresh proposal.", "python_plan_already_used")
    plan._used = True
    try:
        stale = (_session(hou) != plan._session or _selection(hou) != plan._selection
                 or any(_target(hou, identity[0]) != identity for identity in plan._targets))
    except Exception:
        stale = True
    if stale:
        return {"success": False, "status": "stale", "execution_started": False,
                "rollback": "not_needed", "manual_undo_needed": False,
                "message": "The session, selection or a target changed after review. Inspect and prepare a fresh proposal."}
    if cancelled is not None and cancelled():
        return {"success": False, "status": "cancelled", "execution_started": False,
                "rollback": "not_needed", "manual_undo_needed": False,
                "message": "The action was cancelled before execution."}
    output = _Output()
    started, events, last_progress = time.monotonic(), 0, 0.0
    operation = None

    def checkpoint():
        nonlocal last_progress
        if cancelled is not None and cancelled():
            raise _Stopped("python_cancelled", "The action was cancelled. Earlier changes may remain.")
        now = time.monotonic()
        if now - started > MAX_SECONDS or events > MAX_TRACE_EVENTS:
            raise _Stopped("python_budget_exceeded", "The action exceeded its cooperative execution budget. Earlier changes may remain; use smaller steps.")
        if operation is not None and now - last_progress >= 0.1:
            last_progress = now
            operation.updateProgress()

    def trace(frame, event, arg):
        nonlocal events
        if frame.f_code.co_filename == plan._filename:
            events += 1
            if event in ("line", "call"):
                checkpoint()
            return trace
        return None

    namespace = _namespace(hou, output, checkpoint)
    previous_trace = sys.gettrace()
    execution_started, error, undo_recorded = False, None, False
    label = "Hou-lm Python: " + plan._filename[16:28]
    try:
        interrupt_type = getattr(hou, "InterruptableOperation", None)
        interrupt = interrupt_type("Hou-lm reviewed Python", open_interrupt_dialog=True) if interrupt_type else nullcontext()
        with hou.undos.group(label):
            with interrupt as operation:
                checkpoint()
                sys.settrace(trace)
                execution_started = True
                try:
                    exec(plan._compiled, namespace, namespace)
                    checkpoint()
                finally:
                    sys.settrace(previous_trace)
    except BaseException as caught:
        sys.settrace(previous_trace)
        error = _failure(caught, plan._filename)
    try:
        labels = getattr(hou.undos, "undoLabels", lambda: ())()
        undo_recorded = bool(labels and labels[0] == label)
    except Exception:
        pass
    state = {"items": 0, "truncated": False}
    returned = _json_value(namespace.get("result"), state)
    outcome = {"status": "failed" if error else "applied", "success": error is None,
               "execution_started": execution_started,
               "rollback": "not_attempted" if error and execution_started else "not_needed",
               "manual_undo_needed": bool(error and execution_started),
               "completed_operations": int(execution_started and error is None),
               "target_paths": [identity[0] for identity in plan._targets],
               "undo_group": label, "undo_recorded": undo_recorded,
               "external_side_effects_covered": False,
               "result": returned, "stdout": output.text(),
               "output_truncated": state["truncated"] or output.truncated,
               "message": ("Python execution failed. Earlier scene changes and external side effects may remain; inspect the scene and Undo history before correcting it."
                           if error and execution_started else "Python could not start; no action code was executed."
                           if error else "Reviewed Python completed. Inspect the intended scene result; Undo covers only operations Houdini records.")}
    if error:
        outcome["error"] = error
    return _bounded(outcome)
