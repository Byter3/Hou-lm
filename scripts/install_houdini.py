"""Optional persistent package registration. Run inside the target Houdini GUI."""
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]


def install():
    import hou
    if not hou.isUIAvailable():
        raise RuntimeError("Run this from the target Houdini GUI's Python Shell.")
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("This package currently targets Houdini's Python 3.11 build.")
    preference_dir = hou.getenv("HOUDINI_USER_PREF_DIR")
    if not preference_dir:
        raise RuntimeError("Houdini did not provide its user preferences directory.")
    target = Path(preference_dir) / "packages" / "hou-lm.json"
    # Houdini 20.5's bundled packages use hpath as the HOUDINI_PATH shortcut.
    package = {"hpath": (ROOT / "houdini").as_posix()}
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != package:
            raise RuntimeError("A different hou-lm.json already exists at %s; review it before replacing it." % target)
    else:
        with target.open("x", encoding="utf-8") as output:
            json.dump(package, output, indent=2)
            output.write("\n")
    print("Hou-lm package registered at: %s" % target)
    print("Choose Hou-lm Assistant from the pane + menu to open it. This entry will also be available after restarting Houdini.")
    runpy.run_path(str(ROOT / "scripts" / "launch_houdini.py"), run_name="__main__")


if __name__ == "__main__":
    install()
