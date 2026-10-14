"""Scene capture tests use an inert HOM double, never an installed Houdini."""

import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import scene


class FakeParm:
    forbidden_calls = []

    def __init__(self, name, value):
        self._name, self._value = name, value

    def name(self):
        return self._name

    def rawValue(self):
        return self._value

    def eval(self):
        self.forbidden_calls.append("eval")
        raise AssertionError("Snapshot must not evaluate parameters")

    evalAsString = eval
    evalAsFloat = eval


class FakeNode:
    forbidden_calls = []

    def __init__(self, name="wrangle1", parms=(), inputs=(), outputs=()):
        self._name = name
        self._parms = tuple(parms)
        self._inputs, self._outputs = tuple(inputs), tuple(outputs)

    def path(self):
        return "/obj/geo1/" + self._name

    def type(self):
        class NodeType:
            def name(self):
                return "attribwrangle"
        return NodeType()

    def parms(self):
        return self._parms

    def parm(self, name):
        return next((p for p in self._parms if p.name() == name), None)

    def inputs(self):
        return self._inputs

    def outputs(self):
        return self._outputs

    def errors(self):
        return ("Last-cook syntax error",)

    def warnings(self):
        return ("Last-cook warning",)

    def cook(self, *args, **kwargs):
        self.forbidden_calls.append("cook")
        raise AssertionError("Snapshot must not cook, render, or mutate")

    geometry = cook
    render = cook
    setSelected = cook


class FakeHou:
    class hipFile:
        @staticmethod
        def basename():
            return "test_scene.hip"

        @staticmethod
        def path():
            raise AssertionError("Snapshot must not request the full HIP path")

    def __init__(self, nodes=()):
        self._nodes = tuple(nodes)
        self.selection_reads = 0

    def applicationVersionString(self):
        return "20.5.445"

    def selectedNodes(self):
        self.selection_reads += 1
        return self._nodes


class SceneSnapshotTests(unittest.TestCase):
    def setUp(self):
        FakeParm.forbidden_calls.clear()
        FakeNode.forbidden_calls.clear()

    def test_authored_values_and_last_cook_diagnostics_without_evaluation(self):
        upstream, downstream = FakeNode("box1"), FakeNode("null1")
        expression = '__import__("os").system("must not run")'
        node = FakeNode(parms=[FakeParm("snippet", "@P.y += 1;"),
                               FakeParm("expression", expression),
                               FakeParm("filename", "$HIP/image.$F4.exr")],
                        inputs=(upstream, None), outputs=(downstream,))
        result = scene.snapshot_selection(FakeHou([node]))
        record = result["nodes"][0]
        values = {entry["name"]: entry["raw_value"] for entry in record["parameters"]}
        self.assertEqual(values["expression"], expression)
        self.assertEqual(values["filename"], "$HIP/image.$F4.exr")
        self.assertEqual(record["inputs"], ["/obj/geo1/box1", None])
        self.assertEqual(record["outputs"], ["/obj/geo1/null1"])
        self.assertEqual(record["last_cook_diagnostics"]["errors"], ["Last-cook syntax error"])
        self.assertEqual(result["session"]["scene_name"], "test_scene.hip")
        self.assertEqual(result["session"]["houdini_version"], "20.5.445")
        self.assertFalse(FakeParm.forbidden_calls)
        self.assertFalse(FakeNode.forbidden_calls)

    def test_selection_and_parameter_limits_prioritize_snippet(self):
        parms = [FakeParm("p" + str(i), str(i)) for i in range(100)]
        parms.append(FakeParm("snippet", "@Cd = 1;"))
        result = scene.snapshot_selection(FakeHou([FakeNode(str(i), parms) for i in range(12)]))
        self.assertEqual(result["selected_node_count"], 12)
        self.assertEqual(len(result["nodes"]), scene.MAX_NODES)
        self.assertTrue(result["truncated"])
        for record in result["nodes"]:
            self.assertLessEqual(len(record["parameters"]), scene.MAX_PARAMETERS)
            self.assertEqual(record["parameters"][0]["name"], "snippet")

    def test_total_unicode_json_and_individual_string_bounds(self):
        huge = "😀\\\"\n" * 10000
        parms = [FakeParm("snippet", huge)] + [FakeParm("p" + str(i), huge) for i in range(70)]
        result = scene.snapshot_selection(FakeHou([FakeNode(str(i), parms) for i in range(8)]))
        encoded = json.dumps(result, indent=2).encode("utf-8")
        self.assertLessEqual(len(encoded), scene.MAX_SNAPSHOT_BYTES)
        self.assertEqual(json.loads(encoded), result)
        self.assertTrue(result["truncated"])
        for record in result["nodes"]:
            for entry in record["parameters"]:
                self.assertLessEqual(len(entry["raw_value"]), scene.MAX_STRING_CHARS)
        json.dumps(result, ensure_ascii=False).encode("utf-8")

    def test_connection_limits_and_empty_selection(self):
        connected = [FakeNode(str(i)) for i in range(100)]
        record = scene.snapshot_selection(FakeHou([FakeNode(inputs=connected, outputs=connected)]))["nodes"][0]
        self.assertEqual(record["input_count"], 100)
        self.assertEqual(len(record["inputs"]), scene.MAX_CONNECTIONS)
        self.assertTrue(record["truncated"])
        self.assertEqual(scene.snapshot_selection(FakeHou())["nodes"], [])

    def test_session_identity_is_stable_within_process(self):
        first = scene.snapshot_selection(FakeHou())["session"]
        second = scene.snapshot_selection(FakeHou())["session"]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["pid"], second["pid"])

    def test_rejects_worker_thread_before_reading_hou(self):
        hou, errors = FakeHou(), []
        def work():
            try:
                scene.snapshot_selection(hou)
            except RuntimeError as error:
                errors.append(str(error))
        worker = threading.Thread(target=work)
        worker.start()
        worker.join()
        self.assertEqual(len(errors), 1)
        self.assertEqual(hou.selection_reads, 0)

    def test_failed_reads_do_not_evaluate_or_expose_exception_details(self):
        class BrokenParm(FakeParm):
            def rawValue(self):
                raise RuntimeError("private directory must not appear")
        result = scene.snapshot_selection(FakeHou([FakeNode(parms=[BrokenParm("broken", "")])]))
        self.assertGreater(result["nodes"][0]["read_failures"], 0)
        self.assertNotIn("private directory", json.dumps(result))
        self.assertFalse(FakeParm.forbidden_calls)


if __name__ == "__main__":
    unittest.main()
