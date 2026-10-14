"""Exercise the deployed executable and real worker process boundaries offline.

Tiny package fixtures stand in for LiteLLM so these bootstrap tests need no
server dependencies, listening socket, model, database, or credentials. Adapter
behavior against the installed LiteLLM package has its own integration suite.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "gateway" / "bin" / "litellm"

FAKE_COMPAT = '''
import multiprocessing
import os

installed = 0

def install():
    global installed
    if os.environ.get("HOULM_TEST_FAIL") == "parent":
        raise RuntimeError("PRIVATE_BOOTSTRAP_EXCEPTION_SENTINEL")
    if (os.environ.get("HOULM_TEST_FAIL") == "child"
            and multiprocessing.current_process().name != "MainProcess"):
        raise RuntimeError("PRIVATE_BOOTSTRAP_EXCEPTION_SENTINEL")
    installed += 1
    return "test-version"
'''

FAKE_CLI = '''
import json
import multiprocessing
import os
from pathlib import Path
import sys

import litellm_stream_compat as compat

assert compat.installed == 1, "CLI was imported before exactly one installation"

def worker(path):
    # Like uvicorn's child startup, import the ASGI app in the child process.
    from litellm.proxy import proxy_server
    Path(path).write_text(json.dumps({
        "installed": compat.installed,
        "asgi_ready": proxy_server.ready,
        "pid": os.getpid(),
    }), encoding="utf-8")

def run_server():
    out = Path(os.environ["HOULM_TEST_OUTPUT"])
    out.mkdir(exist_ok=True)
    Path(out / "cli.json").write_text(json.dumps({
        "argv": sys.argv[1:], "installed": compat.installed,
        "environment": os.environ.get("HOULM_TEST_PRESERVED"),
        "pid": os.getpid(),
    }), encoding="utf-8")
    method = os.environ.get("HOULM_TEST_METHOD", "spawn")
    if method == "none":
        from litellm.proxy import proxy_server
        assert proxy_server.ready
        return
    ctx = multiprocessing.get_context(method)
    processes = [ctx.Process(target=worker, args=(str(out / ("worker%d.json" % i)),))
                 for i in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
            raise SystemExit(81)
        if process.exitcode != 0:
            raise SystemExit(80)
'''


class GatewayBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="houlm-bootstrap-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gateway = self.root / "gateway"
        (self.gateway / "bin").mkdir(parents=True)
        self.wrapper = self.gateway / "bin" / "litellm"
        shutil.copyfile(WRAPPER, self.wrapper)
        self.wrapper.chmod(0o755)
        (self.gateway / "litellm_stream_compat.py").write_text(
            textwrap.dedent(FAKE_COMPAT), encoding="utf-8")
        package = self.gateway / "litellm"
        (package / "proxy").mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "proxy" / "__init__.py").write_text("", encoding="utf-8")
        (package / "proxy" / "proxy_cli.py").write_text(
            textwrap.dedent(FAKE_CLI), encoding="utf-8")
        (package / "proxy" / "proxy_server.py").write_text(
            "import litellm_stream_compat as compat\n"
            "assert compat.installed == 1, 'ASGI imported without installation'\n"
            "ready = True\n", encoding="utf-8")
        self.output = self.root / "result"

    def run_wrapper(self, *args, method="spawn", failure="", direct=False):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.update({"HOULM_TEST_OUTPUT": str(self.output),
                    "HOULM_TEST_METHOD": method,
                    "HOULM_TEST_FAIL": failure,
                    "HOULM_TEST_PRESERVED": "existing-setting"})
        command = ([str(self.wrapper)] if direct else [sys.executable, str(self.wrapper)])
        return subprocess.run([*command, *args],
                              cwd=self.root, env=env, capture_output=True,
                              text=True, timeout=45)

    def assert_workers_ready(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parent = json.loads((self.output / "cli.json").read_text(encoding="utf-8"))
        workers = [json.loads((self.output / ("worker%d.json" % i)).read_text(encoding="utf-8"))
                   for i in range(2)]
        self.assertEqual(parent["installed"], 1)
        self.assertEqual(len({parent["pid"], *(worker["pid"] for worker in workers)}), 3)
        self.assertTrue(all(worker["asgi_ready"] and worker["installed"] == 1
                            for worker in workers))
        return parent

    def test_extensionless_wrapper_installs_in_each_spawn_worker_and_preserves_cli(self):
        args = ["--port", "4000", "--num_workers", "2", "--config", "/safe/config file.yaml"]
        result = self.run_wrapper(*args)
        parent = self.assert_workers_ready(result)
        self.assertEqual(parent["argv"], args)
        self.assertEqual(parent["environment"], "existing-setting")
        self.assertEqual(result.stdout.count("compatibility active"), 3)

    def test_single_process_installs_before_asgi_import(self):
        result = self.run_wrapper("--port", "4000", method="none")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("compatibility active"), 1)

    @unittest.skipUnless(hasattr(os, "fork"), "fork unavailable on this platform")
    def test_fork_workers_inherit_patch(self):
        self.assert_workers_ready(self.run_wrapper(method="fork"))

    def test_install_failure_stops_before_cli_without_leaking_exception(self):
        result = self.run_wrapper(failure="parent")
        self.assertEqual(result.returncode, 78)
        self.assertFalse(self.output.exists())
        self.assertIn("startup failed (RuntimeError)", result.stderr)
        self.assertNotIn("PRIVATE_BOOTSTRAP_EXCEPTION_SENTINEL", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_spawn_worker_failure_prevents_asgi_start_and_redacts_exception(self):
        result = self.run_wrapper(failure="child")
        self.assertEqual(result.returncode, 80, result.stdout + result.stderr)
        self.assertFalse(list(self.output.glob("worker*.json")))
        self.assertIn("startup failed (RuntimeError)", result.stderr)
        self.assertNotIn("PRIVATE_BOOTSTRAP_EXCEPTION_SENTINEL", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_unverified_granian_launcher_is_rejected_before_start(self):
        result = self.run_wrapper("--run_granian")
        self.assertEqual(result.returncode, 78)
        self.assertFalse(self.output.exists())
        self.assertIn("Granian workers have not been verified", result.stderr)

    def test_linux_shebang_uses_python_from_inherited_path(self):
        self.assertEqual(WRAPPER.read_bytes().splitlines()[0], b"#!/usr/bin/env python")

    @unittest.skipIf(os.name == "nt", "Linux executable shebang unavailable on Windows")
    def test_direct_shebang_launch_reinstalls_in_spawn_workers(self):
        self.assert_workers_ready(self.run_wrapper("--port", "4000", direct=True))


if __name__ == "__main__":
    unittest.main()
