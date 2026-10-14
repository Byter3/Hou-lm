"""HIP-associated conversations. Local JSON only; never executes saved content."""
import copy

from . import history_store


def _install_memory_invalidator(hou_module):
    """Discard old scene memory even if its panel has already been closed.

    This process-local callback deliberately outlives panel/module reloads. It
    references no Qt object, conversation instance or saved scene content.
    Keeping its registration on ``hou`` avoids installing duplicate callbacks
    when a HIP supplies different ``hou.session`` source code.
    """
    previous = getattr(hou_module, "_houlm_conversation_memory_invalidator", None)
    if previous is not None and getattr(hou_module, "_houlm_conversation_callback_version", 0) == 2:
        return
    try:
        if previous is not None:
            hou_module.hipFile.removeEventCallback(previous)
        events = hou_module.hipFileEventType
        after_events = (events.AfterLoad, events.AfterClear)
        state = {"epoch": 0, "cleared": False, "saving": False, "confirmed_path": None}
        hou_module._houlm_conversation_scene_state = state

        def invalidate(event):
            if event in after_events:
                state["epoch"] += 1
                state["cleared"] = event == events.AfterClear
                state["saving"] = False
                state["confirmed_path"] = None if state["cleared"] else _hip_path(hou_module)
                session = getattr(hou_module, "session", None)
                if session is not None:
                    session._houlm_conversations = None
            elif event == events.BeforeSave:
                state["saving"] = True
            elif event == events.AfterSave:
                state.update(cleared=False, saving=False, confirmed_path=_hip_path(hou_module))

        hou_module.hipFile.addEventCallback(invalidate)
        hou_module._houlm_conversation_memory_invalidator = invalidate
        hou_module._houlm_conversation_callback_version = 2
    except (AttributeError, RuntimeError):
        pass


def _hip_path(hou_module):
    try:
        return hou_module.hipFile.path()
    except (AttributeError, OSError, RuntimeError):
        return None


class Conversations:
    def __init__(self, hou_module):
        self.hou = hou_module
        self.document = history_store.fresh_document()
        self.path = None
        self.revision = None
        self.error = ""
        self.error_code = ""
        self.blocked = False
        self._callback = None
        _install_memory_invalidator(hou_module)
        self.open_current()

    @property
    def active(self):
        identifier = self.document.get("active_conversation_id")
        for item in self.document["conversations"]:
            if item["id"] == identifier:
                return item
        return history_store.new_conversation(self.document)

    def current_path(self):
        self._path_error = ""
        try:
            hip_path = _hip_path(self.hou)
            if hip_path is None:
                self._path_error = "path_unavailable"
                return None
            destination = history_store.sidecar_path(hip_path)
            state = self._scene_state()
            if state.get("confirmed_path") == hip_path:
                return destination
            if state.get("cleared"):
                return None
            # A default untitled path can already exist on disk while this is
            # an unrelated, never-saved new scene. Only an observed Save/Load
            # above may override Houdini's new-scene flag, not file existence.
            if getattr(self.hou.hipFile, "isNewFile", lambda: True)():
                return None
            return destination
        except (OSError, RuntimeError, history_store.StoreError):
            self._path_error = "path_unavailable"
            return None

    def _scene_state(self):
        return getattr(self.hou, "_houlm_conversation_scene_state", {})

    @property
    def saved(self):
        """Whether the current destination has a successfully read/saved file."""
        return self.path is not None and self.revision is not None and not self.error

    def _set_error(self, code):
        self.error_code = code
        messages = {
            "path_unavailable": "Could not resolve this HIP's save location. Chat remains in memory.",
            "scene_changed": "The scene changed without this panel receiving its load event. Reopen the panel to load that scene's history; this chat remains in memory.",
            "unconfirmed_path": "The HIP filename changed without a completed Save event. Save the HIP to attach this chat to its new filename.",
            "conflict": "Chat file changed in another panel or process; this copy remains in memory. Reopen the HIP to load it.",
            "invalid": "Could not read this HIP's chat file. It will not be overwritten.",
            "read_failed": "Could not read this HIP's chat file. It will not be overwritten.",
            "write_failed": "Could not save chat beside this HIP. This copy remains in memory; check folder permissions.",
            "locked": "Another panel is saving this chat file. This copy remains in memory and will retry at the next checkpoint.",
        }
        self.error = messages.get(code, "")

    def _memory(self):
        session = getattr(self.hou, "session", None)
        if session is not None:
            session._houlm_conversations = {
                "path": str(self.path) if self.path else None,
                "document": copy.deepcopy(self.document),
                "revision": self.revision,
                "blocked": self.blocked,
                "error": self.error,
                "error_code": self.error_code,
                "epoch": self._epoch,
            }

    def open_current(self, *, use_memory=True):
        self.path = self.current_path()
        self._epoch = self._scene_state().get("epoch", 0)
        self.revision, self.blocked, self.error = None, False, ""
        self.error_code = ""
        cached = getattr(getattr(self.hou, "session", None), "_houlm_conversations", None)
        cached_same_scene = (isinstance(cached, dict)
                             and cached.get("epoch", self._epoch) == self._epoch)
        cached_same_path = cached_same_scene and cached.get("path") == (str(self.path) if self.path else None)
        saved_while_closed = (cached_same_scene and self.path is not None
                              and self._scene_state().get("confirmed_path") == _hip_path(self.hou))
        if (use_memory and isinstance(cached, dict)
                and (cached_same_path or saved_while_closed)):
            self.document = copy.deepcopy(cached["document"])
            if cached_same_path:
                self.revision = cached.get("revision")
                self.blocked = cached.get("blocked", False)
                self.error = cached.get("error", "")
                self.error_code = cached.get("error_code", "")
        elif self.path is not None:
            try:
                self.document, self.revision = history_store.load(self.path)
            except history_store.StoreError as error:
                self.document = history_store.fresh_document()
                if error.code != "missing":
                    self.blocked = True
                    self._set_error(error.code)
        else:
            self.document = history_store.fresh_document()
        if self._path_error:
            self._set_error(self._path_error)
        history_store.recover_interrupted(self.document)
        for conversation in self.document["conversations"]:
            if conversation.get("turn_state") in ("working", "review"):
                conversation["turn_state"] = "interrupted"
            for action in conversation.get("actions", []):
                if action.get("status") == "review":
                    action["status"] = "cancelled"
        self.active  # Ensure a fresh document has a conversation.
        self._memory()

    def save(self):
        state = self._scene_state()
        if state.get("epoch", 0) != self._epoch:
            # A missed panel callback must never associate the old conversation
            # with a different loaded/cleared scene. Preserve it on this object.
            self._set_error("scene_changed")
            return False
        if not state.get("saving"):
            destination = self.current_path()
            if self._path_error:
                self._set_error(self._path_error)
                self._memory()
                return False
            if destination != self.path:
                if destination is not None and state.get("confirmed_path") == _hip_path(self.hou):
                    # The process callback survives panel reloads and recovers
                    # a missed panel-level AfterSave notification.
                    self.path, self.revision = destination, None
                    self.blocked = False
                    self._set_error("")
                else:
                    self._set_error("unconfirmed_path")
                    self._memory()
                    return False
        self._memory()
        if self.path is None or self.blocked:
            return not self.blocked
        try:
            self.revision = history_store.save(self.path, self.document, self.revision)
            self._set_error("")
        except history_store.StoreError as error:
            self._set_error(error.code)
            if error.code in ("conflict", "invalid"):
                self.blocked = True
            self._memory()
            return False
        self._memory()
        return True

    def after_save(self):
        self._scene_state().update(cleared=False, saving=False, confirmed_path=_hip_path(self.hou))
        destination = self.current_path()
        if destination != self.path:
            # Save As forks the complete book; never overwrite a sidecar that
            # already belongs to the destination HIP.
            self.path, self.revision = destination, None
            self.blocked = False
            self._set_error("")
        return self.save()

    def new(self, server="", model=""):
        item = history_store.new_conversation(self.document, server=server, model=model)
        self._memory()
        return item

    def select(self, identifier):
        if any(item["id"] == identifier for item in self.document["conversations"]):
            self.document["active_conversation_id"] = identifier
            return True
        return False

    def listen(self, before_switch, after_switch, before_save, after_save):
        """Callbacks supplied by the panel use weak references to its QObject."""
        if self._callback is not None:
            return
        try:
            events = self.hou.hipFileEventType
            def callback(event):
                if event in (events.BeforeLoad, events.BeforeClear):
                    before_switch()
                elif event in (events.AfterLoad, events.AfterClear):
                    self.open_current(use_memory=False)
                    after_switch()
                elif event == events.BeforeSave:
                    before_save()
                elif event == events.AfterSave:
                    self.after_save()
                    after_save()
            self.hou.hipFile.addEventCallback(callback)
            self._callback = callback
        except (AttributeError, RuntimeError):
            pass

    def close(self):
        if self._callback is not None:
            try:
                self.hou.hipFile.removeEventCallback(self._callback)
            except (AttributeError, RuntimeError):
                pass
            self._callback = None


def request_window(messages, *, max_messages=240, max_bytes=320 * 1024):
    """Bound requests by whole user turns; the sidecar always keeps all turns.

    Do not split tool calls/results or alter provider continuation fields.
    If the current turn alone is too large the existing payload validator
    reports that error, rather than fabricating or losing its tool results.
    """
    import json
    start = 0
    boundaries = [index for index, item in enumerate(messages) if item.get("role") == "user"]
    for boundary in boundaries[1:]:
        recent = messages[start:]
        if len(recent) <= max_messages and len(json.dumps(recent, ensure_ascii=True).encode("utf-8")) <= max_bytes:
            break
        start = boundary
    return messages[start:], start
