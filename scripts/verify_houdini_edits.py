"""Opt-in native HOM regression check, run with hython in a fresh process.

Creates a disposable FLIP-related graph with automatic cooking disabled. No
scene is loaded/saved and no simulation, render or cache button is executed.
This checks authored edits and rollback, not a production FLIP recipe.
This developer fixture is never imported by the panel or exposed to the model;
artist requests must be planned through the generic scene tools at runtime.
"""
import json
import os
from pathlib import Path
import sys


def main():
    import hou

    if hou.isUIAvailable():
        raise RuntimeError("Run this check in a separate hython process, not the artist's Houdini UI.")
    if hou.node("/obj").children() or (hou.node("/stage") and hou.node("/stage").children()):
        raise RuntimeError("Run this check in a fresh empty hython process.")
    # Exercise the production main-thread guard without showing a window.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide2 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    hou.setUpdateMode(hou.updateMode.Manual)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
    from houlm import edits, inspection

    root = "/obj/HOULM_NATIVE_TEST"
    source, cache, dop = root + "/source", root + "/cache", root + "/sim"

    def create(parent, node_type, name):
        return {"op": "create_node", "parent": parent, "node_type": node_type, "name": name}

    def parameter(path, name, value):
        return {"op": "set_parameter", "path": path, "parameter": name, "value": value}

    def wire(path, source_path, index=0):
        return {"op": "connect_nodes", "path": path, "source": source_path, "input_index": index}

    def apply(operations):
        return edits.apply(hou, edits.prepare(hou, {"summary": "Native HOM regression check", "operations": operations}))

    result = apply([
        create("/obj", "geo", "HOULM_NATIVE_TEST"),
        create(root, "sphere", "emitter"),
        create(root, "flipsource", "source"),
        wire(source, root + "/emitter"),
        parameter(source, "initialize", "sourceflip"),
        parameter(source, "createparticles", True),
        parameter(source, "shell", True),
        parameter(source, "particlesep", 0.08),
        create(root, "dopnet", "sim"),
        create(dop, "flipobject", "fluid"),
        create(dop, "flipsolver::2.0", "solver"),
        wire(dop + "/solver", dop + "/fluid"),
        create(root, "filecache::2.0", "cache"),
        wire(cache, source),
        parameter(cache, "filemethod", "explicit"),
        parameter(cache, "file", "$HIP/cache/houlm.$F4.bgeo.sc"),
        parameter(cache, "cachesim", True),
    ])
    assert result["success"], result
    assert hou.node(source).parm("initialize").rawValue() == "sourceflip"
    assert hou.node(source).parm("createparticles").rawValue() == "on"
    assert hou.node(source).parm("shell").rawValue() == "on"
    assert hou.node(cache).parm("filemethod").rawValue() == "explicit"
    assert hou.node(cache).parm("file").rawValue() == "$HIP/cache/houlm.$F4.bgeo.sc"
    assert hou.node(cache).parm("f1").rawValue() == "$FSTART"
    assert hou.node(cache).parm("f2").rawValue() == "$FEND"
    assert hou.node(cache).inputs()[0] == hou.node(source)
    assert hou.node(dop + "/solver").inputs()[0] == hou.node(dop + "/fluid")
    print("PASS native FLIP Source, DOP nodes, File Cache creation/parameters/wiring", flush=True)

    # Invalid static names/values should fail before any node is created.
    for name, value, expected in (("not_a_parameter", 1, "parameter_not_found"),
                                  ("initialize", "FLIP Source", "invalid_menu_token"),
                                  ("createparticles", 2, "invalid_toggle_value")):
        try:
            edits.prepare(hou, {"summary": "Invalid future parameter", "operations": [
                create(root, "flipsource", "invalid"), parameter(root + "/invalid", name, value)]})
        except edits.EditError as error:
            assert error.details()["error"]["code"] == expected, error.details()
            assert error.details()["failed_operation"]["parameter"] == name
        else:
            raise AssertionError("Invalid static parameter passed preflight")
        assert hou.node(root + "/invalid") is None
    print("PASS native static parameter preflight", flush=True)

    # A new cache's actual frame expression must remain protected. Deliberately
    # request its replacement after other operations to exercise real rollback;
    # no HOM objects or executor functions are mocked.
    temp = root + "/temporary_cache"
    failure = apply([
        parameter(source, "initialize", "sink"),
        parameter(source, "createparticles", False),
        parameter(cache, "filemethod", "constructed"),
        wire(cache, root + "/emitter"),
        create(root, "filecache::2.0", "temporary_cache"),
        parameter(temp, "f1", 1),
    ])
    assert not failure["success"] and failure["rollback"] == "reverted_scene_fields", failure
    assert failure["manual_undo_needed"] is False
    assert failure["failed_operation"]["parameter"] == "f1"
    assert failure["error"]["code"] == "authored_channel_protected"
    assert failure["available_schema"]["captured_before_rollback"] is True
    assert any(p["name"] == "f1" for p in failure["available_schema"]["parameters"])
    assert hou.node(temp) is None
    assert hou.node(source).parm("initialize").rawValue() == "sourceflip"
    assert hou.node(source).parm("createparticles").rawValue() == "on"
    assert hou.node(cache).parm("filemethod").rawValue() == "explicit"
    assert hou.node(cache).inputs()[0] == hou.node(source)
    print("PASS native menu/toggle/connection rollback and pre-rollback schema capture", flush=True)

    # Metadata discovery is read-only, including a category with no DOP parent.
    static = inspection.execute(hou, "node_type_schema", {"category": "Sop", "node_type": "flipsource", "query": "initialize"})
    menu = static["parameters"][0]
    assert menu["menu_use_token"] is False
    assert any(item["token"] == "sourceflip" for item in menu["menu"])
    channel = inspection.execute(hou, "parameter_schema", {"path": cache, "query": "f1"})
    frame = next(item for item in channel["parameters"] if item["name"] == "f1")
    assert frame["editable"] is False
    dops = inspection.execute(hou, "list_node_types", {"category": "Dop", "query": "flipsolver"})
    assert any(item["name"] == "flipsolver::2.0" for item in dops["node_types"])
    print("PASS native menu/expression metadata and category discovery", flush=True)
    print(json.dumps({"houdini": hou.applicationVersionString(), "result": "passed",
                      "scope": "authored edits and rollback; no simulation cook or cache write"}), flush=True)


if __name__ == "__main__":
    main()
