"""Build the relocatable Houdini plug-in ZIP from explicitly allowed files.

No Houdini import, installation, model request or server configuration is needed.
The ZIP's two root entries can be copied directly into a packages directory.
"""

import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]

# Review additions explicitly: never recursively include a developer's checkout.
RUNTIME_MODULES = (
    "__init__", "activity", "change_review", "chat_format", "context_index",
    "conversations", "core", "credentials", "discovery", "edits", "history_store",
    "homhelp", "inspection", "node_paths", "node_ui", "panel", "panel_host",
    "python_actions", "request_context", "scene", "stream_diagnostics", "toolchat",
    "transcript", "vision", "vision_ui",
)
FILES = {
    "packaging/houdini/hou-lm.json": "hou-lm.json",
    "docs/package-install.md": "hou-lm/README.md",
    "docs/visual-reference.md": "hou-lm/visual-reference.md",
    "LICENSE": "hou-lm/LICENSE",
    "scripts/launch_houdini.py": "hou-lm/scripts/launch_houdini.py",
    "scripts/reload_houdini.py": "hou-lm/scripts/reload_houdini.py",
    "houdini/python_panels/hou_lm.pypanel": "hou-lm/houdini/python_panels/hou_lm.pypanel",
    **{
        "houdini/python3.11libs/houlm/%s.py" % name:
        "hou-lm/houdini/python3.11libs/houlm/%s.py" % name
        for name in RUNTIME_MODULES
    },
}
MANIFEST_PATH = "hou-lm/package-manifest.json"


def _read_source(root: Path, relative: str) -> bytes:
    source = root / relative
    if (not source.is_file() or not source.resolve().is_relative_to(root)
            or any(part.is_symlink() for part in (source, *source.parents)
                   if part != root and part.is_relative_to(root))):
        raise ValueError("Missing or unexpected package input: " + relative)
    return source.read_bytes()


def _version(source: bytes) -> str:
    # Do not import the application while packaging it.
    module = ast.parse(source.decode("utf-8-sig"))
    for statement in module.body:
        if (isinstance(statement, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__version__"
                        for target in statement.targets)):
            value = ast.literal_eval(statement.value)
            if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", value):
                return value
    raise ValueError("A valid houlm.__version__ is required to name the package.")


def build_archive(output: Path | None = None, *, source_root: Path = ROOT) -> Path:
    root = Path(source_root).resolve()
    payload = {destination: _read_source(root, source)
               for source, destination in FILES.items()}
    package = json.loads(payload["hou-lm.json"])
    if not isinstance(package, dict) or not package.get("hpath"):
        raise ValueError("The Houdini package descriptor requires an hpath.")
    try:
        ET.fromstring(payload["hou-lm/houdini/python_panels/hou_lm.pypanel"])
    except ET.ParseError as error:
        raise ValueError("Invalid Python Panel definition.") from error
    version = _version(payload["hou-lm/houdini/python3.11libs/houlm/__init__.py"])
    manifest = {
        "schema_version": 1,
        "version": version,
        "target": {"houdini": "20.5", "python": "3.11", "qt": "PySide2"},
        "files": {name: hashlib.sha256(data).hexdigest()
                  for name, data in sorted(payload.items())},
    }
    payload[MANIFEST_PATH] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

    # Build completely before creating the output. Fixed metadata avoids leaking
    # local usernames/timestamps and makes identical inputs reproducible.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 15, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)

    target = (Path(output) if output is not None
              else root / "dist" / ("Hou-lm-%s-houdini20.5.zip" % version))
    target.parent.mkdir(parents=True, exist_ok=True)
    # An existing release should never be silently replaced with another build.
    with target.open("xb") as handle:
        handle.write(buffer.getvalue())
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New ZIP filename (existing files are not overwritten).")
    args = parser.parse_args(argv)
    try:
        target = build_archive(args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, "Package not built: %s\n" % error)
    print(target)
    print("Copy hou-lm.json and the hou-lm folder from this ZIP into Houdini's packages folder.")
    print("Contains %d allowlisted files plus checksums; no credentials, scene histories or server configuration."
          % len(FILES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
