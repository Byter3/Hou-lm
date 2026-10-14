"""General reviewed scripting tests use an inert HOM double, never a live HIP."""

from contextlib import contextmanager
from pathlib import Path
import json
import sys
import threading
import unittest
from unittest import mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import python_actions as actions


class Node:
    def __init__(self, path, sid):
        self.location, self.sid, self.value = path, sid, 0
        self.ramp, self.templates = None, []

    def path(self):
        return self.location

    def sessionId(self):
        return self.sid

    def type(self):
        return SimpleNamespace(name=lambda: "attribwrangle")

    def setValue(self, value):
        self.value = value

    def eval(self):
        return self.value

    def setRamp(self, ramp):
        self.ramp = ramp

    def addTemplate(self, template):
        self.templates.append(template)


class Undos:
    def __init__(self):
        self.labels = []

    @contextmanager
    def group(self, label):
        try:
            yield
        finally:
            self.labels.insert(0, label)

    def undoLabels(self):
        return tuple(self.labels)

    def performUndo(self):
        raise AssertionError("Never run a global Undo after generated code fails.")


class Hou:
    def __init__(self):
        self.nodes = {"/obj/geo1/wrangle1": Node("/obj/geo1/wrangle1", 1)}
        self.selection = [self.nodes["/obj/geo1/wrangle1"]]
        self.hipFile = SimpleNamespace(basename=lambda: "untitled.hip")
        self.undos = Undos()
        self.rampBasis = SimpleNamespace(Linear="linear", CatmullRom="catmullrom")
        self.parmTemplateType = SimpleNamespace(Ramp="ramp")

    def applicationVersionString(self):
        return "20.5.445"

    def node(self, path):
        return self.nodes.get(path)

    def selectedNodes(self):
        return tuple(self.selection)

    def Ramp(self, basis, keys, values):
        return {"basis": tuple(basis), "keys": tuple(keys), "values": tuple(values)}

    def RampParmTemplate(self, name, label, kind):
        return {"name": name, "label": label, "kind": kind}

    def fail(self):
        raise ValueError("Parameter 'size' is not present at C:\\private\\asset.py")

    def binary(self):
        return b"private binary image content"


class PythonActionTests(unittest.TestCase):
    def setUp(self):
        self.hou = Hou()
        self.path = "/obj/geo1/wrangle1"

    def prepare(self, code="result = 3", **kwargs):
        args = {"summary": "Exercise a general scene action", "code": code, "target_paths": [self.path]}
        args.update(kwargs)
        return actions.prepare(self.hou, args)

    def execute(self, code, **kwargs):
        return actions.apply(self.hou, self.prepare(code), **kwargs)

    def test_prepare_and_rejected_proposal_never_execute(self):
        code = 'hou.node("/obj/geo1/wrangle1").setValue(12)'
        plan = self.prepare(code)
        self.assertEqual(self.hou.node(self.path).value, 0)
        self.assertIn(code, plan.summary)
        self.assertIn("not be undoable", plan.summary)
        # A rejected review simply discards the prepared plan.
        del plan
        self.assertEqual(self.hou.node(self.path).value, 0)

    def test_general_ramp_and_template_actions(self):
        code = '''import hou
node = hou.node("/obj/geo1/wrangle1")
template = hou.RampParmTemplate("falloff", "Falloff", hou.parmTemplateType.Ramp)
node.addTemplate(template)
ramp = hou.Ramp((hou.rampBasis.Linear, hou.rampBasis.CatmullRom), (0.0, 1.0), (1.0, 0.0))
node.setRamp(ramp)
result = {"path": node.path(), "ramp": ramp}
print("Configured", node.path())
'''
        result = self.execute(code)
        self.assertTrue(result["success"])
        self.assertTrue(result["execution_started"])
        self.assertTrue(result["undo_recorded"])
        self.assertEqual(self.hou.node(self.path).ramp["values"], (1.0, 0.0))
        self.assertEqual(result["result"]["ramp"]["values"], [1.0, 0.0])
        self.assertIn("Configured /obj/geo1/wrangle1", result["stdout"])

    def test_fresh_namespace_and_imports(self):
        first = self.execute("import math\nprivate_state = 18\nresult = math.sqrt(81)")
        self.assertEqual(first["result"], 9)
        second = self.execute("result = private_state")
        self.assertFalse(second["success"])
        self.assertEqual(second["error"]["exception_type"], "NameError")
        self.assertTrue(second["manual_undo_needed"])

    def test_general_control_flow_functions_and_comprehensions(self):
        result = self.execute('''from collections import Counter
def squared(value):
    checkpoint()
    return value * value
values = [squared(value) for value in range(5)]
result = {"squares": values, "counts": dict(Counter("abaca"))}
''')
        self.assertEqual(result["result"], {"squares": [0, 1, 4, 9, 16], "counts": {"a": 3, "b": 1, "c": 1}})

    def test_hom_eval_and_underscore_locals_are_available(self):
        result = self.execute('import math as _math\n_node = hou.node("/obj/geo1/wrangle1")\n_node.setValue(4)\nresult = _math.sqrt(_node.eval())')
        self.assertEqual(result["result"], 2.0)

    def test_locally_bound_names_do_not_expose_absent_builtins(self):
        for code, expected in (
                ('input = hou.node("/obj/geo1/wrangle1")\nresult = input.path()', self.path),
                ("result = []\nfor input in range(3):\n    result.append(input)", [0, 1, 2]),
                ("result = [input * 2 for input in range(3)]", [0, 2, 4]),
                ("def summarize(input):\n    return input + 1\nresult = summarize(4)", 5),
                ("def open(value):\n    return value + 2\nresult = open(4)", 6),
                ("import math as sys\nresult = sys.sqrt(9)", 3.0),
                ("from math import sqrt as eval\nresult = eval(16)", 4.0),
                ("result = (input := 7)", 7),
                ("try:\n    raise ValueError('local exception')\nexcept ValueError as input:\n    result = str(input)", "local exception")):
            with self.subTest(code=code):
                result = self.execute(code)
                self.assertTrue(result["success"], result)
                self.assertEqual(result["result"], expected)

    def test_rejected_source_reports_identifier_and_line_without_other_source(self):
        for code, identifier, line in (
                ('result = "SCENE_TEXT_MUST_NOT_LEAK"\nimport os', "os", 2),
                ('result = "SCENE_TEXT_MUST_NOT_LEAK"\nresult = hou.ui', "ui", 2),
                ('result = "SCENE_TEXT_MUST_NOT_LEAK"\ninput("credential prompt")', "input", 2),
                ('result = "SCENE_TEXT_MUST_NOT_LEAK"\nresult = hou.__dict__', "__dict__", 2)):
            with self.subTest(identifier=identifier), self.assertRaises(actions.PythonActionError) as raised:
                self.prepare(code)
            error = raised.exception.details()["error"]
            self.assertEqual(error["identifier"], identifier)
            self.assertEqual(error["code_line"], line)
            self.assertIn(identifier, error["message"])
            self.assertNotIn("SCENE_TEXT_MUST_NOT_LEAK", json.dumps(error))

    def test_partial_failure_preserves_effects_and_diagnostic(self):
        result = self.execute('hou.node("/obj/geo1/wrangle1").setValue(5)\nhou.fail()')
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.hou.node(self.path).value, 5)
        self.assertTrue(result["execution_started"])
        self.assertTrue(result["manual_undo_needed"])
        self.assertEqual(result["rollback"], "not_attempted")
        self.assertTrue(result["undo_recorded"])
        self.assertEqual(result["error"]["code_line"], 2)
        self.assertIn("Parameter 'size'", result["error"]["message"])
        self.assertNotIn("C:\\private", json.dumps(result))

    def test_syntax_validation_precedes_mutation(self):
        with self.assertRaises(actions.PythonActionError) as raised:
            self.prepare('hou.node("/obj/geo1/wrangle1").setValue(4)\nif :')
        self.assertEqual(raised.exception.code, "python_syntax_error")
        self.assertEqual(self.hou.node(self.path).value, 0)

    def test_reject_direct_system_private_and_capture_access(self):
        for code in ("import os", "from pathlib import Path", "result = hou.ui",
                     "result = hou.session", "result = hou.__dict__", "exec('1')",
                     "open('x')", "hou.hscript('opls')", "import math as __math",
                     "from collections import *", "from . import x", "hou.saveViewToImage('x')",
                     "result = globals()", "import os as scene_helpers"):
            with self.subTest(code=code), self.assertRaises(actions.PythonActionError):
                self.prepare(code)

    def test_dynamic_private_attribute_access_rejected(self):
        result = self.execute('result = getattr(hou, "__dict__")')
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "python_access_not_available")

    def test_result_objects_are_not_stringified(self):
        result = self.execute('result = {"node": hou.node("/obj/geo1/wrangle1"), "binary": hou.binary()}\nprint(hou.node("/obj/geo1/wrangle1"))')
        self.assertEqual(result["result"]["node"], "[non-JSON object omitted]")
        self.assertEqual(result["result"]["binary"], "[binary data omitted]")
        self.assertNotIn("object at", result["stdout"])
        self.assertNotIn("private binary", json.dumps(result))

    def test_image_base64_and_credentials_omitted(self):
        result = self.execute('result = {"image": "data:image/png;base64,AAAA", "password": "secret", "encoded": "a" * 500}\nprint("Bearer SECRET_VALUE")')
        self.assertNotIn("data:image", json.dumps(result))
        self.assertNotIn("SECRET_VALUE", json.dumps(result))
        self.assertNotIn('"secret"', json.dumps(result))
        self.assertNotIn("a" * 500, json.dumps(result))

    def test_stdout_and_result_total_bound(self):
        result = self.execute('for value in range(1000):\n    print("large output! " * 100)\nresult = ["long unicode: 雪" * 500 for value in range(100)]')
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True).encode()), actions.MAX_OUTPUT_BYTES)
        self.assertTrue(result["output_truncated"])

    def test_recursive_result_bounded(self):
        result = self.execute("result = []\nresult.append(result)")
        self.assertTrue(result["success"])
        self.assertTrue(result["output_truncated"])
        self.assertLess(len(json.dumps(result)), 4000)

    def test_plan_one_use_and_cross_session(self):
        plan = self.prepare()
        with self.assertRaises(actions.PythonActionError):
            actions.apply(Hou(), plan)
        self.assertTrue(actions.apply(self.hou, plan)["success"])
        with self.assertRaises(actions.PythonActionError):
            actions.apply(self.hou, plan)

    def test_deleted_replaced_and_renamed_targets_are_stale(self):
        for change in ("delete", "replace", "rename"):
            with self.subTest(change=change):
                self.setUp()
                plan = self.prepare('hou.node("/obj/geo1/wrangle1").setValue(5)')
                if change == "delete":
                    del self.hou.nodes[self.path]
                elif change == "replace":
                    self.hou.nodes[self.path] = Node(self.path, 2)
                else:
                    self.hou.node(self.path).location = "/obj/geo1/renamed"
                result = actions.apply(self.hou, plan)
                self.assertEqual(result["status"], "stale")
                self.assertFalse(result["execution_started"])
                self.assertFalse(result["manual_undo_needed"])

    def test_selection_change_is_stale(self):
        plan = self.prepare()
        self.hou.selection = []
        self.assertEqual(actions.apply(self.hou, plan)["status"], "stale")

    def test_pre_execution_cancel(self):
        result = self.execute('hou.node("/obj/geo1/wrangle1").setValue(4)', cancelled=lambda: True)
        self.assertFalse(result["execution_started"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.hou.node(self.path).value, 0)

    def test_mid_execution_cancel(self):
        result = self.execute('hou.node("/obj/geo1/wrangle1").setValue(4)\ncheckpoint()\nresult = 1',
                              cancelled=lambda: self.hou.node(self.path).value == 4)
        self.assertTrue(result["execution_started"])
        self.assertEqual(result["error"]["code"], "python_cancelled")
        self.assertEqual(result["rollback"], "not_attempted")

    def test_trace_event_budget_stops_python_loop_and_restores_trace(self):
        previous = sys.gettrace()
        with mock.patch.object(actions, "MAX_TRACE_EVENTS", 30):
            result = self.execute("while True:\n    value = 1")
        self.assertIs(sys.gettrace(), previous)
        self.assertEqual(result["error"]["code"], "python_budget_exceeded")

    def test_time_budget_is_cooperative(self):
        plan = self.prepare("result = 1")
        with mock.patch.object(actions.time, "monotonic", side_effect=[1.0, 30.0]):
            result = actions.apply(self.hou, plan)
        self.assertEqual(result["error"]["code"], "python_budget_exceeded")
        self.assertFalse(result["execution_started"])

    def test_validates_shape_limits_and_targets(self):
        for changes in ({"summary": ""}, {"summary": "x" * 1025}, {"code": "x" * (24 * 1024 + 1)},
                        {"target_paths": [self.path, self.path]}, {"target_paths": ["/missing"]},
                        {"target_paths": ["/obj/../obj"]}, {"target_paths": "bad"}, {"other": 1}):
            with self.subTest(changes=changes), self.assertRaises(actions.PythonActionError):
                self.prepare(**changes)

    def test_source_limit_counts_utf8_bytes(self):
        with self.assertRaises(actions.PythonActionError):
            self.prepare("# " + "雪" * 9000)

    def test_long_target_paths_cannot_overflow_result_limit(self):
        paths = []
        for index in range(32):
            path = "/" + "雪" * 1000 + str(index)
            self.hou.nodes[path] = Node(path, index + 10)
            paths.append(path)
        result = actions.apply(self.hou, self.prepare(target_paths=paths))
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True).encode()), actions.MAX_OUTPUT_BYTES)
        self.assertTrue(result["target_paths_truncated"])

    def test_helper_class_output_never_invokes_repr(self):
        result = self.execute("class LocalResult:\n    value = 3\nresult = LocalResult()")
        self.assertTrue(result["success"])
        self.assertEqual(result["result"], "[non-JSON object omitted]")

    def test_rejects_non_ui_thread_before_hom(self):
        errors = []
        def work():
            try:
                self.prepare()
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=work)
        thread.start()
        thread.join()
        self.assertIsInstance(errors[0], RuntimeError)


if __name__ == "__main__":
    unittest.main()
