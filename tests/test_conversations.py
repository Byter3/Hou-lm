import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import conversations, history_store


EVENTS = SimpleNamespace(**{name: name for name in (
    "BeforeLoad", "AfterLoad", "BeforeClear", "AfterClear", "BeforeSave", "AfterSave")})


class FakeHipFile:
    def __init__(self, path, new=True):
        self.filename = str(path)
        self.new = new
        self.callbacks = []

    def path(self):
        return self.filename

    def isNewFile(self):
        return self.new

    def addEventCallback(self, callback):
        self.callbacks.append(callback)

    def removeEventCallback(self, callback):
        self.callbacks.remove(callback)

    def emit(self, event):
        for callback in list(self.callbacks):
            callback(event)

    def save_as(self, path):
        self.emit(EVENTS.BeforeSave)
        self.filename, self.new = str(path), False
        self.emit(EVENTS.AfterSave)

    def load(self, path):
        self.emit(EVENTS.BeforeLoad)
        self.filename, self.new = str(path), False
        self.emit(EVENTS.AfterLoad)

    def clear(self, path):
        self.emit(EVENTS.BeforeClear)
        self.filename, self.new = str(path), True
        self.emit(EVENTS.AfterClear)


class ConversationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.hou = SimpleNamespace(session=SimpleNamespace(), hipFileEventType=EVENTS,
                                   hipFile=FakeHipFile(self.folder / "untitled.hip"))
        self.book = conversations.Conversations(self.hou)
        self.addCleanup(self.book.close)

    def listen(self, book=None):
        book = book or self.book
        book.listen(book.save, lambda: None, book.save, lambda: None)

    def test_unsaved_history_and_draft_survive_panel_reload_without_writing_files(self):
        self.book.active["messages"] = [{"role": "user", "content": "Unsaved scene"}]
        self.book.active["draft"] = "Next question"
        self.assertTrue(self.book.save())
        self.book.close()
        reloaded = conversations.Conversations(self.hou)
        self.assertEqual(reloaded.document, self.book.document)
        self.assertIsNot(reloaded.document, self.book.document)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_saved_default_filename_is_not_mistaken_for_an_unsaved_scene(self):
        filename = self.folder / "untitled.hip"
        filename.write_bytes(b"Synthetic file existence only; not a real scene")
        # The native 20.5.445 save fixture confirms this state after Save.
        self.hou.hipFile.new = False
        self.book.open_current(use_memory=False)
        self.assertEqual(self.book.path, history_store.sidecar_path(filename))
        self.assertFalse(self.book.saved)
        self.book.active["draft"] = "Keep my default-name scene chat"
        self.assertTrue(self.book.save())
        self.assertTrue(self.book.saved)
        self.assertEqual(history_store.load(self.book.path)[0], self.book.document)

    def test_existing_default_file_does_not_attach_history_to_never_saved_scene(self):
        filename = self.folder / "untitled.hip"
        filename.write_bytes(b"An older HIP already occupies the default path")
        old_document = history_store.fresh_document()
        history_store.new_conversation(old_document)["draft"] = "Other saved scene"
        sidecar = history_store.sidecar_path(filename)
        history_store.save(sidecar, old_document, None)
        original = sidecar.read_bytes()
        # No Save/Load event belongs to this new scene; disk existence must
        # not override isNewFile=True during first panel initialization.
        replacement = conversations.Conversations(self.hou)
        self.assertIsNone(replacement.path)
        self.assertEqual(replacement.active["draft"], "")
        replacement.active["draft"] = "New unsaved scene only"
        self.assertTrue(replacement.save())
        self.assertFalse(replacement.saved)
        self.assertEqual(sidecar.read_bytes(), original)

    def test_completed_save_evidence_overrides_new_flag_for_current_scene_only(self):
        filename = self.folder / "untitled.hip"
        self.book.active["draft"] = "This scene was actually saved"
        self.hou.hipFile.save_as(filename)
        self.hou.hipFile.new = True
        self.assertTrue(self.book.save())
        self.assertEqual(self.book.path, history_store.sidecar_path(filename))
        self.assertEqual(history_store.load(self.book.path)[0], self.book.document)

    def test_clear_to_existing_default_filename_does_not_reuse_its_history(self):
        filename = self.folder / "untitled.hip"
        filename.write_bytes(b"Synthetic saved file")
        self.hou.hipFile.new = False
        self.book.open_current(use_memory=False)
        self.book.active["draft"] = "Saved file's chat"
        self.book.save()
        original = self.book.path.read_bytes()
        self.listen()
        self.hou.hipFile.clear(filename)
        self.assertIsNone(self.book.path)
        self.assertEqual(self.book.active["draft"], "")
        self.book.active["draft"] = "New untitled scene"
        self.assertTrue(self.book.save())
        self.assertEqual(history_store.sidecar_path(filename).read_bytes(), original)

    def test_checkpoint_recovers_missed_panel_save_notification(self):
        # Only the process-level callback is installed, not the panel listener.
        self.book.active["draft"] = "Before saving"
        first = self.folder / "shot_v001.hip"
        self.hou.hipFile.save_as(first)
        self.assertIsNone(self.book.path)
        self.assertTrue(self.book.save())
        self.assertTrue(self.book.saved)
        self.assertEqual(self.book.path, history_store.sidecar_path(first))
        original = self.book.path.read_bytes()
        second = self.folder / "shot_v002.hip"
        self.hou.hipFile.save_as(second)
        self.book.active["draft"] = "Version two"
        self.assertTrue(self.book.save())
        self.assertEqual(self.book.path, history_store.sidecar_path(second))
        self.assertEqual(history_store.sidecar_path(first).read_bytes(), original)

    def test_first_save_while_panel_closed_keeps_unsaved_chat_on_reopen(self):
        self.book.active["draft"] = "Chat before closing the panel"
        self.book.save()
        self.book.close()
        filename = self.folder / "first_saved.hip"
        self.hou.hipFile.save_as(filename)
        replacement = conversations.Conversations(self.hou)
        self.assertEqual(replacement.active["draft"], "Chat before closing the panel")
        self.assertEqual(replacement.path, history_store.sidecar_path(filename))
        self.assertTrue(replacement.save())
        self.assertEqual(history_store.load(replacement.path)[0], replacement.document)

    def test_checkpoint_after_missed_load_callback_never_writes_to_new_scene(self):
        first, second = self.folder / "first.hip", self.folder / "second.hip"
        self.hou.hipFile.save_as(first)
        self.book.save()
        old_path = self.book.path
        original = old_path.read_bytes()
        self.book.active["draft"] = "Old scene only"
        self.hou.hipFile.load(second)
        self.assertFalse(self.book.save())
        self.assertEqual(self.book.error_code, "scene_changed")
        self.assertEqual(self.book.active["draft"], "Old scene only")
        self.assertEqual(old_path.read_bytes(), original)
        self.assertFalse(history_store.sidecar_path(second).exists())
        self.assertIsNone(self.hou.session._houlm_conversations)

    def test_changed_filename_without_save_event_does_not_migrate_history(self):
        self.hou.hipFile.save_as(self.folder / "first.hip")
        self.book.save()
        previous = self.book.path
        self.hou.hipFile.filename = str(self.folder / "renamed.hip")
        self.assertFalse(self.book.save())
        self.assertEqual(self.book.error_code, "unconfirmed_path")
        self.assertEqual(self.book.path, previous)
        self.assertFalse(history_store.sidecar_path(self.hou.hipFile.filename).exists())
        self.hou.hipFile.save_as(self.hou.hipFile.filename)
        self.assertTrue(self.book.save())
        self.assertEqual(self.book.error_code, "")

    def test_missing_path_api_is_reported_instead_of_claiming_unsaved_scene(self):
        book = conversations.Conversations(SimpleNamespace())
        self.assertEqual(book.error_code, "path_unavailable")
        self.assertFalse(book.saved)
        self.assertFalse(book.save())

    def test_closed_panel_does_not_restore_old_untitled_chat_in_new_untitled_scene(self):
        self.book.active["draft"] = "Belongs to old untitled scene"
        self.book.save()
        self.book.close()
        self.hou.hipFile.clear(self.folder / "untitled.hip")
        reloaded = conversations.Conversations(self.hou)
        self.assertEqual(reloaded.active["draft"], "")
        self.assertNotEqual(reloaded.active["id"], self.book.active["id"])

    def test_first_save_and_save_as_copy_full_conversations_without_changing_old_json(self):
        self.listen()
        self.book.active["messages"] = [{"role": "user", "content": "Before first save"}]
        first = self.folder / "shot_v001.hip"
        self.hou.hipFile.save_as(first)
        first_sidecar = history_store.sidecar_path(first)
        first_book, _ = history_store.load(first_sidecar)
        self.assertEqual(first_book, self.book.document)
        self.book.new(model="studio-chatgpt")
        self.book.active["messages"] = [{"role": "user", "content": "Second conversation"}]
        self.book.save()
        old_bytes = first_sidecar.read_bytes()
        second = self.folder / "shot_v002.hip"
        self.hou.hipFile.save_as(second)
        self.assertEqual(first_sidecar.read_bytes(), old_bytes)
        self.assertEqual(history_store.load(history_store.sidecar_path(second))[0], self.book.document)
        self.book.active["draft"] = "Only version two changes now"
        self.book.save()
        self.assertEqual(first_sidecar.read_bytes(), old_bytes)
        self.assertEqual(len(self.book.document["conversations"]), 2)

    def test_switch_snapshots_old_scene_and_cancels_before_new_history_is_loaded(self):
        first = self.folder / "first.hip"
        second = self.folder / "second.hip"
        self.hou.hipFile.filename, self.hou.hipFile.new = str(first), False
        self.book.after_save()
        self.book.active["draft"] = "Old draft before switch"
        original_id = self.book.active["id"]
        other = history_store.fresh_document()
        other_chat = history_store.new_conversation(other)
        other_chat["draft"] = "New scene draft"
        history_store.save(history_store.sidecar_path(second), other, None)
        order = []

        def before():
            order.append(("snapshot", self.book.active["id"], self.hou.hipFile.path()))
            self.book.save()
            order.append(("cancel", self.book.active["id"], self.hou.hipFile.path()))

        def after():
            order.append(("restore", self.book.active["id"], self.hou.hipFile.path()))

        self.book.listen(before, after, self.book.save, lambda: None)
        self.hou.hipFile.load(second)
        self.assertEqual([item[0] for item in order], ["snapshot", "cancel", "restore"])
        self.assertEqual(order[0][1:], (original_id, str(first)))
        self.assertEqual(order[1][1:], (original_id, str(first)))
        self.assertEqual(order[2][1:], (other_chat["id"], str(second)))
        self.assertEqual(history_store.load(history_store.sidecar_path(first))[0]["conversations"][0]["draft"],
                         "Old draft before switch")
        self.assertEqual(self.book.active["draft"], "New scene draft")

    def test_active_panel_clear_resets_memory_and_reloader_does_not_duplicate_callbacks(self):
        self.listen()
        self.book.active["draft"] = "Old draft"
        self.book.save()
        self.hou.hipFile.clear(self.folder / "untitled.hip")
        self.assertEqual(self.book.active["draft"], "")
        self.assertEqual(len(self.hou.hipFile.callbacks), 2)  # Process invalidator + panel listener.
        self.book.close()
        self.assertEqual(len(self.hou.hipFile.callbacks), 1)
        replacement = conversations.Conversations(self.hou)
        self.listen(replacement)
        self.assertEqual(len(self.hou.hipFile.callbacks), 2)
        replacement.close()
        replacement.close()
        self.assertEqual(len(self.hou.hipFile.callbacks), 1)

    def test_reopen_same_saved_hip_reads_disk_even_if_old_memory_exists(self):
        filename = self.folder / "shot.hip"
        self.hou.hipFile.filename, self.hou.hipFile.new = str(filename), False
        self.book.after_save()
        saved = copy.deepcopy(self.book.document)
        self.book.active["draft"] = "Unsaved runtime only"
        self.book._memory()
        self.book.close()
        self.hou.hipFile.load(filename)
        reopened = conversations.Conversations(self.hou)
        self.assertEqual(reopened.document, saved)

    def test_load_recovery_cancels_reviews_and_marks_running_execution_unknown(self):
        filename = self.folder / "shot.hip"
        self.book.active.update(turn_state="review", actions=[
            {"status": "running", "source": "Never replay"}, {"status": "review"},
            {"status": "completed", "result": {"applied": True}},
        ])
        history_store.save(history_store.sidecar_path(filename), self.book.document, None)
        self.hou.hipFile.filename, self.hou.hipFile.new = str(filename), False
        self.book.open_current(use_memory=False)
        self.assertEqual(self.book.active["turn_state"], "interrupted")
        self.assertEqual([item["status"] for item in self.book.active["actions"]],
                         ["unknown", "cancelled", "completed"])

    def test_corrupt_file_remains_untouched_while_new_work_stays_in_memory(self):
        filename = self.folder / "corrupt.hip"
        path = history_store.sidecar_path(filename)
        path.parent.mkdir()
        path.write_bytes(b"not JSON")
        self.hou.hipFile.filename, self.hou.hipFile.new = str(filename), False
        self.book.open_current(use_memory=False)
        self.assertTrue(self.book.blocked)
        self.book.active["draft"] = "New work still available"
        self.assertFalse(self.book.save())
        self.assertEqual(path.read_bytes(), b"not JSON")
        replacement = conversations.Conversations(self.hou)
        self.assertTrue(replacement.blocked)
        self.assertEqual(replacement.active["draft"], "New work still available")

    def test_external_revision_conflict_preserves_both_file_and_runtime_work(self):
        filename = self.folder / "shot.hip"
        self.hou.hipFile.filename, self.hou.hipFile.new = str(filename), False
        self.book.after_save()
        other, revision = history_store.load(self.book.path)
        other["conversations"][0]["draft"] = "Other process"
        history_store.save(self.book.path, other, revision)
        on_disk = self.book.path.read_bytes()
        self.book.active["draft"] = "My in-memory work"
        self.assertFalse(self.book.save())
        self.assertTrue(self.book.blocked)
        self.assertEqual(self.book.path.read_bytes(), on_disk)
        self.assertEqual(conversations.Conversations(self.hou).active["draft"], "My in-memory work")

    def test_save_as_existing_destination_does_not_overwrite_and_can_fork_elsewhere(self):
        source = self.folder / "source.hip"
        destination = self.folder / "occupied.hip"
        self.listen()
        self.hou.hipFile.save_as(source)
        self.book.active["draft"] = "My complete history"
        other = history_store.fresh_document()
        history_store.new_conversation(other)["draft"] = "Existing destination history"
        occupied = history_store.sidecar_path(destination)
        history_store.save(occupied, other, None)
        original = occupied.read_bytes()
        self.hou.hipFile.save_as(destination)
        self.assertTrue(self.book.blocked)
        self.assertEqual(occupied.read_bytes(), original)
        self.assertEqual(self.book.active["draft"], "My complete history")
        self.assertEqual(history_store.load(history_store.sidecar_path(source))[0]["conversations"][0]["draft"],
                         "My complete history")
        final = self.folder / "safe_new_name.hip"
        self.hou.hipFile.save_as(final)
        self.assertFalse(self.book.blocked)
        self.assertEqual(history_store.load(history_store.sidecar_path(final))[0], self.book.document)
        self.assertEqual(occupied.read_bytes(), original)

    def test_transient_save_failure_keeps_document_and_can_retry(self):
        filename = self.folder / "shot.hip"
        self.hou.hipFile.filename, self.hou.hipFile.new = str(filename), False
        self.book.active["draft"] = "Keep me"
        with patch.object(history_store, "save", side_effect=history_store.StoreError("write_failed")):
            self.assertFalse(self.book.after_save())
        self.assertFalse(self.book.blocked)
        self.assertEqual(self.book.active["draft"], "Keep me")
        self.assertTrue(self.book.save())
        self.assertEqual(self.book.error, "")

    def test_missing_hou_or_invalid_saved_path_does_not_break_panel_startup(self):
        self.assertIsNone(conversations.Conversations(SimpleNamespace()).path)
        self.hou.hipFile.filename, self.hou.hipFile.new = "relative.hip", False
        self.assertIsNone(conversations.Conversations(self.hou).path)

    def test_new_and_select_archive_all_conversations(self):
        old_id = self.book.active["id"]
        self.book.active["draft"] = "Older chat"
        created = self.book.new(server="http://studio", model="local")
        self.assertEqual(self.book.active["id"], created["id"])
        self.assertTrue(self.book.select(old_id))
        self.assertEqual(self.book.active["draft"], "Older chat")
        self.assertFalse(self.book.select("missing"))
        self.assertEqual(len(self.book.document["conversations"]), 2)


class RequestWindowTests(unittest.TestCase):
    def turn(self, number):
        identifier = "call_%d" % number
        return [
            {"role": "user", "content": "Request %d" % number},
            {"role": "assistant", "content": None,
             "reasoning_items": [{"type": "reasoning", "encrypted_content": "exact opaque bytes+/="}],
             "tool_calls": [{"id": identifier, "type": "function", "function": {
                 "name": "inspect", "arguments": '{"path":"/obj/geo1"}'}}]},
            {"role": "tool", "tool_call_id": identifier, "content": '{"found":true}'},
            {"role": "assistant", "content": "Result %d" % number},
        ]

    def test_count_bound_omits_only_complete_turns_and_keeps_original_full_history(self):
        turns = [self.turn(number) for number in range(3)]
        messages = sum(turns, [])
        original = copy.deepcopy(messages)
        selected, omitted = conversations.request_window(messages, max_messages=8)
        self.assertEqual(omitted, 4)
        self.assertEqual(selected, turns[1] + turns[2])
        self.assertEqual(messages, original)
        self.assertEqual(selected[1]["reasoning_items"], original[5]["reasoning_items"])

    def test_byte_bound_keeps_current_turn_whole_even_when_it_exceeds_limit(self):
        first, second = self.turn(1), self.turn(2)
        second[0]["content"] = "large prompt " * 200
        selected, omitted = conversations.request_window(first + second, max_bytes=20)
        self.assertEqual(omitted, 4)
        self.assertEqual(selected, second)
        self.assertEqual(selected[1]["tool_calls"][0]["id"], selected[2]["tool_call_id"])

    def test_empty_or_single_turn_is_never_trimmed_into_orphan_results(self):
        self.assertEqual(conversations.request_window([]), ([], 0))
        turn = self.turn(1)
        self.assertEqual(conversations.request_window(turn, max_messages=1, max_bytes=1), (turn, 0))
        self.assertEqual(conversations.request_window(turn), (turn, 0))


if __name__ == "__main__":
    unittest.main()
