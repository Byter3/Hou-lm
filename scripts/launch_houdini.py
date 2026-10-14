"""Register/focus Hou-lm without initializing Houdini's default Python Panel."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "houdini" / "python3.11libs"
PANEL_FILE = ROOT / "houdini" / "python_panels" / "hou_lm.pypanel"
INTERFACE_NAME = "hou_lm"
if str(MODULES) not in sys.path:
    sys.path.insert(0, str(MODULES))


def _assistant_tabs(hou):
    """Find live native tabs; never trust a saved PaneTab across desktop changes."""
    result = []
    for tab in hou.ui.paneTabs():
        if tab.type() == hou.paneTabType.PythonPanel:
            interface = tab.activeInterface()
            if interface is not None and interface.name() == INTERFACE_NAME:
                result.append(tab)
    return result


def _launch_on_ui_thread():
    # Houdini's Python Shell is a worker thread. Import the UI and create every
    # Qt object only after Houdini dispatches this callback on the UI thread.
    import hou
    from PySide2 import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None or QtCore.QThread.currentThread() != app.thread():
        raise RuntimeError("Hou-lm panel creation must run on Houdini's UI thread.")

    tabs = _assistant_tabs(hou)
    # A pane the artist has deliberately floated remains a native Houdini pane.
    # Prefer an embedded tab, but do not discard an existing conversation.
    if tabs:
        tab = next((item for item in tabs if not item.isFloating()), tabs[0])
        tab.setIsCurrentTab()
        return tab

    interface = hou.pypanel.interfaceByName(INTERFACE_NAME)
    if interface is None:
        hou.pypanel.installFile(str(PANEL_FILE))
        interface = hou.pypanel.interfaceByName(INTERFACE_NAME)
    if interface is None:
        raise RuntimeError("Houdini did not register the Hou-lm Python Panel interface.")
    # Houdini 20.5 createTab(PythonPanel) first opens its default interface,
    # which can initialize the QtWebEngine Quick Start page and crash before
    # setActiveInterface can run. Its native + menu creates the named interface
    # directly. Do not replace this with generic tab creation or a floating
    # fallback, and do not reinstall a definition already used by live panes.
    message = (
        "Hou-lm is registered. To open it, click + beside the pane tabs, then "
        "New Pane Tab Type > Hou-lm Assistant (near the bottom). "
        "Choose that named entry, not Misc > Python Panel."
    )
    hou.ui.setStatusMessage(message)
    print(message)
    return None


def launch():
    import hou
    if not hou.isUIAvailable():
        raise RuntimeError("Open Houdini's graphical application before launching the panel.")
    import hdefereval

    # Never wait here: the shell must return so Houdini can process idle events.
    hdefereval.executeDeferred(_launch_on_ui_thread)
    print("Hou-lm: panel registration/focus queued on Houdini's UI thread.")


if __name__ == "__main__":
    launch()
