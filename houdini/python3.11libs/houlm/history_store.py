"""Lossless local JSON chat sidecars. No Houdini, Qt, database or network imports.

Each saved HIP owns ``.houlm/<HIP filename>.json``. Callers copy the complete
document to a new sidecar after Save As, and keep the old sidecar unchanged.
This module never executes saved content or resumes an interrupted action.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid


SCHEMA_VERSION = 1
_ERRORS = {
    "missing": "No conversation sidecar exists for this scene yet.",
    "invalid": "The conversation sidecar is invalid or uses an unsupported format. It was not overwritten.",
    "conflict": "The conversation sidecar changed in another session. Your in-memory history was kept; the file was not overwritten.",
    "locked": "Another session is saving this conversation sidecar. Your in-memory history was kept.",
    "read_failed": "The conversation sidecar could not be read. Your in-memory history was kept.",
    "write_failed": "The conversation sidecar could not be saved. Your in-memory history was kept.",
}


class StoreError(ValueError):
    """A fixed error code/message without saved prompts, paths or credentials."""

    def __init__(self, code):
        self.code = code if code in _ERRORS else "invalid"
        super().__init__(_ERRORS[self.code])


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def fresh_document():
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "active_conversation_id": None,
        "conversations": [],
    }


def new_conversation(document, server="", model=""):
    """Archive previous chats in place and return a new active mutable chat."""
    _validate(document)
    if not isinstance(server, str) or not isinstance(model, str):
        raise StoreError("invalid")
    conversation = {
        "id": str(uuid.uuid4()),
        "created_at": utc_now(),
        "server": server,
        "model": model,
        "messages": [],
        "transcript": [],
        "actions": [],
        "scene_context": {},
        "draft": "",
    }
    document["conversations"].append(conversation)
    document["active_conversation_id"] = conversation["id"]
    return conversation


def sidecar_path(hip_path):
    """Return a sidecar path for an absolute saved HIP path, without I/O."""
    if not isinstance(hip_path, (str, os.PathLike)) or not str(hip_path):
        raise StoreError("invalid")
    path = Path(hip_path)
    if not path.is_absolute() or path.suffix.lower() not in (".hip", ".hiplc", ".hipnc"):
        raise StoreError("invalid")
    return path.parent / ".houlm" / (path.name + ".json")


def _validate(document):
    if (type(document) is not dict
            or type(document.get("schema_version")) is not int
            or document["schema_version"] != SCHEMA_VERSION
            or type(document.get("conversations")) is not list):
        raise StoreError("invalid")
    identities = set()
    for conversation in document["conversations"]:
        if type(conversation) is not dict:
            raise StoreError("invalid")
        identity = conversation.get("id")
        if not isinstance(identity, str) or not identity or identity in identities:
            raise StoreError("invalid")
        identities.add(identity)
        for field in ("server", "model", "draft"):
            if not isinstance(conversation.get(field), str):
                raise StoreError("invalid")
        if (type(conversation.get("messages")) is not list
                or type(conversation.get("transcript")) is not list
                or type(conversation.get("actions")) is not list
                or type(conversation.get("scene_context")) is not dict):
            raise StoreError("invalid")
        if any(type(part) is not str for part in conversation["transcript"]):
            raise StoreError("invalid")
        commands = conversation.get("commands", [])
        if type(commands) is not list:
            raise StoreError("invalid")
        for command in commands:
            if (type(command) is not dict or any(type(command.get(field)) is not str
                                                for field in ("id", "title", "source", "status"))
                    or type(command.get("result_text", "")) is not str):
                raise StoreError("invalid")
        for message in conversation["messages"]:
            if (type(message) is not dict or message.get("role") not in ("user", "assistant", "tool")
                    or ("content" in message and message["content"] is not None
                        and type(message["content"]) is not str)):
                raise StoreError("invalid")
        for action in conversation["actions"]:
            if type(action) is not dict or ("status" in action and type(action["status"]) is not str):
                raise StoreError("invalid")
    active = document.get("active_conversation_id")
    if active is not None and (not isinstance(active, str) or active not in identities):
        raise StoreError("invalid")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StoreError("invalid")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise StoreError("invalid")


def _decode(raw):
    try:
        document = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                              parse_constant=_invalid_constant)
        _validate(document)
        return document
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise StoreError("invalid") from exc


def _revision(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    """Read a document losslessly; return its optimistic-concurrency token."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError as exc:
        raise StoreError("missing") from exc
    except OSError as exc:
        raise StoreError("read_failed") from exc
    return _decode(raw), _revision(raw)


def recover_interrupted(document):
    """Mark unfinished executions unknown; never infer success or replay code.

    Call after loading, not for a live document whose action is still running.
    Returning a count lets the panel display a recovery notice.
    """
    _validate(document)
    recovered = 0
    for conversation in document["conversations"]:
        for action in conversation["actions"]:
            if action.get("status") == "running":
                action["status"] = "unknown"
                action["recovery_note"] = "Execution was interrupted. Inspect the scene before continuing; this action was not replayed."
                recovered += 1
    return recovered


@contextmanager
def _write_lock(path):
    """Hold a nonblocking OS lock; leave the lock inode in place for all writers.

    OS locks are released on crashes. Deleting lock files would allow different
    writers to lock different inodes and defeats cross-process exclusion.
    """
    handle = None
    locked = False
    try:
        handle = open(str(path) + ".lock", "a+b")
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise StoreError("locked") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StoreError("locked") from exc
        locked = True
        yield
    finally:
        if handle is not None:
            try:
                if locked:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def save(path, document, expected_revision):
    """Atomically save a complete document if its previous revision still matches.

    ``None`` means the destination must not exist, including on Save As. There
    is intentionally no force-overwrite option. Errors leave the old file and
    the caller's in-memory document intact.
    """
    _validate(document)
    if expected_revision is not None and not isinstance(expected_revision, str):
        raise StoreError("invalid")
    try:
        raw = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise StoreError("invalid") from exc
    destination = Path(path)
    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock(destination):
            try:
                previous = destination.read_bytes()
            except FileNotFoundError:
                previous = None
            if previous is not None:
                _decode(previous)  # A broken file is never silently replaced.
            actual = None if previous is None else _revision(previous)
            if actual != expected_revision:
                raise StoreError("conflict")
            descriptor, temporary = tempfile.mkstemp(prefix="." + destination.name + ".", suffix=".tmp",
                                                      dir=str(destination.parent))
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            temporary = None
        return _revision(raw)
    except StoreError:
        raise
    except OSError as exc:
        raise StoreError("write_failed") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
