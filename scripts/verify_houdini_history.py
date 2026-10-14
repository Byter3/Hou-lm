"""Opt-in HIP history regression in a fresh hython process and temporary folder.

Never run inside an artist's Houdini UI. This creates only empty synthetic HIPs,
checks native save/load callbacks, and removes its temporary directory afterward.
"""
from pathlib import Path
import sys
import tempfile


def main():
    import hou
    if hou.isUIAvailable() or hou.node("/obj").children() or hou.node("/stage").children():
        raise RuntimeError("Use a fresh empty hython, never the artist's open scene.")
    hou.setUpdateMode(hou.updateMode.Manual)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
    from houlm import conversations, history_store

    with tempfile.TemporaryDirectory(prefix="houlm-history-test-") as directory:
        root = Path(directory)
        first, second = root / "untitled.hip", root / "version_002.hip"
        book = conversations.Conversations(hou)
        book.listen(book.save, lambda: None, book.save, lambda: None)
        book.active["draft"] = "Synthetic conversation before first save"
        hou.hipFile.save(str(first), save_to_recent_files=False)
        assert book.path == history_store.sidecar_path(first), book.error
        assert book.saved, book.error
        assert history_store.load(book.path)[0] == book.document
        print("PASS native first Save writes JSON; saved untitled isNewFile=%s" % hou.hipFile.isNewFile(), flush=True)

        initial = book.path.read_bytes()
        hou.hipFile.save(str(second), save_to_recent_files=False)
        assert book.path == history_store.sidecar_path(second), book.error
        assert history_store.sidecar_path(first).read_bytes() == initial
        book.active["draft"] = "Synthetic second version"
        assert book.save(), book.error
        assert history_store.sidecar_path(first).read_bytes() == initial
        print("PASS native Save As copies complete history and preserves the earlier file", flush=True)

        hou.hipFile.load(str(first), suppress_save_prompt=True)
        assert book.active["draft"] == "Synthetic conversation before first save"
        assert book.path == history_store.sidecar_path(first)
        print("PASS native Load restores that HIP's conversation", flush=True)

        hou.hipFile.clear(suppress_save_prompt=True)
        assert book.path is None
        assert book.active["draft"] == ""
        book.close()
        print("PASS native Clear starts a separate unsaved conversation", flush=True)
    print("PASS no artist scene opened or modified", flush=True)


if __name__ == "__main__":
    main()
