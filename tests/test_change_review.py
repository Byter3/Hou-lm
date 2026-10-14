"""Review diffs inspect authored fields only; all HOM objects are inert doubles."""

import copy
import json
from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import change_review


class Parm:
    def __init__(self, name, value):
        self._name, self.value = name, value

    def name(self):
        return self._name

    def rawValue(self):
        return self.value

    def eval(self):
        raise AssertionError("Never evaluate parameters in review.")


class Node:
    def __init__(self, path, parms=()):
        self._path, self._parms = path, list(parms)
        self._children = []

    def type(self):
        return types.SimpleNamespace(name=lambda: "attribwrangle")

    def path(self):
        return self._path

    def parms(self):
        return self._parms

    def inputConnections(self):
        return []

    def children(self):
        return self._children

    def cook(self):
        raise AssertionError("No cooks in change review.")

    geometry = cook


class ChangeReviewTests(unittest.TestCase):
    def test_typed_vex_diff_uses_authored_text_and_tracks_sequential_changes(self):
        node = Node("/obj/geo/wrangle", [Parm("snippet", "@P.y = 0;")])
        hou = types.SimpleNamespace(node=lambda path: node)
        arguments = {"operations": [{"op": "set_parameter", "path": node.path(),
                                     "parameter": "snippet", "value": "@P.y = 1;"}]}
        before = change_review.snapshot_targets(hou, arguments)
        text = change_review.review_text("Exact plan", arguments, before)
        self.assertIn("-@P.y = 0;", text)
        self.assertIn("+@P.y = 1;", text)
        node._parms[0].value = "@P.y = 1;"
        after = change_review.snapshot_targets(hou, arguments)
        self.assertIn("+@P.y = 1;", change_review.format_changes(before, after))

    def test_python_source_is_not_interpreted_and_after_state_not_predicted(self):
        arguments = {"code": "raise Exception('DO NOT EXECUTE')", "target_paths": ["/obj/new"]}
        hou = types.SimpleNamespace(node=lambda path: None)
        before = change_review.snapshot_targets(hou, arguments)
        self.assertEqual(before["nodes"]["/obj/new"], {"exists": False})
        text = change_review.review_text(arguments["code"], arguments, before)
        self.assertIn("after-state is unknown until Apply", text)
        self.assertIn(arguments["code"], text)

    def test_create_delete_and_parent_child_changes_are_observed(self):
        node = Node("/obj")
        arguments = {"code": "", "target_paths": ["/obj", "/obj/new"]}
        nodes = {"/obj": node}
        hou = types.SimpleNamespace(node=nodes.get)
        before = change_review.snapshot_targets(hou, arguments)
        child = Node("/obj/new")
        nodes[child.path()] = child
        node._children.append(child)
        after = change_review.snapshot_targets(hou, arguments)
        text = change_review.format_changes(before, after)
        self.assertIn("Created /obj/new", text)
        self.assertIn("Appeared under /obj", text)
        self.assertIn("Removed from /obj/new", change_review.format_changes(after, before))

    def test_limits_and_partial_snapshot_do_not_claim_deleted_parameters(self):
        parms = [Parm("p" + str(i), "a" * 9000) for i in range(100)]
        node = Node("/obj/geo", parms)
        arguments = {"code": "", "target_paths": [node.path()]}
        before = change_review.snapshot_targets(types.SimpleNamespace(node=lambda path: node), arguments)
        self.assertLessEqual(len(json.dumps(before).encode("utf-8")), change_review.MAX_BYTES)
        self.assertTrue(before["truncated"])
        after = copy.deepcopy(before)
        after["nodes"].pop(node.path(), None)
        text = change_review.format_changes(before, after)
        self.assertNotIn("parameter removed", text)
        self.assertIn("partial", text)

    def test_typed_create_resolves_future_node_path(self):
        self.assertEqual(change_review.target_paths({"operations": [{"op": "create_node", "parent": "/obj",
                                                                     "name": "new"}]}), ["/obj/new"])


if __name__ == "__main__":
    unittest.main()
