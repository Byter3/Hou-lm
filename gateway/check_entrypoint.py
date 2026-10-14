"""Offline image-build smoke check for the actual inherited LiteLLM entrypoint.

Run this in a network-disabled Docker build step, without runtime credentials or
data-volume mounts. It executes --version, which returns before database setup.
No shell-source parsing or assumptions about a floating image tag are required.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


class EntrypointCheckError(RuntimeError):
    """A fixed diagnostic code, never captured process or environment text."""


def check(entrypoint, wrapper, expected_version):
    resolved = shutil.which("litellm")
    if resolved is None or Path(resolved).resolve() != Path(wrapper).resolve():
        raise EntrypointCheckError("wrapper_not_first_on_path")

    env = os.environ.copy()
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    try:
        result = subprocess.run([*entrypoint, "--version"], env=env,
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=45)
    except (OSError, subprocess.SubprocessError):
        raise EntrypointCheckError("entrypoint_version_command_failed") from None
    if result.returncode != 0:
        raise EntrypointCheckError("entrypoint_version_command_failed")

    active = "Hou-lm ChatGPT stream compatibility active (LiteLLM %s)." % expected_version
    version = "LiteLLM: Current Version = %s" % expected_version
    lines = {line.strip() for line in result.stdout.splitlines()}
    if active not in lines:
        raise EntrypointCheckError("entrypoint_bypassed_wrapper")
    if "Hou-lm stream repair revision: streamfix2 (tool arguments and answer text)." not in lines:
        raise EntrypointCheckError("unexpected_stream_repair_revision")
    if version not in lines:
        raise EntrypointCheckError("unexpected_cli_version_output")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entrypoint", default="/app/docker/prod_entrypoint.sh")
    parser.add_argument("--wrapper", default="/opt/houlm/gateway/bin/litellm")
    parser.add_argument("--expected-version", default="1.103.2")
    args = parser.parse_args()
    try:
        check([args.entrypoint], args.wrapper, args.expected_version)
    except EntrypointCheckError as error:
        print("Hou-lm image startup check failed: %s." % error, file=sys.stderr)
        return 1
    print("Hou-lm inherited entrypoint and LiteLLM version smoke check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
