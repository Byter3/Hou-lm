"""Reload assistant children without rebuilding Houdini's native Python Panel."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "houdini" / "python3.11libs"
if str(MODULES) not in sys.path:
    sys.path.insert(0, str(MODULES))

# Never reload panel_host: Houdini retains instances of its stable root class.
RELOAD_MODULES = (
    "houlm.stream_diagnostics", "houlm.vision", "houlm.core", "houlm.credentials", "houlm.scene",
    "houlm.inspection", "houlm.edits", "houlm.discovery", "houlm.homhelp",
    "houlm.python_actions", "houlm.toolchat", "houlm.activity", "houlm.node_paths", "houlm.chat_format",
    "houlm.history_store", "houlm.conversations", "houlm.context_index", "houlm.request_context",
    "houlm.node_ui", "houlm.vision_ui", "houlm.change_review", "houlm.transcript", "houlm.panel",
)


def _notice(message):
    import hou
    print(message)
    hou.ui.setStatusMessage(message)


def _preserve_active_login(previous):
    if previous is None or getattr(previous, "_connected", False) is not True:
        return True
    from houlm import core, credentials
    try:
        server = getattr(previous, "_server", None)
        if not isinstance(server, str) or not server:
            raise ValueError()
        server = core.normalize_server_url(server)
        if core.normalize_server_url(previous.server_url.text()) != server:
            raise ValueError()
        key = previous.api_key.text()
        if not isinstance(key, str) or not key.strip():
            raise ValueError()
        credentials.save_key(server, key.strip())
    except (ValueError, OSError, RuntimeError, AttributeError):
        _notice("Hou-lm: reload stopped because the active login could not be saved securely. The current panel remains open.")
        return False
    return True


def _complete_messages(messages):
    """A resumable history never contains an unanswered or orphaned tool call."""
    if not isinstance(messages, list):
        return False
    pending = set()
    used = set()
    for message in messages:
        if not isinstance(message, dict):
            return False
        role = message.get("role")
        if role == "tool":
            identifier = message.get("tool_call_id")
            if not isinstance(identifier, str) or identifier not in pending:
                return False
            pending.remove(identifier)
        elif role in ("user", "assistant"):
            if pending:
                return False
            calls = message.get("tool_calls", [])
            if not isinstance(calls, list) or (calls and role != "assistant"):
                return False
            for call in calls:
                if not isinstance(call, dict):
                    return False
                identifier = call.get("id")
                if not isinstance(identifier, str) or not identifier or identifier in used:
                    return False
                pending.add(identifier)
                used.add(identifier)
        else:
            return False
    return not pending


def _capture_legacy_conversation(previous):
    """Copy old in-memory chat before disconnect clears it. Never copy keys."""
    if previous is None or hasattr(previous, "_history"):
        return None
    committed = getattr(previous, "_messages", [])
    working = getattr(previous, "_agent_history", None)
    messages = working if working is not None and _complete_messages(working) else committed
    if not _complete_messages(messages):
        raise ValueError("The previous panel has an incomplete saved conversation.")
    transcript = getattr(getattr(previous, "transcript", None), "_parts", [])
    if not isinstance(transcript, list) or any(not isinstance(part, str) for part in transcript):
        raise ValueError("The previous panel transcript cannot be preserved.")
    prompt = getattr(previous, "prompt", None)
    draft = prompt.toPlainText() if prompt is not None else ""
    draft = draft or getattr(previous, "_pending_prompt", "")
    if not isinstance(draft, str):
        raise ValueError("The previous panel draft cannot be preserved.")
    if not messages and not transcript and not draft:
        return None
    from houlm import core
    server = getattr(previous, "_server", "")
    if not server:
        server_field = getattr(previous, "server_url", None)
        server = server_field.text() if server_field is not None else ""
    server = core.normalize_server_url(server) if server else ""
    model_field = getattr(previous, "models", None)
    model = model_field.currentData() if model_field is not None else ""
    model = model or getattr(previous, "_preferred_model", "") or ""
    if not isinstance(server, str) or not isinstance(model, str):
        raise ValueError("The previous panel connection context cannot be preserved.")
    scene_field = getattr(previous, "scene_tools", None)
    scene_access = scene_field.isChecked() if scene_field is not None else True
    if not isinstance(scene_access, bool):
        raise ValueError("The previous panel scene access setting cannot be preserved.")
    interrupted = (working is not None or getattr(previous, "_reply", None) is not None
                   or getattr(previous, "_pending_review", None) is not None
                   or getattr(previous, "_pending_messages", None) is not None)
    snapshot = {"messages": messages, "transcript": transcript, "draft": draft,
                "server": server, "model": model, "scene_access": scene_access,
                "turn_state": "interrupted" if interrupted else "idle"}
    # Independent JSON primitives survive replacement of all module globals.
    # Refuse non-JSON values instead of retiring a panel with unpreserved data.
    return json.loads(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))


def _legacy_scene_identity(hou):
    """Bind recovery chat to this HIP incarnation, including same-path loads.

    Older panels have no conversation event epoch. This tiny process-local
    listener therefore retains its own counter and no references to widgets.
    It survives module reload without evaluating scene content or changing it.
    """
    hip = getattr(hou, "hipFile", None)
    path = hip.path() if hip is not None else None
    if path is not None and not isinstance(path, str):
        raise ValueError("The current HIP location is unavailable.")
    state = getattr(hou, "_houlm_legacy_reload_scene_state", None)
    if not isinstance(state, dict):
        state = {"epoch": 0}
        try:
            event_types = hou.hipFileEventType
            switches = (event_types.BeforeLoad, event_types.BeforeClear)

            def scene_switch(event):
                if event in switches:
                    state["epoch"] += 1

            hip.addEventCallback(scene_switch)
            hou._houlm_legacy_reload_scene_callback = scene_switch
        except AttributeError:
            pass  # Minimal test doubles may have no HIP event API.
        # Do not latch an unregistered counter after a real HOM registration
        # failure; leave capture blocked so a later retry can register again.
        hou._houlm_legacy_reload_scene_state = state
    return {"path": path, "session": id(getattr(hou, "session", None)), "epoch": state["epoch"]}


def _reload_on_ui_thread():
    import hou
    import importlib
    import runpy
    import shiboken2
    from PySide2 import QtCore, QtWidgets

    try:
        app = QtWidgets.QApplication.instance()
        if app is None or QtCore.QThread.currentThread() != app.thread():
            raise RuntimeError("Hou-lm reload must run on Houdini's UI thread.")
        launcher = runpy.run_path(str(ROOT / "scripts" / "launch_houdini.py"))
        tabs = launcher["_assistant_tabs"](hou)
        hosts = [tab.activeInterfaceRootWidget() for tab in tabs]
        if not hosts:
            _notice("Hou-lm: no Assistant pane is open. Open New Pane Tab Type > Hou-lm Assistant from the pane + menu, then run reload again.")
            hou.session._hou_lm_reload_pending = False
            return
        if any(host is None or not shiboken2.isValid(host)
               or getattr(type(host), "_houlm_host_version", None) != 1 for host in hosts):
            _notice("Hou-lm: this session needs a clean restart to load the updated panel host. Save your work, restart Houdini, run launch_houdini.py, then choose Hou-lm Assistant from the pane + menu. No panes were rebuilt.")
            hou.session._hou_lm_reload_pending = False
            return
        if any(getattr(host, "_closed", False) or getattr(host, "_retiring", False) for host in hosts):
            _notice("Hou-lm: a panel is closing or already reloading. No update was started.")
            hou.session._hou_lm_reload_pending = False
            return
        if not all(_preserve_active_login(host.assistant_widget()) for host in hosts):
            hou.session._hou_lm_reload_pending = False
            return

        snapshots = dict(getattr(hou, "_houlm_legacy_reload_snapshots", {}))
        try:
            for host in hosts:
                identifier = str(id(host))
                if identifier in snapshots:
                    if snapshots[identifier].get("scene") != _legacy_scene_identity(hou):
                        _notice("Hou-lm: reload stopped because the retained chat belongs to an earlier HIP session. The backup remains in memory and the current panel is unchanged. Do not restart Houdini until that chat has been recovered.")
                        hou.session._hou_lm_reload_pending = False
                        return
                else:
                    snapshot = _capture_legacy_conversation(host.assistant_widget())
                    if snapshot is not None:
                        snapshots[identifier] = {"conversation": snapshot, "scene": _legacy_scene_identity(hou)}
        except Exception:
            _notice("Hou-lm: reload stopped because the current chat could not be preserved. Copy the conversation and draft before restarting; the current panel remains open.")
            hou.session._hou_lm_reload_pending = False
            return
        # Survive a failed module reload/import until the user retries. This is
        # process memory only; it cannot execute any request or saved command.
        hou._houlm_legacy_reload_snapshots = snapshots

        state = {"waiting": set(range(len(hosts))), "survivors": set(), "failed": False, "finished": False}
        destruction_handlers = []

        def finish():
            try:
                for host, handler in destruction_handlers:
                    if shiboken2.isValid(host):
                        host.destroyed.disconnect(handler)
                active = [hosts[index] for index in sorted(state["survivors"])
                          if shiboken2.isValid(hosts[index]) and not hosts[index]._closed]
                if state["failed"]:
                    raise RuntimeError("A child could not be retired.")
                if not active:
                    _notice("Hou-lm: reload cancelled because its panes were closed.")
                    return
                # Child.destroyed plus this next Qt turn ensures no old widget
                # methods/signals run while module globals are replaced.
                importlib.invalidate_caches()
                for name in RELOAD_MODULES:
                    if name in sys.modules:
                        importlib.reload(sys.modules[name])
                # A HIP switch may have happened while Qt destroyed old widgets.
                # Never restore the captured chat into a different scene.
                if any(snapshots.get(str(id(host))) is not None
                       and snapshots[str(id(host))].get("scene") != _legacy_scene_identity(hou)
                       for host in active):
                    raise RuntimeError("The HIP session changed during panel reload.")
                for host in active:
                    host.install_assistant()
                    identifier = str(id(host))
                    backup = snapshots.get(identifier)
                    if backup is not None:
                        host.assistant_widget()._import_legacy_conversation(backup["conversation"])
                        snapshots.pop(identifier)
                _notice("Hou-lm: assistant reloaded in place. Saved login and HIP conversation are retained.")
            except Exception:
                for host in hosts:
                    if shiboken2.isValid(host):
                        host.show_reload_failure()
                if snapshots:
                    _notice("Hou-lm: the update could not restore its chat. The captured conversation remains in memory. Retry in the same HIP session; if the HIP changed, keep Houdini open until the retained chat has been recovered. No native panes were rebuilt.")
                else:
                    _notice("Hou-lm: the update could not load. Save your work and restart Houdini. No native panes were rebuilt.")
            finally:
                hou.session._hou_lm_reload_pending = False

        def retired(index, alive):
            if state["finished"] or index not in state["waiting"]:
                return
            state["waiting"].remove(index)
            if alive:
                state["survivors"].add(index)
            if not state["waiting"]:
                state["finished"] = True
                QtCore.QTimer.singleShot(0, finish)

        for index, host in enumerate(hosts):
            handler = lambda *unused, index=index: retired(index, False)
            host.destroyed.connect(handler)
            destruction_handlers.append((host, handler))
        for index, host in enumerate(hosts):
            try:
                host.retire_assistant(lambda alive, index=index: retired(index, alive))
            except Exception:
                state["failed"] = True
                retired(index, False)
    except Exception:
        hou.session._hou_lm_reload_pending = False
        raise


def reload_panel():
    import hou
    if not hou.isUIAvailable():
        raise RuntimeError("Open Houdini's graphical application before reloading the panel.")
    import hdefereval
    if getattr(hou.session, "_hou_lm_reload_pending", False):
        return
    hou.session._hou_lm_reload_pending = True
    try:
        hdefereval.executeDeferred(_reload_on_ui_thread)
    except Exception:
        hou.session._hou_lm_reload_pending = False
        raise
    print("Hou-lm: assistant reload queued; the native pane will not be rebuilt.")


if __name__ == "__main__":
    reload_panel()
