"""Dependency-free visual history, bounded transport and asset integrity tests."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import vision


def png(red=0):
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, red, 0, 0]))) + chunk(b"IEND", b""))


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = vision.AssetStore()
        self.ref = self.store.add(png(), "image/png", 1, 1)
        self.cap = self.store.add(png(40), "image/png", 1, 1, kind="capture", metadata={"reason": "Check spacing", "frame": 7, "fresh_evaluation_requested": False})

    def payload(self):
        return {"messages": [{"role": "system", "content": "System"}, {"role": "user", "content": "Match this"}], "model": "test"}

    def capture_history(self):
        return [{"role": "user", "content": "Match this", "attachments": [self.ref]},
                {"role": "assistant", "content": "Inspecting", "tool_calls": [{"id": "capture_1", "type": "function", "function": {"name": "houlm_capture_viewport", "arguments": '{"reason":"Check spacing"}'}}]},
                {"role": "tool", "tool_call_id": "capture_1", "content": '{"ok":true}', "attachments": [self.cap]}]

    def test_archive_has_only_manifests_and_exact_bytes_survive_save_as(self):
        history = self.capture_history()
        self.assertNotIn("base64", json.dumps(history))
        one = self.store.copy_to(vision.asset_root(self.folder / "scene_v1.hip.json"), vision.collect_manifests(history))
        two = one.copy_to(vision.asset_root(self.folder / "scene_v2.hip.json"), vision.collect_manifests(history))
        self.assertNotEqual(one.root, two.root)
        for manifest in (self.ref, self.cap):
            self.assertEqual(one.read(manifest), two.read(manifest))
            self.assertEqual(self.store.read(manifest), two.read(manifest))
        self.assertEqual(len(list(one.root.iterdir())), 2)

    def test_missing_or_changed_assets_fail_explicitly(self):
        disk = self.store.copy_to(self.folder / "assets", [self.ref])
        path = next(disk.root.iterdir())
        path.write_bytes(b"unexpected")
        with self.assertRaisesRegex(vision.VisionError, "integrity"):
            disk.read(self.ref)
        path.unlink()
        with self.assertRaisesRegex(vision.VisionError, "unavailable"):
            disk.read(self.ref)
        with self.assertRaises(vision.VisionError):
            disk.copy_to(self.folder / "new", [self.ref])
        self.assertEqual(self.store.read(self.ref), png())

    def test_hash_id_cannot_escape_store_or_change_mime_dimensions(self):
        for field, value in (("id", "../../escape"), ("mime_type", "image/svg+xml"), ("width", True), ("width", 0), ("width", 1281), ("byte_count", 524289), ("kind", "script")):
            changed = dict(self.ref, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(vision.VisionError):
                self.store.read(changed)
        with self.assertRaisesRegex(vision.VisionError, "dimensions"):
            self.store.add(png(), "image/png", 2, 1)
        with self.assertRaises(vision.VisionError):
            self.store.add(b"<svg></svg>", "image/png", 1, 1)

    def test_symlink_asset_is_rejected(self):
        disk = self.store.copy_to(self.folder / "assets", [self.ref])
        path = next(disk.root.iterdir())
        path.unlink()
        source = self.folder / "outside.png"
        source.write_bytes(png())
        try:
            path.symlink_to(source)
        except OSError:
            self.skipTest("This Windows session cannot create symlinks")
        with self.assertRaisesRegex(vision.VisionError, "symbolic"):
            disk.read(self.ref)

    def test_capture_follows_complete_tool_pair_and_preserves_inputs(self):
        history = self.capture_history()
        payload = self.payload()
        payload["messages"].extend([{k: v for k, v in item.items() if k != "attachments"} for item in history[1:]])
        before = copy.deepcopy((history, payload))
        wire = vision.enrich_payload(payload, history, self.store)
        self.assertEqual((history, payload), before)
        self.assertEqual(wire["messages"][-2]["role"], "tool")
        self.assertEqual(wire["messages"][-2]["tool_call_id"], "capture_1")
        self.assertEqual(wire["messages"][-1]["role"], "user")
        self.assertIn("capture_1", wire["messages"][-1]["content"][0]["text"])
        self.assertIn("not a new artist request", wire["messages"][-1]["content"][0]["text"])
        self.assertEqual(wire["messages"][1]["content"][0]["text"], "Match this")

    def test_only_latest_current_turn_capture_is_sent(self):
        history = self.capture_history()
        newer = self.store.add(png(77), "image/png", 1, 1, kind="capture")
        history.append({"role": "tool", "tool_call_id": "capture_2", "content": "{}", "attachments": [newer]})
        wire = vision.enrich_payload(self.payload(), history, self.store)
        text = json.dumps(wire)
        self.assertIn(newer["id"], text)
        self.assertNotIn(self.cap["id"], text)
        history.append({"role": "user", "content": "Next turn", "attachments": []})
        wire = vision.enrich_payload(self.payload(), history, self.store)
        self.assertNotIn("image_url", json.dumps(wire))

    def test_permission_exclusion_and_explicit_empty_reference_selection(self):
        wire = vision.enrich_payload(self.payload(), self.capture_history(), self.store, references=[], include_captures=False)
        self.assertNotIn("image_url", json.dumps(wire))

    def test_compacted_text_still_gets_selected_reference_without_raw_archive_replay(self):
        history = [{"role": "user", "content": "Old source", "attachments": [self.ref]},
                   {"role": "assistant", "content": "done"}, {"role": "user", "content": "Modify"}]
        wire = vision.enrich_payload(self.payload(), history, self.store, references=[self.ref])
        self.assertIn("image_url", json.dumps(wire))
        self.assertNotIn("Old source", json.dumps(wire))
        self.assertNotIn("image_url", json.dumps(vision.enrich_payload(self.payload(), history, self.store)))

    def test_count_byte_and_wire_budgets_reject_instead_of_silently_dropping(self):
        with self.assertRaises(vision.VisionError):
            vision.enrich_payload(self.payload(), [], self.store, references=[self.ref] * 4)
        with mock.patch.object(vision, "MAX_REQUEST_IMAGE_BYTES", 1), self.assertRaises(vision.VisionError):
            vision.enrich_payload(self.payload(), [], self.store, references=[self.ref])
        with mock.patch.object(vision, "MAX_WIRE_BYTES", 100), self.assertRaises(vision.VisionError):
            vision.enrich_payload(self.payload(), [], self.store, references=[self.ref])

    def test_malformed_metadata_and_attachment_roles_rejected(self):
        for metadata in ({"huge": "a" * 4096}, {"nan": float("nan")}):
            with self.assertRaises(vision.VisionError):
                vision.validate_manifest(dict(self.ref, metadata=metadata))
        for message in ({"role": "assistant", "attachments": [self.ref]}, {"role": "tool", "attachments": [self.ref]}, {"role": "user", "attachments": [self.cap]}):
            with self.assertRaises(vision.VisionError):
                vision.validate_message(message)

    def test_nondict_messages_and_ancestor_links_rejected(self):
        for message in (None, 7, [], "message"):
            with self.assertRaises(vision.VisionError):
                vision.validate_message(message)
        store = vision.AssetStore(self.folder / ".houlm" / "scene.json.assets")
        ancestor = self.folder / ".houlm"
        with mock.patch.object(Path, "is_symlink", lambda candidate: candidate == ancestor):
            with self.assertRaisesRegex(vision.VisionError, "symbolic"):
                store.read(self.ref)
        with mock.patch.object(Path, "lstat", return_value=mock.Mock(st_reparse_tag=0xA0000003)), mock.patch.object(Path, "is_symlink", return_value=False):
            with self.assertRaisesRegex(vision.VisionError, "junction"):
                store.read(self.ref)

    def test_capture_arguments_cannot_supply_paths_or_force_a_cook(self):
        self.assertEqual(vision.validate_capture_arguments({"reason": "Compare silhouettes"}), {"reason": "Compare silhouettes"})
        for arguments in ({}, {"reason": ""}, {"reason": "x", "path": "C:/private"}, {"reason": "x", "cook": True}, {"reason": "x" * 301}):
            with self.assertRaises(vision.VisionError):
                vision.validate_capture_arguments(arguments)

if __name__ == "__main__":
    unittest.main()
