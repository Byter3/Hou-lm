"""Opt-in generic Python bridge regression in a fresh, empty hython process.

This developer fixture is not imported by the panel or offered as a model tool.
It tests unrelated HOM capabilities through the same source-execution path,
without loading/saving an artist scene, rendering or writing caches.
"""
import json
import os
from pathlib import Path
import sys
import textwrap


def main():
    import hou
    if hou.isUIAvailable() or hou.node("/obj").children() or hou.node("/stage").children():
        raise RuntimeError("Use a fresh empty hython, never the artist's open scene.")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide2 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    hou.setUpdateMode(hou.updateMode.Manual)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
    from houlm import homhelp, python_actions

    for symbol, expected in (("hou.RampParmTemplate", "ramp_parm_type"), ("hou.Parm.set", "value"),
                             ("hou.LopNode.stage", "stage"), ("hou.Parm.evalAsRamp", "Ramp")):
        result = homhelp.lookup(hou, {"symbol": symbol})
        assert expected.lower() in result["documentation"].lower(), result
    assert "RampParmTemplate" in homhelp.lookup(hou, {"symbol": "hou", "query": "ramp"})["members"]
    print("PASS installed HOM constructor/method discovery without evaluation", flush=True)

    def run(code, targets=("/obj",)):
        proposal = python_actions.prepare(hou, {"summary": "Generic native Python bridge verification",
                                                "code": textwrap.dedent(code), "target_paths": list(targets)})
        result = python_actions.apply(hou, proposal)
        assert result["success"], result
        return result["result"]

    result = run('''
        geo = hou.node("/obj").createNode("geo", "HOULM_PYTHON_TEST", run_init_scripts=False)
        node = geo.createNode("attribwrangle", "controls", run_init_scripts=False)
        group = node.parmTemplateGroup()
        group.append(hou.FloatParmTemplate("distance", "Distance", 1, default_value=(2.0,)))
        group.append(hou.RampParmTemplate("falloff_ramp", "Falloff", hou.rampParmType.Float))
        group.append(hou.RampParmTemplate("tint_ramp", "Tint", hou.rampParmType.Color))
        node.setParmTemplateGroup(group)
        node.parm("falloff_ramp").set(hou.Ramp((hou.rampBasis.Linear, hou.rampBasis.Linear), (0.0, 1.0), (1.0, 0.0)))
        node.parm("tint_ramp").set(hou.Ramp((hou.rampBasis.Linear, hou.rampBasis.Linear), (0.0, 1.0), ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))))
        node.parm("snippet").set('float u = clamp(length(@P) / max(chf("distance"), 1e-6), 0, 1);\\nf@weight = chramp("falloff_ramp", u);')
        result = {"path": node.path(), "distance": node.parm("distance").eval(),
                  "keys": list(node.parm("falloff_ramp").evalAsRamp().keys()),
                  "values": list(node.parm("falloff_ramp").evalAsRamp().values()),
                  "color_keys": list(node.parm("tint_ramp").evalAsRamp().keys())}
    ''')
    assert result["keys"] == [0.0, 1.0] and result["values"] == [1.0, 0.0], result
    assert result["distance"] == 2.0 and result["color_keys"] == [0.0, 1.0], result
    path = result["path"]
    print("PASS generic Python creates float/color ramps and VEX controls; reads evaluated values", flush=True)

    result = run('''
        node = hou.node("/obj/HOULM_PYTHON_TEST/controls")
        group = node.parmTemplateGroup()
        folder = hou.FolderParmTemplate("artist_controls", "Artist Controls")
        folder.addParmTemplate(hou.FloatParmTemplate("gain", "Gain", 1, default_value=(1.0,)))
        multi = hou.FolderParmTemplate("layers", "Layers", folder_type=hou.folderType.MultiparmBlock)
        multi.addParmTemplate(hou.FloatParmTemplate("weight#", "Weight", 1, default_value=(1.0,)))
        folder.addParmTemplate(multi)
        group.append(folder)
        node.setParmTemplateGroup(group)
        node.parm("layers").set(2)
        for index, value in enumerate((0.25, 0.75), 1):
            checkpoint()
            node.parm("weight" + str(index)).set(value)
        node.parm("gain").setExpression('$F / 24', hou.exprLanguage.Hscript)
        key = hou.Keyframe()
        key.setFrame(1)
        key.setValue(2)
        node.parm("distance").setKeyframe(key)
        key.setFrame(24)
        key.setValue(5)
        node.parm("distance").setKeyframe(key)
        result = {"layers": node.parm("layers").eval(),
                  "weights": [node.parm("weight1").eval(), node.parm("weight2").eval()],
                  "expression": node.parm("gain").expression(),
                  "frames": [k.frame() for k in node.parm("distance").keyframes()],
                  "ramp_values": list(node.parm("falloff_ramp").evalAsRamp().values())}
    ''', (path,))
    assert result["weights"] == [0.25, 0.75] and result["frames"] == [1.0, 24.0], result
    assert result["expression"] == "$F / 24" and result["ramp_values"] == [1.0, 0.0], result
    print("PASS generic Python modifies interface folders/multiparms, expressions and animation", flush=True)

    result = run('''
        node = hou.node("/obj/HOULM_PYTHON_TEST/controls")
        group = node.parmTemplateGroup()
        group.append(hou.IntParmTemplate("clicked", "Clicked", 1, default_value=(0,)))
        button = hou.ButtonParmTemplate("test_action", "Test Action")
        button.setScriptCallback('hou.pwd().parm("clicked").set(1)')
        button.setScriptCallbackLanguage(hou.scriptLanguage.Python)
        group.append(button)
        node.setParmTemplateGroup(group)
        node.parm("test_action").pressButton()
        geo = node.parent()
        source = geo.createNode("null", "source", run_init_scripts=False)
        out = geo.createNode("null", "out", run_init_scripts=False)
        out.setInput(0, source)
        out.setName("renamed_out")
        duplicate = out.copyTo(geo)
        duplicate_path = duplicate.path()
        duplicate.destroy()
        result = {"clicked": node.parm("clicked").eval(), "renamed": out.path(),
                  "connected": out.inputs()[0].path(), "deleted": hou.node(duplicate_path) is None}
    ''', (path,))
    assert result["clicked"] == 1 and result["deleted"] and result["renamed"].endswith("renamed_out"), result
    print("PASS generic Python invokes callbacks and creates/connects/copies/renames/deletes nodes", flush=True)

    result = run('''
        from pxr import Usd
        python_types = [name for name in hou.lopNodeTypeCategory().nodeTypes() if "python" in name.lower()]
        assert len(python_types) == 1, python_types
        node = hou.node("/stage").createNode(python_types[0], "HOULM_PYTHON_STAGE", run_init_scripts=False)
        code_parms = [parm for parm in node.parms() if "python" in parm.parmTemplate().tags().get("editorlang", "").lower()]
        assert len(code_parms) == 1, [parm.name() for parm in code_parms]
        code_parms[0].set('from pxr import UsdGeom\\nimport hou\\nUsdGeom.Xform.Define(hou.pwd().editableStage(), "/World")\\nUsdGeom.Cube.Define(hou.pwd().editableStage(), "/World/TestCube")')
        previous_mode = hou.updateModeSetting()
        try:
            hou.setUpdateMode(hou.updateMode.AutoUpdate)
            stage = node.stage()
            assert stage is not None, node.errors()
            prim = stage.GetPrimAtPath("/World/TestCube")
            result = {"path": node.path(), "type": node.type().name(), "usd_version": list(Usd.GetVersion()),
                      "prim_path": str(prim.GetPath()), "prim_type": prim.GetTypeName()}
        finally:
            hou.setUpdateMode(previous_mode)
    ''', ("/stage",))
    assert result["path"] == "/stage/HOULM_PYTHON_STAGE" and result["usd_version"], result
    assert result["prim_path"] == "/World/TestCube" and result["prim_type"] == "Cube", result
    print("PASS generic Python authors and evaluates a small USD stage through a Python LOP", flush=True)

    proposal = python_actions.prepare(hou, {"summary": "Verify honest partial failure",
        "target_paths": [path], "code": 'hou.node("' + path + '").parm("clicked").set(3)\nraise ValueError("synthetic failure")'})
    result = python_actions.apply(hou, proposal)
    assert not result["success"] and result["execution_started"] and result["manual_undo_needed"], result
    assert result["rollback"] == "not_attempted" and hou.node(path).parm("clicked").eval() == 3, result
    assert result["error"]["code_line"] == 2, result
    print("PASS partial execution returns actionable error and does not falsely claim rollback", flush=True)
    print(json.dumps({"houdini": hou.applicationVersionString(), "result": "passed",
                      "scope": "generic Python bridge; no scene load/save, render or cache write"}), flush=True)


if __name__ == "__main__":
    main()
