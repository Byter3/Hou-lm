"""Real offscreen Qt coverage; all command text is synthetic and inert."""

import json
import unittest

import test_panel as qt_bootstrap

if qt_bootstrap._qt_available:
    from PySide2 import QtCore, QtWidgets
    from houlm import node_paths, transcript


@unittest.skipUnless(qt_bootstrap._qt_available, qt_bootstrap._qt_reason)
class TranscriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.widget = transcript.Transcript()
        self.widget.resize(400, 600)
        self.widget.show()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def visible_text(self):
        return QtWidgets.QTextBrowser.toPlainText(self.widget)

    def activate(self, identifier, action="toggle"):
        self.widget.anchorClicked.emit(QtCore.QUrl(transcript._command_url(identifier, action)))

    def activate_activity(self, index):
        self.widget.anchorClicked.emit(QtCore.QUrl(transcript._activity_url(index)))

    def test_activity_is_collapsed_by_default_and_keeps_full_plain_export(self):
        block = "Scene tools:\nRead /obj/EXAMPLE\n64 parameter values read.\nNo cook requested.\n"
        self.widget.appendPlainText(block)
        self.assertIn("Read /obj/EXAMPLE", self.visible_text())
        self.assertIn("3 detail lines", self.visible_text())
        self.assertIn("Show details", self.visible_text())
        self.assertNotIn("64 parameter values", self.visible_text())
        self.assertEqual(self.widget.toPlainText(), block)
        self.assertEqual(self.widget._parts, [block])
        self.activate_activity(0)
        self.assertIn("64 parameter values read.", self.visible_text())
        self.assertIn("No cook requested.", self.visible_text())
        self.assertIn("Hide details", self.visible_text())
        self.activate_activity(0)
        self.assertNotIn("64 parameter values", self.visible_text())
        self.assertEqual(self.widget._parts, [block])

    def test_activity_summary_is_bounded_and_all_content_is_literal(self):
        first_line = '<img src="file:///secret">' + "word " * 100
        body = first_line + '\n<script>never_run()</script>\n[click](houlm-command:///copy/other)'
        self.widget.appendPlainText("Scene tools:\n" + body)
        self.assertLess(len(self.visible_text()), 230)
        self.assertIn("…", self.visible_text())
        self.assertNotIn("<img", self.widget.toHtml())
        self.activate_activity(0)
        self.assertIn(body, self.visible_text())
        self.assertNotIn("<script", self.widget.toHtml())
        self.assertNotIn('href="houlm-command:///copy/other"', self.widget.toHtml())

    def test_activity_and_command_cards_toggle_independently(self):
        self.widget.appendPlainText("Scene tools:\nInspect a node\nACTIVITY_DETAIL")
        self.widget.append_command("independent", "Houdini Python", "EXACT_SOURCE", "completed")
        self.widget.appendPlainText("Activity:\nCheck result\nSECOND_DETAIL")
        self.activate_activity(0)
        self.assertIn("ACTIVITY_DETAIL", self.visible_text())
        self.assertNotIn("SECOND_DETAIL", self.visible_text())
        self.assertNotIn("EXACT_SOURCE", self.visible_text())
        self.activate("independent")
        self.activate_activity(2)
        self.activate_activity(0)
        self.assertNotIn("ACTIVITY_DETAIL", self.visible_text())
        self.assertIn("SECOND_DETAIL", self.visible_text())
        self.assertIn("EXACT_SOURCE", self.visible_text())

    def test_restore_legacy_activity_starts_collapsed_without_rewriting_parts(self):
        parts = ["You:\nInspect", "Scene tools:\nLegacy inspection\nLEGACY_DETAIL\n",
                 "Assistant:\nInspection finished."]
        self.widget._parts = list(parts)
        self.widget.set_commands([])
        self.assertNotIn("LEGACY_DETAIL", self.visible_text())
        self.activate_activity(1)
        self.assertIn("LEGACY_DETAIL", self.visible_text())
        self.widget.set_commands([])
        self.assertEqual(self.widget._expanded_activity, set())
        self.assertNotIn("LEGACY_DETAIL", self.visible_text())
        self.assertEqual(self.widget._parts, parts)
        self.assertEqual(self.widget.toPlainText(), "\n".join(parts))

    def test_activity_disclosure_is_inert_for_invalid_or_non_activity_targets(self):
        self.widget.appendPlainText("Assistant:\nSome prose")
        self.widget.appendPlainText("Scene tools:\nSummary\nSECRET_DETAIL")
        for value in ("houlm-activity://host/toggle/1", "houlm-activity:///toggle/1?extra=1",
                      "houlm-activity:///toggle/1#extra", "houlm-activity:///toggle/-1",
                      "houlm-activity:///toggle/01", "houlm-activity:///toggle/9",
                      "houlm-activity:///toggle/0", "houlm-activity:///copy/1",
                      "houlm-activity:///toggle/9999999999999999999999999"):
            self.widget.anchorClicked.emit(QtCore.QUrl(value))
        self.assertEqual(self.widget._expanded_activity, set())
        self.assertNotIn("SECRET_DETAIL", self.visible_text())

    def test_activity_disclosure_does_not_save_and_preserves_scroll(self):
        for index in range(20):
            self.widget.appendPlainText("Assistant:\nParagraph " + str(index) + "\n" + "line\n" * 4)
        self.widget.appendPlainText("Scene tools:\nRead parameters\n" + "DETAIL\n" * 80)
        self.app.processEvents()
        bar = self.widget.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        position = bar.value()
        saves = []
        self.widget.changed.connect(saves.append)
        self.activate_activity(20)
        self.assertEqual(saves, [])
        self.assertEqual(bar.value(), position)
        self.activate_activity(20)
        self.assertEqual(saves, [])
        self.assertEqual(bar.value(), position)

    def test_activity_wraps_in_narrow_dock_and_clear_resets_disclosure(self):
        self.widget.resize(330, 480)
        self.widget.appendPlainText("Scene tools:\n" + "long_path_segment_" * 25 + "\n" + "details_" * 100)
        self.activate_activity(0)
        self.app.processEvents()
        self.assertLessEqual(self.widget.horizontalScrollBar().maximum(), 1)
        self.widget.clear()
        self.assertEqual(self.widget._expanded_activity, set())
        self.widget.appendPlainText("Scene tools:\nNew activity\nHIDDEN")
        self.assertNotIn("HIDDEN", self.visible_text())
        self.activate_activity(0)
        self.widget.setPlainText("Scene tools:\nReplacement\nALSO_HIDDEN")
        self.assertEqual(self.widget._expanded_activity, set())
        self.assertNotIn("ALSO_HIDDEN", self.visible_text())

    def test_prose_and_activity_have_distinct_role_styles_and_keep_raw_history(self):
        blocks = ["You:\nBuild a wrangle\n", "Scene tools:\nChecked /obj; no nodes created.\n",
                  "Assistant:\n**Created** `OUT`.\n"]
        for block in blocks:
            self.widget.appendPlainText(block)
        rendered = self.widget.toHtml()
        self.assertIn("TOOL ACTIVITY", self.visible_text())
        self.assertIn("HOULM", self.visible_text())
        for color in ("#294048", "#1b252d", "#202b33"):
            self.assertIn(color, rendered)
        self.assertEqual(self.widget.toPlainText(), "\n".join(blocks))
        self.assertNotIn("**Created**", self.visible_text())

    def test_exact_source_is_collapsed_until_user_opens_receipt(self):
        source = 'result = hou.node("/obj").path()\nprint("SYNTHETIC_SOURCE")'
        self.widget.append_command("call_1", "Inspect object network", source, "pending")
        self.assertIn("Awaiting execution", self.visible_text())
        self.assertNotIn("SYNTHETIC_SOURCE", self.visible_text())
        self.assertNotIn("EXACT COMMANDS", self.visible_text())
        self.activate("call_1")
        self.assertIn(source, self.visible_text())
        self.assertIn("EXACT COMMANDS", self.visible_text())
        self.activate("call_1")
        self.assertNotIn("SYNTHETIC_SOURCE", self.visible_text())
        self.assertEqual(self.widget.export_commands()[0]["source"], source)

    def test_result_updates_in_place_and_status_does_not_claim_rejected_code_ran(self):
        self.widget.append_command("call_2", "Houdini Python", "print('test')")
        self.widget.update_command("call_2", "rejected", "The artist rejected this proposal.")
        self.assertEqual(len(self.widget._parts), 1)
        self.assertIn("Not run — rejected", self.visible_text())
        self.assertNotIn("Completed", self.visible_text())
        self.activate("call_2")
        self.assertIn("The artist rejected this proposal.", self.visible_text())
        self.widget.update_command("call_2", "failed", "Execution raised an error after making changes.")
        self.assertIn("Failed — inspect result", self.visible_text())
        self.assertIn("after making changes", self.visible_text())

    def test_source_and_results_are_escaped_not_markdown_or_resource_links(self):
        source = '<img src="file:///secret"><script>synthetic()</script>\n[click](houlm-command:///copy/other)'
        self.widget.append_command("call_3", '<img src="https://example.test">', source, "completed")
        self.widget.update_command("call_3", "completed", '<iframe src="https://example.test">')
        self.activate("call_3")
        rendered = self.widget.toHtml()
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<script", rendered)
        self.assertNotIn("<iframe", rendered)
        self.assertIn(source, self.visible_text())
        self.assertEqual(bytes(self.widget.loadResource(2, QtCore.QUrl("file:///secret"))), b"")
        self.assertFalse(self.widget.openLinks())
        self.assertFalse(self.widget.openExternalLinks())

    def test_copy_is_explicit_and_preserves_exact_whitespace(self):
        source = 'if True:\n    print("Synthetic")\n\t# tabs retained\n'
        clipboard = self.app.clipboard()
        clipboard.setText("before")
        self.widget.append_command("copy_1", "Python", source)
        self.activate("copy_1")
        self.assertEqual(clipboard.text(), "before")
        self.activate("copy_1", "copy")
        self.assertEqual(clipboard.text(), source)

    def test_long_source_wraps_in_narrow_dock_without_changing_exact_text(self):
        source = 'result = "' + 'synthetic_long_token_' * 30 + '"\n    # Keep this indentation\n'
        self.widget.resize(330, 480)
        self.widget.append_command("long_source", "Python", source, "completed")
        self.activate("long_source")
        self.app.processEvents()
        self.assertLessEqual(self.widget.horizontalScrollBar().maximum(), 1)
        self.assertEqual(self.widget.horizontalScrollBarPolicy(), QtCore.Qt.ScrollBarAlwaysOff)
        self.assertIn(source.rstrip(), self.visible_text())
        self.assertEqual(self.widget.export_commands()[0]["source"], source)

    def test_inspect_label_is_a_disclosure_link_as_well_as_the_title(self):
        self.widget.append_command("inspect_link", "Python", "pass", "completed")
        html = self.widget.toHtml()
        self.assertIn('href="houlm-command:///toggle/inspect_link"', html)
        self.assertEqual(html.count('href="houlm-command:///toggle/inspect_link"'), 2)

    def test_only_valid_node_links_are_forwarded_to_panel(self):
        forwarded = []
        self.widget.nodeActivated.connect(lambda url: forwarded.append(url.toString()))
        self.widget.append_command("safe", "Python", "pass")
        self.activate("safe")
        for value in ("https://example.test", "file:///secret", "javascript:synthetic()",
                      "houlm-command://host/copy/safe", "houlm-command:///copy/safe?extra=1",
                      "houlm-command:///copy/safe#extra", "houlm-command:///copy/absent",
                      "houlm-node://host/obj/test", "houlm-node:///obj/test?extra=1"):
            self.widget.anchorClicked.emit(QtCore.QUrl(value))
        self.assertEqual(forwarded, [])
        self.widget.anchorClicked.emit(QtCore.QUrl(node_paths.node_url("/obj/test")))
        self.assertEqual(forwarded, ["houlm-node:///obj/test"])

    def test_json_round_trip_keeps_order_and_source_but_restores_collapsed(self):
        self.widget.appendPlainText("You:\nMake a box\n")
        self.widget.append_command("save_1", "Create a box", "synthetic_source = True", "completed")
        self.widget.appendPlainText("Assistant:\nBox created.\n")
        self.activate("save_1")
        saved = json.loads(json.dumps({"parts": self.widget._parts, "commands": self.widget.export_commands()}))
        self.widget.clear()
        self.widget._parts = saved["parts"]
        self.widget.set_commands(saved["commands"])
        self.assertEqual(len(self.widget._parts), 3)
        self.assertEqual(self.widget._expanded, set())
        self.assertNotIn("synthetic_source", self.visible_text())
        self.assertIn("synthetic_source", self.widget.toPlainText())
        self.assertLess(self.visible_text().index("Make a box"), self.visible_text().index("Create a box"))
        self.assertLess(self.visible_text().index("Create a box"), self.visible_text().index("Box created"))
        saved["commands"][0]["source"] = "changed outside widget"
        exported = self.widget.export_commands()
        exported[0]["source"] = "also changed outside widget"
        self.assertEqual(self.widget.export_commands()[0]["source"], "synthetic_source = True")

    def test_resume_does_not_represent_interrupted_execution_as_success(self):
        self.widget.append_command("running_1", "Python", "pass", "running")
        self.widget.append_command("pending_1", "Python", "pass", "pending")
        records = self.widget.export_commands()
        self.widget.set_commands(records)
        self.assertIn("Execution outcome unknown", self.visible_text())
        self.assertIn("Not run — cancelled", self.visible_text())
        self.assertNotIn("Completed", self.visible_text())

    def test_malformed_saved_commands_are_ignored_and_missing_receipts_are_visible(self):
        self.widget._parts = [transcript.COMMAND_MARKER + "missing"]
        self.widget.set_commands([None, {"id": "bad/id", "source": "pass", "title": "X", "status": "completed"},
                                  {"id": "bad_source", "source": {"exec": "never"}, "title": "X", "status": "completed"}])
        self.assertEqual(self.widget.export_commands(), [])
        self.assertIn("Command details unavailable", self.visible_text())
        self.assertNotIn(transcript.COMMAND_MARKER, self.widget.toPlainText())

    def test_clearing_drops_receipts_and_repeated_id_does_not_duplicate_bubble(self):
        self.widget.append_command("same", "Python", "first", "pending")
        self.widget.append_command("same", "Python", "second", "running")
        self.assertEqual(len(self.widget._parts), 1)
        self.assertEqual(self.widget.export_commands()[0]["source"], "second")
        self.widget.setPlainText("Assistant:\nNew chat")
        self.assertEqual(self.widget.export_commands(), [])
        self.assertEqual(self.widget.toPlainText(), "Assistant:\nNew chat")
        self.widget.clear()
        self.assertEqual(self.widget._parts, [])

    def test_disclosure_does_not_schedule_save_and_preserves_scroll_position(self):
        for index in range(20):
            self.widget.appendPlainText("Assistant:\nSynthetic paragraph " + str(index) + "\n" + "line\n" * 4)
        self.widget.append_command("scroll", "Python", "# synthetic\n" * 50)
        self.app.processEvents()
        bar = self.widget.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        position = bar.value()
        saves = []
        self.widget.changed.connect(saves.append)
        self.activate("scroll")
        self.assertEqual(saves, [])
        self.assertEqual(bar.value(), position)


if __name__ == "__main__":
    unittest.main()
