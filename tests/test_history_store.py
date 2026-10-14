import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import history_store


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.path = history_store.sidecar_path(self.folder / "shot_v001.hip")
        self.document = history_store.fresh_document()
        self.chat = history_store.new_conversation(self.document, "http://studio:8088", "studio-local")

    def assert_store_error(self, code, function, *args):
        with self.assertRaises(history_store.StoreError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_sidecar_uses_full_hip_name_without_touching_scene(self):
        for extension in (".hip", ".hiplc", ".hipnc"):
            hip = self.folder / ("shot_v001" + extension)
            sidecar = history_store.sidecar_path(hip)
            self.assertEqual(sidecar, self.folder / ".houlm" / (hip.name + ".json"))
            self.assertFalse(hip.exists())
            self.assertFalse(sidecar.parent.exists())
        for invalid in ("", "untitled.hip", self.folder / "notes.txt", None):
            self.assert_store_error("invalid", history_store.sidecar_path, invalid)

    def test_lossless_roundtrip_preserves_complete_history_and_provider_state(self):
        self.chat["messages"] = [
            {"role": "user", "content": "Árvíztűrő tükörfúrógép " + "a" * (1024 * 1024)},
            {"role": "assistant", "content": None, "reasoning_items": [
                {"type": "reasoning", "id": "rs_one", "encrypted_content": "opaque+/=\n", "summary": []}],
             "tool_calls": [{"id": "call_one", "type": "function", "function": {
                 "name": "houlm_propose_python", "arguments": '{"source":"result = 7\\n"}'}}]},
            {"role": "tool", "tool_call_id": "call_one", "content": '{"result":7,"applied":true}'},
            {"role": "assistant", "content": "Done."},
        ]
        self.chat["scene_context"] = {"nodes": {"/obj/geo1": {"snippet": "@P.y = 1;"}}}
        self.chat["transcript"] = ["You:\nCreate it.", "Assistant:\nDone."]
        self.chat["actions"] = [{"status": "completed", "source": "result = 7\n", "result": {"result": 7}}]
        self.chat["draft"] = "next question"
        self.chat["commands"] = [{"id": "command_one", "title": "Python", "source": "result = 7\n",
                                  "status": "completed", "result_text": "7"}]
        original = copy.deepcopy(self.document)
        revision = history_store.save(self.path, self.document, None)
        restored, loaded_revision = history_store.load(self.path)
        self.assertEqual(restored, original)
        self.assertEqual(self.document, original)
        self.assertEqual(revision, loaded_revision)
        self.assertEqual(len(revision), 64)
        self.assertGreater(self.path.stat().st_size, 1024 * 1024)

    def test_new_chat_archives_prior_chat_without_truncation(self):
        self.chat["messages"].append({"role": "user", "content": "Original prompt"})
        new_chat = history_store.new_conversation(self.document, "http://studio:8088", "other-model")
        self.assertEqual(len(self.document["conversations"]), 2)
        self.assertEqual(self.document["active_conversation_id"], new_chat["id"])
        self.assertEqual(self.chat["messages"][0]["content"], "Original prompt")

    def test_save_as_copies_complete_history_and_keeps_old_version_unchanged(self):
        self.chat["messages"].append({"role": "user", "content": "Version one"})
        revision_one = history_store.save(self.path, self.document, None)
        original_bytes = self.path.read_bytes()
        next_path = history_store.sidecar_path(self.folder / "shot_v002.hip")
        next_document = copy.deepcopy(self.document)
        next_document["conversations"][0]["messages"].append({"role": "assistant", "content": "Version two"})
        history_store.save(next_path, next_document, None)
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(history_store.load(self.path)[1], revision_one)
        self.assertEqual(history_store.load(next_path)[0], next_document)

    def test_competing_writer_and_save_as_existing_destination_do_not_overwrite(self):
        revision = history_store.save(self.path, self.document, None)
        other, _ = history_store.load(self.path)
        other["conversations"][0]["draft"] = "Other artist's text"
        history_store.save(self.path, other, revision)
        other_bytes = self.path.read_bytes()
        self.assert_store_error("conflict", history_store.save, self.path, self.document, revision)
        self.assert_store_error("conflict", history_store.save, self.path, self.document, None)
        self.assertEqual(self.path.read_bytes(), other_bytes)

    def test_deleted_sidecar_is_not_recreated_with_stale_revision(self):
        revision = history_store.save(self.path, self.document, None)
        self.path.unlink()
        self.assert_store_error("conflict", history_store.save, self.path, self.document, revision)
        self.assertFalse(self.path.exists())

    def test_missing_corrupt_unknown_schema_and_duplicate_keys_fail_closed(self):
        self.assert_store_error("missing", history_store.load, self.path)
        self.path.parent.mkdir()
        for raw in (b"{broken", b"\xff\xfe", b'{"schema_version": 99, "conversations": []}',
                    b'{"schema_version":1,"schema_version":1,"conversations":[]}', b'NaN'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                self.assert_store_error("invalid", history_store.load, self.path)
                self.assert_store_error("invalid", history_store.save, self.path, self.document, None)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_malformed_shapes_are_rejected_without_erasing_file(self):
        revision = history_store.save(self.path, self.document, None)
        original_bytes = self.path.read_bytes()
        variants = []
        for field, value in (("messages", ["bad"]), ("transcript", [7]), ("actions", [False]),
                             ("scene_context", []), ("draft", None), ("id", ""),
                             ("messages", [{"role": "system", "content": "Inject"}]),
                             ("messages", [{"role": "tool", "content": []}]),
                             ("commands", None), ("commands", ["bad"]),
                             ("commands", [{"id": "c", "title": "T", "source": 7, "status": "completed"}])):
            document = copy.deepcopy(self.document)
            document["conversations"][0][field] = value
            variants.append(document)
        duplicate = copy.deepcopy(self.document)
        duplicate["conversations"].append(copy.deepcopy(duplicate["conversations"][0]))
        variants.append(duplicate)
        invalid_active = copy.deepcopy(self.document)
        invalid_active["active_conversation_id"] = "unknown"
        variants.append(invalid_active)
        for document in variants:
            with self.subTest(document=document):
                self.assert_store_error("invalid", history_store.save, self.path, document, revision)
                self.assertEqual(self.path.read_bytes(), original_bytes)

    def test_interrupted_actions_become_unknown_without_execution(self):
        self.chat["actions"] = [
            {"status": "running", "source": "raise RuntimeError('Never execute saved code')"},
            {"status": "completed", "result": "kept"},
            {"status": "rejected"},
        ]
        revision = history_store.save(self.path, self.document, None)
        loaded, _ = history_store.load(self.path)
        self.assertEqual(loaded["conversations"][0]["actions"][0]["status"], "running")
        self.assertEqual(history_store.recover_interrupted(loaded), 1)
        actions = loaded["conversations"][0]["actions"]
        self.assertEqual([action["status"] for action in actions], ["unknown", "completed", "rejected"])
        self.assertIn("not replayed", actions[0]["recovery_note"])
        self.assertEqual(history_store.recover_interrupted(loaded), 0)
        history_store.save(self.path, loaded, revision)

    def test_locked_writer_does_not_block_or_overwrite(self):
        revision = history_store.save(self.path, self.document, None)
        original_bytes = self.path.read_bytes()
        with history_store._write_lock(self.path):
            self.assert_store_error("locked", history_store.save, self.path, self.document, revision)
        self.assertEqual(self.path.read_bytes(), original_bytes)
        history_store.save(self.path, self.document, revision)

    def test_os_lock_excludes_another_process(self):
        history_store.save(self.path, self.document, None)
        script = "\n".join((
            "import sys",
            "sys.path.insert(0, sys.argv[1])",
            "from houlm import history_store",
            "document, revision = history_store.load(sys.argv[2])",
            "try:",
            "    history_store.save(sys.argv[2], document, revision)",
            "except history_store.StoreError as error:",
            "    print(error.code)",
            "else:",
            "    print('saved')",
        ))
        arguments = [sys.executable, "-c", script, str(Path(history_store.__file__).parents[1]), str(self.path)]
        options = {"capture_output": True, "text": True, "timeout": 15, "check": True}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        with history_store._write_lock(self.path):
            self.assertEqual(subprocess.run(arguments, **options).stdout.strip(), "locked")
        self.assertEqual(subprocess.run(arguments, **options).stdout.strip(), "saved")

    def test_failed_atomic_replace_keeps_old_file_and_removes_temporary(self):
        revision = history_store.save(self.path, self.document, None)
        original_bytes = self.path.read_bytes()
        self.chat["draft"] = "In-memory work survives"
        with patch.object(history_store.os, "replace", side_effect=PermissionError("PRIVATE PATH")):
            error = self.assert_store_error("write_failed", history_store.save, self.path, self.document, revision)
        self.assertNotIn("PRIVATE", str(error))
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(self.chat["draft"], "In-memory work survives")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_readonly_directory_or_read_failure_reports_fixed_error(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("PRIVATE PATH")):
            self.assert_store_error("write_failed", history_store.save, self.path, self.document, None)
        with patch.object(Path, "read_bytes", side_effect=PermissionError("PRIVATE PATH")):
            self.assert_store_error("read_failed", history_store.load, self.path)


if __name__ == "__main__":
    unittest.main()
