"""Read-only scene query tests use inert HOM doubles; no Houdini process."""

import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import inspection, scene


class Parm:
    def __init__(self, name, text):
        self._name, self._text = name, text

    def name(self):
        return self._name

    def rawValue(self):
        return self._text

    def eval(self):
        raise AssertionError("Parameter evaluation is forbidden")

    evalAsString = eval
    evalAsFloat = eval


class Node:
    def __init__(self, path, children=(), parameters=(), inputs=(), outputs=(), type_name="attribwrangle"):
        self._path, self._children = path, tuple(children)
        self._parameters = tuple(parameters)
        self._inputs, self._outputs = tuple(inputs), tuple(outputs)
        self.type_name = type_name
        self.children_reads = 0

    def path(self):
        return self._path

    def type(self):
        return types.SimpleNamespace(name=lambda: self.type_name)

    def children(self):
        self.children_reads += 1
        return self._children

    def parms(self):
        return self._parameters

    def parm(self, name):
        return next((parm for parm in self._parameters if parm.name() == name), None)

    def inputs(self):
        return self._inputs

    def outputs(self):
        return self._outputs

    def errors(self):
        return ("Last cook: invalid VEX",)

    def warnings(self):
        return ()

    def cook(self, *args, **kwargs):
        raise AssertionError("Cooking, geometry, rendering and mutations are forbidden")

    geometry = cook
    render = cook
    setSelected = cook
    allSubChildren = cook
    asCode = cook


class Hou:
    def __init__(self, nodes=(), selection=()):
        self.nodes = {node.path(): node for node in nodes}
        self.selection = tuple(selection)
        self.reads = []
        self.hipFile = types.SimpleNamespace(basename=self._basename, path=self._forbidden_path)

    def _basename(self):
        self.reads.append("basename")
        return "lookdev.hip"

    def _forbidden_path(self):
        raise AssertionError("Do not request the full HIP file path")

    def applicationVersionString(self):
        self.reads.append("version")
        return "20.5.445"

    def selectedNodes(self):
        self.reads.append("selection")
        return self.selection

    def node(self, path):
        self.reads.append("node")
        return self.nodes.get(path)


class InspectionTests(unittest.TestCase):
    def test_session_identity_uses_basename_and_matches_snapshot(self):
        hou = Hou()
        first = inspection.execute(hou, "session_info", {})
        second = inspection.execute(hou, "session_info", {})
        self.assertEqual(first["session"], second["session"])
        self.assertEqual(first["session"], scene.snapshot_selection(hou)["session"])
        self.assertEqual(first["session"]["scene_name"], "lookdev.hip")
        self.assertEqual(first["session"]["houdini_version"], "20.5.445")
        self.assertGreater(first["session"]["pid"], 0)
        self.assertEqual(len(first["session"]["id"]), 36)

    def test_operation_and_argument_allowlist_rejects_before_hom_reads(self):
        invalid = [
            ("execute_python", {"code": "private-untrusted-code"}),
            ([], {}), ("session_info", []), ("session_info", {"path": "/"}),
            ("get_selection", {"limit": 2}), ("inspect_node", {}),
            ("inspect_node", {"path": "/obj", "code": "private-untrusted-code"}),
            ("list_children", {"path": "/", "limit": True}),
            ("list_children", {"path": "/", "limit": 0}),
            ("list_children", {"path": "/", "limit": 33}),
            ("list_children", {"path": "/", "offset": -1}),
            ("list_children", {"path": "/", "offset": False}),
            ("list_children", {"path": "/", "offset": 1.5}),
            ("list_node_types", {}),
            ("list_node_types", {"parent": "/obj", "category": "Object"}),
            ("node_type_schema", {"node_type": "flipsolver::2.0"}),
            ("list_node_types", {"category": "Dop;import os"}),
            ("list_node_types", {"category": "Dop\n"}),
            ("list_node_types", {"category": 1}),
            ("list_node_types", {"category": "D" * 65}),
        ]
        for operation, arguments in invalid:
            with self.subTest(operation=operation, arguments=arguments):
                hou = Hou()
                with self.assertRaises(ValueError) as caught:
                    inspection.execute(hou, operation, arguments)
                self.assertNotIn("private-untrusted-code", str(caught.exception))
                self.assertEqual(hou.reads, [])

    def test_category_discovery_dispatch_does_not_require_an_existing_network(self):
        from houlm import discovery
        hou = Hou()
        arguments = {"category": "Dop", "node_type": "flipsolver::2.0"}
        with mock.patch.object(discovery, "execute", return_value={"parameters": []}) as execute:
            result = inspection.execute(hou, "node_type_schema", arguments)
        self.assertEqual(result, {"parameters": []})
        self.assertEqual(execute.call_args.args[:3], (hou, "node_type_schema", arguments))
        self.assertNotIn("node", hou.reads)

    def test_paths_reject_traversal_patterns_controls_and_oversized_values(self):
        for path in ("obj/geo", "", None, "/obj/../geo", "/obj/./geo", "/obj/*", "/obj/a?", "/obj/[a]",
                     "/obj/{a,b}", "/obj\\geo", "/obj/\x00geo", "/obj/\ngeo", "/obj/\ud800", "/obj//geo", "/obj/", "/" + "a" * 1024):
            with self.subTest(path=repr(path)):
                hou = Hou()
                with self.assertRaises(ValueError):
                    inspection.execute(hou, "inspect_node", {"path": path})
                self.assertEqual(hou.reads, [])

    def test_missing_node_has_fixed_error(self):
        hou = Hou()
        with self.assertRaisesRegex(ValueError, "does not exist") as caught:
            inspection.execute(hou, "inspect_node", {"path": "/private_asset"})
        self.assertNotIn("private_asset", str(caught.exception))

    def test_inspect_node_prioritizes_raw_snippet_without_selection_or_evaluation(self):
        source, target = Node("/obj/source"), Node("/obj/target")
        raw_expression = '__import__("os").system("never execute")'
        parms = [Parm("ordinary_%d" % index, "$F") for index in range(80)]
        parms.append(Parm("snippet", raw_expression))
        node = Node("/obj/wrangle", parameters=parms, inputs=(source, None), outputs=(target,))
        hou = Hou(nodes=(node,))
        result = inspection.execute(hou, "inspect_node", {"path": node.path()})
        record = result["node"]
        self.assertEqual(record["path"], node.path())
        self.assertEqual(record["parameters"][0], {"name": "snippet", "raw_value": raw_expression})
        self.assertEqual(record["parameters"][1]["raw_value"], "$F")
        self.assertEqual(record["parameter_count"], 81)
        self.assertEqual(len(record["parameters"]), scene.MAX_PARAMETERS)
        self.assertEqual(record["inputs"], ["/obj/source", None])
        self.assertEqual(record["outputs"], ["/obj/target"])
        self.assertEqual(record["last_cook_diagnostics"]["errors"], ["Last cook: invalid VEX"])
        self.assertNotIn("selection", hou.reads)
        self.assertTrue(result["truncated"])

    def test_root_network_pagination_does_not_recurse(self):
        grandchild = Node("/child0/grandchild", children=(Node("/must_not_visit"),))
        children = [Node("/child%d" % index, children=(grandchild,) if index == 0 else ()) for index in range(70)]
        root = Node("/", children=children, type_name="root")
        hou = Hou(nodes=(root,))
        result = inspection.execute(hou, "list_children", {"path": "/", "limit": 16})
        self.assertEqual(result["child_count"], 70)
        self.assertEqual(len(result["children"]), 16)
        self.assertEqual(result["children"][0]["child_count"], 1)
        self.assertEqual(result["next_offset"], 16)
        self.assertTrue(result["truncated"])
        self.assertEqual(grandchild.children_reads, 0)
        self.assertEqual(children[16].children_reads, 0)
        second = inspection.execute(hou, "list_children", {"path": "/", "offset": 16, "limit": 16})
        self.assertEqual(second["children"][0]["path"], "/child16")
        self.assertEqual(second["next_offset"], 32)
        past_end = inspection.execute(hou, "list_children", {"path": "/", "offset": 100})
        self.assertEqual(past_end["children"], [])
        self.assertIsNone(past_end["next_offset"])

    def test_inspect_exposes_precise_multi_output_connection_indices(self):
        source, node = Node("/stage/source"), Node("/stage/target")
        connection = types.SimpleNamespace(inputNode=lambda: source, inputIndex=lambda: 2, outputIndex=lambda: 1)
        node.inputConnections = lambda: (connection,)
        result = inspection.execute(Hou(nodes=(node,)), "inspect_node", {"path": node.path()})
        self.assertEqual(result["node"]["input_connections"], [
            {"input_index": 2, "source": "/stage/source", "output_index": 1}])

    def test_empty_network_and_selection(self):
        root = Node("/", type_name="root")
        hou = Hou(nodes=(root,))
        self.assertEqual(inspection.execute(hou, "get_selection", {})["paths"], [])
        result = inspection.execute(hou, "list_children", {"path": "/"})
        self.assertEqual(result["child_count"], 0)
        self.assertIsNone(result["next_offset"])
        self.assertFalse(result["truncated"])
        self.assertEqual(inspection.execute(hou, "inspect_node", {"path": "/"})["node"]["path"], "/")

    def test_selection_and_unicode_results_stay_bounded_with_explicit_truncation(self):
        nodes = [Node("/obj/" + "é" * 900 + str(index)) for index in range(100)]
        hou = Hou(selection=nodes)
        result = inspection.execute(hou, "get_selection", {})
        self.assertEqual(result["selected_node_count"], 100)
        self.assertLess(len(result["paths"]), inspection.MAX_SELECTION_PATHS)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(scene._size(result), inspection.MAX_RESULT_BYTES)
        self.assertTrue(all(path in hou.nodes or path.startswith("/obj/") for path in result["paths"]))

        root = Node("/", children=nodes)
        child_result = inspection.execute(Hou(nodes=(root,)), "list_children", {"path": "/"})
        self.assertLessEqual(scene._size(child_result), inspection.MAX_RESULT_BYTES)
        self.assertGreater(child_result["next_offset"], 0)
        self.assertLess(child_result["next_offset"], 32)
        self.assertEqual(child_result["next_offset"], len(child_result["children"]))

    def test_inspection_preserves_long_node_identity_with_large_parameters(self):
        path = "/obj/" + "é" * 900
        node = Node(path, parameters=[Parm("snippet", "😀" * 20000)] + [Parm("p%d" % index, "😀" * 20000) for index in range(64)])
        result = inspection.execute(Hou(nodes=(node,)), "inspect_node", {"path": path})
        self.assertEqual(result["node"]["path"], path)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(scene._size(result), inspection.MAX_RESULT_BYTES)
        json.dumps(result, ensure_ascii=False).encode("utf-8")

    def test_read_failures_do_not_echo_hom_exceptions(self):
        node = Node("/obj/hidden")
        node.type = mock.Mock(side_effect=RuntimeError("PRIVATE_DIRECTORY"))
        result = inspection.execute(Hou(nodes=(node,)), "inspect_node", {"path": node.path()})
        self.assertGreater(result["node"]["read_failures"], 0)
        self.assertNotIn("PRIVATE_DIRECTORY", json.dumps(result))

    def test_worker_thread_is_rejected_before_any_hom_read(self):
        hou, failures = Hou(), []
        def work():
            try:
                inspection.execute(hou, "session_info", {})
            except RuntimeError as error:
                failures.append(str(error))
        worker = threading.Thread(target=work)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertEqual(hou.reads, [])

    def test_actual_qt_thread_mismatch_is_rejected_even_on_python_main_thread(self):
        hou = Hou()
        qt_core = types.SimpleNamespace(QThread=types.SimpleNamespace(currentThread=lambda: "worker"))
        qt_widgets = types.SimpleNamespace(QApplication=types.SimpleNamespace(instance=lambda: types.SimpleNamespace(thread=lambda: "ui")))
        with mock.patch.dict(sys.modules, {"PySide2.QtCore": qt_core, "PySide2.QtWidgets": qt_widgets}):
            with self.assertRaises(RuntimeError):
                inspection.execute(hou, "get_selection", {})
        self.assertEqual(hou.reads, [])


if __name__ == "__main__":
    unittest.main()
