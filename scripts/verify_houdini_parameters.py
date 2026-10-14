"""Opt-in generic parameter regression fixture for a fresh, empty hython.

Never loaded by the panel or exposed as a model tool. Tests the edit executor,
not model planning. Does not load/save a scene or cook geometry.
"""
import json
import os
from pathlib import Path
import sys
from unittest import mock


def main():
    import hou

    if hou.isUIAvailable() or hou.node("/obj").children() or hou.node("/stage").children():
        raise RuntimeError("Run in a fresh empty hython process, outside the artist's scene.")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide2 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    hou.setUpdateMode(hou.updateMode.Manual)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
    from houlm import edits, inspection

    root = "/obj/HOULM_PARAMETER_TEST"
    path = root + "/controls"

    def add(name, kind="float", default=1.0, **options):
        return {"op": "add_spare_parameter", "path": path, "name": name,
                "label": name.title(), "parameter_type": kind, "default": default, **options}

    def set_value(name, value):
        return {"op": "set_parameter", "path": path, "parameter": name, "value": value}

    def plan(operations):
        return edits.prepare(hou, {"summary": "Generic native parameter regression", "operations": operations})

    def apply(operations):
        result = edits.apply(hou, plan(operations))
        assert result["success"], result
        return result

    apply([
        {"op": "create_node", "parent": "/obj", "node_type": "geo", "name": "HOULM_PARAMETER_TEST"},
        {"op": "create_node", "parent": root, "node_type": "attribwrangle", "name": "controls"},
        add("gain", min_value=0.0, max_value=2.0), add("fraction", default=1.0 / 3.0),
        add("iterations", "int", 4), add("note", "string", "artist control"),
        add("enabled", "toggle", True), add("direction", "vector", [0.0, 1.0, 0.0]),
        add("tint", "color", [0.2, 0.4, 0.8]),
        add("style", "menu", "smooth", menu_items=[
            {"token": "linear", "label": "Linear"}, {"token": "smooth", "label": "Smooth"}]),
        set_value("gain", 0.75), set_value("directionx", 2.0),
        set_value("snippet", '@P *= chf("gain");'),
    ])
    node = hou.node(path)
    assert node.parm("gain").rawValue() == "0.75"
    assert node.parm("iterations").rawValue() == "4"
    assert node.parm("enabled").rawValue() == "on"
    assert node.parm("style").rawValue() == "smooth"
    assert node.parmTuple("direction").parmTemplate().namingScheme() == hou.parmNamingScheme.XYZW
    assert node.parmTuple("direction").parmTemplate().look() == hou.parmLook.Vector
    assert node.parmTuple("tint").parmTemplate().namingScheme() == hou.parmNamingScheme.RGBA
    assert all(node.parm(name).isSpare() for name in ("gain", "note", "directionx", "tintr", "style"))
    print("PASS create node, add seven control types, assign component/value/VEX in one batch", flush=True)

    # Artist-authored channels must survive interface edits unchanged.
    node.parm("iterations").setExpression('$F + 1', hou.exprLanguage.Hscript)
    node.parm("directiony").setExpression('ch("gain")', hou.exprLanguage.Hscript)
    key = hou.Keyframe()
    key.setFrame(1)
    key.setValue(0.4)
    node.parm("tintb").setKeyframe(key)
    node.parm("note").lock(True)

    def channels():
        return {parm.name(): (parm.rawValue(), tuple(k.asCode() for k in parm.keyframes()), parm.isLocked())
                for parm in node.parms()}

    before = channels()
    apply([add("distance", default=3.0)])
    assert all(channels()[name] == value for name, value in before.items())
    assert node.parm("distance").isSpare()
    print("PASS preserves literals, VEX, expressions, reference, animation and locked channel", flush=True)

    # A strict limit must be reported before Apply rather than silently clamp.
    bounded = add("bounded", default=0.5, min_value=0.0, max_value=1.0,
                  min_strict=True, max_strict=True)
    try:
        plan([bounded, set_value("bounded", 2.0)])
    except edits.EditError as error:
        assert error.code == "parameter_value_out_of_range", error.details()
    else:
        raise AssertionError("Planned strict limit was ignored")
    assert node.parm("bounded") is None
    apply([bounded])
    try:
        plan([set_value("bounded", -1.0)])
    except edits.EditError as error:
        assert error.code == "parameter_value_out_of_range", error.details()
    else:
        raise AssertionError("Existing strict limit was ignored")
    print("PASS strict ranges checked for planned and instantiated controls", flush=True)

    for operation in (add("gain"), add("directionx"), add("tint", "float", 0.0)):
        try:
            plan([operation])
        except edits.EditError as error:
            assert error.code == "parameter_name_collision", error.details()
        else:
            raise AssertionError("Existing parameter name was accepted")
    pending = plan([add("pending")])
    changed_group = node.parmTemplateGroup()
    changed_group.append(hou.ToggleParmTemplate("artist_added", "Artist added", default_value=False))
    node.setParmTemplateGroup(changed_group)
    result = edits.apply(hou, pending)
    assert not result["success"] and node.parm("pending") is None, result
    assert node.parm("artist_added") is not None
    print("PASS tuple/component collision rejection and stale-layout detection", flush=True)

    # Fail after a set + template append + another set. All earlier fields and
    # the new template must roll back even when HOM recreates parameter handles.
    before = channels()
    failure_plan = plan([set_value("gain", 1.5), add("temporary"),
                         set_value("temporary", 0.25), set_value("gain", 1.75)])
    original = edits._perform

    def fail_last(hou_module, operation, journal):
        if operation.get("parameter") == "gain" and operation.get("value") == 1.75:
            raise RuntimeError("Synthetic post-layout failure")
        return original(hou_module, operation, journal)

    with mock.patch.object(edits, "_perform", side_effect=fail_last):
        result = edits.apply(hou, failure_plan)
    assert not result["success"] and result["rollback"] == "reverted_scene_fields", result
    assert not result["manual_undo_needed"] and node.parm("temporary") is None, result
    assert channels() == before
    print("PASS rollback removes only new template and restores setters across layout changes", flush=True)

    metadata = inspection.execute(hou, "parameter_schema", {"path": path, "query": "gain"})
    gain = next(item for item in metadata["parameters"] if item["name"] == "gain")
    assert gain["is_spare"] is True and gain["default_values"] == [1.0], gain
    assert gain["minimum"] == 0.0 and gain["maximum"] == 2.0, gain
    print("PASS discovered spare status, default and numeric range", flush=True)
    print(json.dumps({"houdini": hou.applicationVersionString(), "result": "passed",
                      "scope": "parameter creation, preservation, collision/stale checks and rollback; no cook"}), flush=True)


if __name__ == "__main__":
    main()
