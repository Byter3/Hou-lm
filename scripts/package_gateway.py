"""Create a small, explicit-file deployment archive without project secrets."""

from pathlib import Path
import tarfile


ROOT = Path(__file__).resolve().parents[1]
NAME = "houlm-gateway-1.103.2-streamfix2"
FILES = (
    "LICENSE",
    ".dockerignore",
    "gateway/Dockerfile",
    "gateway/README.md",
    "gateway/build-image.sh",
    "gateway/bin/litellm",
    "gateway/check_entrypoint.py",
    "gateway/litellm_stream_compat.py",
    "tests/test_litellm_stream_compat.py",
    "tests/test_litellm_stream_probe.py",
    "tests/test_gateway_bootstrap.py",
    "tests/test_gateway_entrypoint.py",
    "scripts/diagnose_litellm_stream.py",
)


def main():
    # Validate all inputs before replacing any previously prepared archive.
    for relative in FILES:
        source = ROOT / relative
        if not source.is_file() or source.is_symlink():
            raise RuntimeError("Missing or unexpected package input: " + relative)
    output = ROOT / "dist" / (NAME + ".tar.gz")
    output.parent.mkdir(exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for relative in FILES:
            source = ROOT / relative
            info = archive.gettarinfo(str(source), arcname=NAME + "/" + relative)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if relative in ("gateway/build-image.sh", "gateway/bin/litellm") else 0o644
            with source.open("rb") as handle:
                archive.addfile(info, handle)
    print(output)
    print("Contains %d allowlisted source/check files; no environment files or credentials." % len(FILES))


if __name__ == "__main__":
    main()
