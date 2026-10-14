import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import activity


def call(name, arguments=None, identifier="one"):
    return {"id": identifier, "function": {"name": "houlm_" + name, "arguments": json.dumps(arguments or {})}}


def result(data, identifier="one"):
    return {"role": "tool", "tool_call_id": identifier, "content": json.dumps(data)}


class ActivityTests(unittest.TestCase):
    def test_pending_reads_name_target_without_claiming_success(self):
        row = activity.describe_call(call("inspect_node", {"path": "/obj/geo1/wrangle"}))
        self.assertEqual(row["phase"], "inspecting")
        self.assertIn("/obj/geo1/wrangle", row["title"])
        self.assertIn("no scene changes", row["detail"])
        self.assertNotIn("Read /", row["title"])

    def test_type_discovery_is_distinguished_from_scene_creation(self):
        request = call("list_node_types", {"category": "Sop", "query": "wrangle"})
        pending = activity.describe_call(request)
        self.assertIn("Sop", pending["title"])
        self.assertIn("wrangle", pending["title"])
        self.assertIn("no nodes are created", pending["detail"])
        row = activity.describe_call(request, result({"ok": True, "result": {
            "matched_count": 1, "node_types": [{"name": "attribwrangle"}]}}))
        self.assertIn("1 entry returned of 1 matching", row["detail"])
        self.assertIn("attribwrangle", row["detail"])
        self.assertIn("No nodes created", row["detail"])

    def test_static_schema_names_type_and_filter(self):
        row = activity.describe_call(call("node_type_schema", {"category": "Sop", "node_type": "grid", "query": "rows"}))
        self.assertIn("grid", row["title"])
        self.assertIn("rows", row["title"])
        self.assertIn("no node instance", row["detail"])

    def test_empty_network_only_claimed_when_count_is_zero(self):
        request = call("list_children", {"path": "/obj"})
        pending = activity.describe_call(request)
        self.assertNotIn("empty", str(pending))
        row = activity.describe_call(request, result({"ok": True, "result": {"child_count": 0, "children": []}}))
        self.assertEqual(row["title"], "/obj is empty")
        page = activity.describe_call(request, result({"ok": True, "result": {"child_count": 80, "children": [], "truncated": True}}))
        self.assertNotIn("empty", page["title"])
        self.assertIn("0 child nodes of 80", page["detail"])
        self.assertIn("Partial result", page["detail"])

    def test_read_failure_never_claims_missing_node_without_evidence(self):
        row = activity.describe_call(call("inspect_node", {"path": "/obj/missing"}), result({"ok": False, "error": "secret traceback"}))
        self.assertEqual(row["phase"], "failed")
        self.assertIn("/obj/missing", row["title"])
        self.assertIn("may be missing", row["detail"])
        self.assertNotIn("secret traceback", str(row))

    def test_inspection_shows_metadata_and_stale_diagnostics_without_values(self):
        row = activity.describe_call(call("inspect_node", {"path": "/obj/geo/wrangle"}), result({"ok": True, "result": {"node": {
            "type": "attribwrangle", "parameters": [{"name": "snippet", "raw_value": "PRIVATE VEX"}],
            "last_cook_diagnostics": {"errors": ["PRIVATE ERROR"], "warnings": []}, "read_failures": 1}}}))
        self.assertIn("attribwrangle", row["detail"])
        self.assertIn("1 parameter value read", row["detail"])
        self.assertIn("last cook: 1 errors", row["detail"])
        self.assertIn("no cook requested", row["detail"])
        self.assertIn("could not be read", row["detail"])
        self.assertNotIn("PRIVATE", str(row))

    def test_schema_shows_control_names_not_values_or_callbacks(self):
        row = activity.describe_call(call("parameter_schema", {"path": "/obj/a", "query": "falloff"}), result({"ok": True, "result": {
            "matched_count": 2, "parameters": [{"name": "falloff1pos", "raw_value": "secret"}, {"name": "falloff1value", "callback": "secret"}]}}))
        self.assertIn("falloff1pos", row["detail"])
        self.assertIn("falloff1value", row["detail"])
        self.assertNotIn("secret", str(row))

    def test_api_lookup_names_symbol_without_dumping_documentation(self):
        row = activity.describe_call(call("hom_help", {"symbol": "hou.RampParmTemplate"}), result({"ok": True, "result": {"documentation": "private doc", "members": []}}))
        self.assertIn("hou.RampParmTemplate", row["title"])
        self.assertIn("no scene commands", row["detail"])
        self.assertNotIn("private doc", str(row))

    def test_proposal_does_not_claim_execution_and_never_renders_source(self):
        request = call("propose_python", {"summary": "Add a falloff ramp", "code": "PRIVATE PYTHON", "target_paths": ["/obj/a"]})
        pending = activity.describe_call(request)
        self.assertEqual(pending["phase"], "preparing")
        self.assertIn("review", pending["title"])
        self.assertIn("falloff ramp", pending["detail"])
        self.assertNotIn("PRIVATE", str(pending))
        done = activity.describe_call(request, result({"success": True, "result": {"secret": "PRIVATE"}, "stdout": "PRIVATE"}))
        self.assertEqual(done["title"], "Ran reviewed Houdini Python")
        self.assertIn("verification follows", done["detail"])
        self.assertNotIn("PRIVATE", str(done))

    def test_success_requires_explicit_outcome(self):
        for outcome in ({}, {"status": "applied"}, {"success": "true"}):
            row = activity.describe_call(call("propose_python"), result(outcome))
            self.assertEqual(row["phase"], "failed")
            self.assertNotEqual(row["title"], "Ran reviewed Houdini Python")

    def test_auto_run_outcomes_do_not_claim_manual_review(self):
        for name, expected in (("propose_python", "Ran Houdini Python automatically"),
                               ("propose_edits", "Applied scene changes automatically")):
            with self.subTest(name=name):
                request = call(name, {"summary": "Add a control"})
                success = activity.describe_call(request, result({
                    "success": True, "authorization": "auto_run", "completed_operations": 1}))
                self.assertEqual(success["title"], expected)
                self.assertNotIn("reviewed", success["title"])
                self.assertIn("verification follows", success["detail"])
                failed = activity.describe_call(request, result({
                    "success": False, "authorization": "auto_run", "execution_started": False}))
                self.assertEqual(failed["phase"], "failed")
                self.assertNotEqual(failed["title"], expected)
        unknown = activity.describe_call(call("propose_python"), result({"success": True, "authorization": True}))
        self.assertEqual(unknown["title"], "Ran reviewed Houdini Python")

    def test_rejection_and_failed_partial_execution_differ(self):
        request = call("propose_python")
        rejected = activity.describe_call(request, result({"status": "rejected", "success": False}))
        self.assertEqual(rejected["title"], "Houdini Python: proposal rejected")
        self.assertIn("Nothing", rejected["detail"])
        failed = activity.describe_call(request, result({"status": "failed", "execution_started": True, "success": False}))
        self.assertIn("Changes may remain", failed["detail"])
        invalid = activity.describe_call(request, result({"status": "invalid", "execution_started": False, "success": False}))
        self.assertIn("No action code was executed", invalid["detail"])

    def test_typed_edits_report_counts_paths_and_actual_rollback(self):
        request = call("propose_edits", {"summary": "Build network", "operations": [{"value": "SECRET"}]})
        done = activity.describe_call(request, result({"success": True, "completed_operations": 7, "changed_paths": ["/obj/demo", "/obj/demo/grid"]}))
        self.assertIn("7 operations completed", done["detail"])
        self.assertIn("/obj/demo/grid", done["detail"])
        self.assertNotIn("SECRET", str(done))
        failed = activity.describe_call(request, result({"success": False, "rollback": "reverted_scene_fields", "manual_undo_needed": False}))
        self.assertIn("were reverted", failed["detail"])

    def test_batch_pairs_by_id_and_groups_only_adjacent_duplicates(self):
        calls = [call("list_children", {"path": "/obj"}, "one"), call("list_children", {"path": "/stage"}, "two")]
        results = [result({"ok": False}, "two"), result({"ok": True, "result": {"child_count": 0}}, "one")]
        rows = activity.summarize_batch(calls, results)
        self.assertEqual(rows[0]["title"], "/obj is empty")
        self.assertEqual(rows[1]["phase"], "failed")
        repeated = activity.format_batch([calls[0], calls[0], calls[1], calls[0]])
        self.assertIn("(×2)", repeated)
        self.assertEqual(len(activity.summarize_batch([calls[0], calls[0], calls[1], calls[0]])), 3)

    def test_malformed_missing_results_are_not_pending_or_success(self):
        for value in (None, 1, [], {"function": []}, {"function": {"arguments": "not JSON"}}):
            self.assertIsInstance(activity.describe_call(value), dict)
        row = activity.summarize_batch([call("get_selection")], [])[0]
        self.assertEqual(row["phase"], "failed")

    def test_activity_is_bounded_even_with_long_or_malformed_identifiers(self):
        request = call("propose_python", {"summary": "X\n\x00" * 10000, "code": "NEVER SHOW THIS"})
        row = activity.describe_call(request)
        self.assertLessEqual(len(row["title"]), activity.MAX_TITLE)
        self.assertLessEqual(len(row["detail"]), activity.MAX_DETAIL)
        self.assertNotIn("\n", row["detail"])
        self.assertNotIn("\x00", row["detail"])
        self.assertEqual(len(activity.summarize_batch([call("inspect_node", {"path": str(i)}) for i in range(100)])), 8)


if __name__ == "__main__":
    unittest.main()
