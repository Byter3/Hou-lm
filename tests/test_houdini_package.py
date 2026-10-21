"""Portable release checks: no Houdini session, credentials or model requests."""

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "package_houdini.py"
spec = importlib.util.spec_from_file_location("houlm_package_builder", BUILDER)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class HoudiniPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="houlm-package-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "source checkout"
        for relative in package.FILES:
            destination = self.source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        self.output = self.directory / "release.zip"

    def build(self):
        return package.build_archive(self.output, source_root=self.source)

    def test_archive_is_complete_and_keeps_runtime_bytes_unchanged(self):
        self.build()
        with zipfile.ZipFile(self.output) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual({PurePosixPath(name).parts[0] for name in names},
                             {"hou-lm.json", "hou-lm"})
            self.assertIsNone(archive.testzip())
            for name in names:
                self.assertFalse(PurePosixPath(name).is_absolute())
                self.assertNotIn("..", PurePosixPath(name).parts)
                self.assertNotIn("\\", name)
            for source in (ROOT / "houdini").rglob("*"):
                if source.suffix not in (".py", ".pypanel"):
                    continue
                relative = source.relative_to(ROOT).as_posix()
                self.assertEqual(archive.read("hou-lm/" + relative), source.read_bytes())
            for helper in ("launch_houdini.py", "reload_houdini.py"):
                self.assertEqual(archive.read("hou-lm/scripts/" + helper),
                                 (ROOT / "scripts" / helper).read_bytes())
            self.assertEqual(archive.read("hou-lm/LICENSE"), (ROOT / "LICENSE").read_bytes())
            self.assertEqual(archive.read("hou-lm/README.md"),
                             (ROOT / "docs/package-install.md").read_bytes())

    def test_manifest_checksums_cover_every_shipped_file(self):
        self.build()
        with zipfile.ZipFile(self.output) as archive:
            manifest_name = "hou-lm/package-manifest.json"
            manifest = json.loads(archive.read(manifest_name))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["version"], "0.1.0")
            self.assertEqual(manifest["target"],
                             {"houdini": "20.5", "python": "3.11", "qt": "PySide2"})
            self.assertEqual(set(manifest["files"]), set(archive.namelist()) - {manifest_name})
            for name, digest in manifest["files"].items():
                self.assertEqual(digest, hashlib.sha256(archive.read(name)).hexdigest())
            self.assertNotIn(str(self.source), archive.read(manifest_name).decode("utf-8"))

    def test_relocated_package_resolves_and_imports_without_the_checkout(self):
        self.build()
        # Spaces and non-ASCII names are common in artist and studio paths.
        packages = self.directory / "relocated M\u0171hely" / "packages"
        with zipfile.ZipFile(self.output) as archive:
            archive.extractall(packages)
        definition = json.loads((packages / "hou-lm.json").read_text(encoding="utf-8"))
        self.assertTrue(definition["load_package_once"])
        self.assertIn("houdini_python == 'python3.11'", definition["enable"])
        variables = {"HOUDINI_PACKAGE_PATH": packages.as_posix()}
        for item in definition.get("env", []):
            for name, value in item.items():
                for variable, replacement in variables.items():
                    value = value.replace("$" + variable, replacement)
                variables[name] = value
        resolved = definition["hpath"]
        for variable, replacement in variables.items():
            resolved = resolved.replace("$" + variable, replacement)
        houdini_path = Path(resolved)
        self.assertEqual(houdini_path, packages / "hou-lm" / "houdini")
        self.assertTrue((houdini_path / "python_panels/hou_lm.pypanel").is_file())
        modules = houdini_path / "python3.11libs"
        code = (
            "import json, pathlib, sys; sys.path.insert(0, sys.argv[1]); "
            "import houlm, houlm.core; "
            "print(json.dumps([str(pathlib.Path(houlm.__file__).resolve()), "
            "str(pathlib.Path(houlm.core.__file__).resolve())]))"
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(modules)],
                                cwd=self.directory, capture_output=True, text=True,
                                timeout=20, check=True)
        imported = [Path(name) for name in json.loads(result.stdout)]
        self.assertEqual(imported, [(modules / "houlm/__init__.py").resolve(),
                                    (modules / "houlm/core.py").resolve()])

    def test_panel_retains_named_native_entry_and_all_local_imports_exist(self):
        self.build()
        with zipfile.ZipFile(self.output) as archive:
            panel = ET.fromstring(archive.read("hou-lm/houdini/python_panels/hou_lm.pypanel"))
            interface = panel.find("interface")
            self.assertEqual(interface.attrib["name"], "hou_lm")
            self.assertEqual(interface.attrib["label"], "Hou-lm Assistant")
            self.assertIsNotNone(interface.find("includeInPaneTabMenu"))
            script = interface.find("script").text
            self.assertIn("panel_host.PanelHost()", script)
            self.assertNotIn("createFloatingPaneTab", script)
            sources = {PurePosixPath(name).stem: archive.read(name)
                       for name in archive.namelist()
                       if name.startswith("hou-lm/houdini/python3.11libs/houlm/")
                       and name.endswith(".py")}
            trees = [ast.parse(source) for source in sources.values()]
            trees.append(ast.parse(script))
            for tree in trees:
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        if node.level == 1 and node.module:
                            self.assertIn(node.module.split(".")[0], sources)
                        elif (node.level == 1 and node.module is None) or node.module == "houlm":
                            for alias in node.names:
                                self.assertIn(alias.name, sources)
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith("houlm."):
                                self.assertIn(alias.name.split(".")[1], sources)

    def test_private_files_and_unlisted_python_do_not_enter_release(self):
        marker = b"synthetic-private-data-that-must-never-be-published"
        decoys = (".env", "secrets/auth.json", ".houlm/scene.hip.json", "scene.hip",
                  "backups/old.py", "gateway/private-config.yaml", "tests/private.py",
                  "scripts/diagnose_private.py", "houdini/python3.11libs/houlm/private.py",
                  "houdini/python3.11libs/houlm/__pycache__/panel.cpython-311.pyc")
        for relative in decoys:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(marker)
        self.build()
        with zipfile.ZipFile(self.output) as archive:
            for name in archive.namelist():
                self.assertNotIn(marker, archive.read(name))
            self.assertEqual(set(archive.namelist()),
                             set(package.FILES.values()) | {"hou-lm/package-manifest.json"})

    def test_missing_input_does_not_create_release(self):
        (self.source / "houdini/python3.11libs/houlm/core.py").unlink()
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_invalid_package_or_panel_definition_does_not_create_release(self):
        for relative in ("packaging/houdini/hou-lm.json", "houdini/python_panels/hou_lm.pypanel"):
            with self.subTest(relative=relative):
                source = self.source / relative
                original = source.read_bytes()
                source.write_text("not a valid definition", encoding="utf-8")
                try:
                    with self.assertRaises(ValueError):
                        self.build()
                    self.assertFalse(self.output.exists())
                finally:
                    source.write_bytes(original)

    def test_version_is_parsed_without_executing_application_code(self):
        (self.source / "houdini/python3.11libs/houlm/__init__.py").write_text(
            '__version__ = "0.1.0"\nraise RuntimeError("application code was executed")\n',
            encoding="utf-8")
        self.build()
        self.assertTrue(self.output.is_file())

    def test_nonliteral_version_is_rejected_without_running_it(self):
        marker = self.directory / "executed.txt"
        (self.source / "houdini/python3.11libs/houlm/__init__.py").write_text(
            '__version__ = __import__("pathlib").Path(%r).write_text("executed")\n' % str(marker),
            encoding="utf-8")
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(marker.exists())
        self.assertFalse(self.output.exists())

    def test_existing_release_is_never_overwritten(self):
        original = b"previous release retained"
        self.output.write_bytes(original)
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(self.output.read_bytes(), original)

    def test_source_paths_cannot_escape_the_checkout(self):
        outside = self.directory / "outside.py"
        outside.write_text("synthetic private source", encoding="utf-8")
        with mock.patch.dict(package.FILES, {"../outside.py": "hou-lm/outside.py"}):
            with self.assertRaises(ValueError):
                self.build()
        self.assertFalse(self.output.exists())

    def test_symlinked_source_is_rejected(self):
        source = self.source / "LICENSE"
        source.unlink()
        try:
            source.symlink_to(ROOT / "LICENSE")
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is unavailable for this process")
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_identical_inputs_produce_identical_zips_without_source_timestamps(self):
        self.build()
        second = self.directory / "second.zip"
        for relative in package.FILES:
            os.utime(self.source / relative, (1600000000, 1600000000))
        package.build_archive(second, source_root=self.source)
        self.assertEqual(self.output.read_bytes(), second.read_bytes())

    def test_cli_builds_an_installable_archive_with_plain_python(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(BUILDER),
                                 "--output", str(self.output)],
                                cwd=self.directory, capture_output=True, text=True,
                                timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Copy hou-lm.json", result.stdout)
        with zipfile.ZipFile(self.output) as archive:
            self.assertIn("hou-lm.json", archive.namelist())
            self.assertIn("hou-lm/houdini/python_panels/hou_lm.pypanel", archive.namelist())


if __name__ == "__main__":
    unittest.main()
