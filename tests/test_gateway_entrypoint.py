"""Routing checks for image startup, without real gateways or credentials."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("houlm_entrypoint_check", ROOT / "gateway" / "check_entrypoint.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
VERSION = "1.103.2"
ACTIVE = ("Hou-lm ChatGPT stream compatibility active (LiteLLM 1.103.2).\n"
          "Hou-lm stream repair revision: streamfix2 (tool arguments and answer text).")
CLI = "LiteLLM: Current Version = 1.103.2"


class GatewayEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.wrapper = ROOT / "gateway" / "bin" / "litellm"
        self.entrypoint = ["/app/docker/prod_entrypoint.sh"]

    def run_guard(self, stdout, returncode=0):
        with mock.patch.object(guard.shutil, "which", return_value=str(self.wrapper)), \
             mock.patch.object(guard.subprocess, "run", return_value=SimpleNamespace(
                 stdout=stdout, stderr="PRIVATE_UPSTREAM_TEXT", returncode=returncode)) as run:
            guard.check(self.entrypoint, self.wrapper, VERSION)
            return run

    def test_invokes_inherited_entrypoint_version_without_shell_or_stdin(self):
        run = self.run_guard(ACTIVE + "\n\n" + CLI + "\n")
        args, kwargs = run.call_args
        self.assertEqual(args[0], [*self.entrypoint, "--version"])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(kwargs["capture_output"])
        self.assertLessEqual(kwargs["timeout"], 60)
        self.assertEqual(kwargs["env"]["LITELLM_LOCAL_MODEL_COST_MAP"], "True")
        self.assertNotIn("shell", kwargs)

    def test_different_or_missing_path_resolution_never_starts_entrypoint(self):
        for value in (None, str(ROOT / "original-litellm")):
            with self.subTest(value=value), \
                 mock.patch.object(guard.shutil, "which", return_value=value), \
                 mock.patch.object(guard.subprocess, "run") as run:
                with self.assertRaisesRegex(guard.EntrypointCheckError, "wrapper_not_first_on_path"):
                    guard.check(self.entrypoint, self.wrapper, VERSION)
                run.assert_not_called()

    def test_original_cli_bypassing_wrapper_is_rejected(self):
        with self.assertRaisesRegex(guard.EntrypointCheckError, "entrypoint_bypassed_wrapper"):
            self.run_guard(CLI + "\n")

    def test_previous_repair_revision_is_rejected(self):
        with self.assertRaisesRegex(guard.EntrypointCheckError, "unexpected_stream_repair_revision"):
            self.run_guard(ACTIVE.splitlines()[0] + "\n" + CLI + "\n")

    def test_failed_smoke_does_not_reveal_process_output(self):
        with self.assertRaises(guard.EntrypointCheckError) as caught:
            self.run_guard("PRIVATE_UPSTREAM_TEXT", returncode=1)
        self.assertEqual(str(caught.exception), "entrypoint_version_command_failed")

    def test_wrong_cli_version_is_rejected(self):
        with self.assertRaisesRegex(guard.EntrypointCheckError, "unexpected_cli_version_output"):
            self.run_guard(ACTIVE + "\nLiteLLM: Current Version = wrong\n")

    def test_timeout_or_spawn_failure_omits_exception_details(self):
        for error in (OSError("PRIVATE_EXCEPTION_TEXT"),
                      subprocess.TimeoutExpired("PRIVATE_EXCEPTION_TEXT", 45)):
            with self.subTest(error=type(error).__name__), \
                 mock.patch.object(guard.shutil, "which", return_value=str(self.wrapper)), \
                 mock.patch.object(guard.subprocess, "run", side_effect=error):
                with self.assertRaises(guard.EntrypointCheckError) as caught:
                    guard.check(self.entrypoint, self.wrapper, VERSION)
                self.assertEqual(str(caught.exception), "entrypoint_version_command_failed")

    @unittest.skipIf(os.name == "nt", "POSIX entrypoint fixture runs in the image build")
    def test_actual_shell_entrypoint_accepts_path_wrapper_but_rejects_absolute_bypass(self):
        with tempfile.TemporaryDirectory(prefix="houlm-entrypoint-") as directory:
            root = Path(directory)
            wrapper = root / "litellm"
            original = root / "original-litellm"
            entrypoint = root / "entrypoint.sh"
            wrapper.write_text("#!/bin/sh\nprintf '%s\\n' '" + ACTIVE + "' '" + CLI + "'\n", encoding="utf-8")
            original.write_text("#!/bin/sh\nprintf '%s\\n' '" + CLI + "'\n", encoding="utf-8")
            for path in (wrapper, original):
                path.chmod(0o755)
            entrypoint.write_text('#!/bin/sh\nexec litellm "$@"\n', encoding="utf-8")
            entrypoint.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": str(root) + os.pathsep + os.environ.get("PATH", "")}):
                guard.check([str(entrypoint)], wrapper, VERSION)
                entrypoint.write_text('#!/bin/sh\nexec "' + str(original) + '" "$@"\n', encoding="utf-8")
                with self.assertRaisesRegex(guard.EntrypointCheckError, "entrypoint_bypassed_wrapper"):
                    guard.check([str(entrypoint)], wrapper, VERSION)


if __name__ == "__main__":
    unittest.main()
