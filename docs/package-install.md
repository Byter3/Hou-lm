# Hou-lm Houdini plug-in

Hou-lm is a dockable assistant for studio Houdini workflows. It connects to your existing Open WebUI service and runs scene tools in the artist's Houdini session.

The supported interactive target is **Windows, Houdini 20.5.445, Python 3.11 and bundled PySide2**. The package enables itself for Houdini 20.5 / Python 3.11. Houdini 21, Qt6 builds and other platforms are not validated by this release. No `pip install` or additional workstation service is required.

## Install

Download **[Hou-lm-0.1.0-houdini20.5.zip](https://github.com/Byter3/Hou-lm/releases/download/v0.1.0/Hou-lm-0.1.0-houdini20.5.zip)** from the [GitHub release](https://github.com/Byter3/Hou-lm/releases/tag/v0.1.0). Choose that named asset, not GitHub's automatic **Source code** archives. Artists do not need to clone the repository or build the plug-in.

1. Locate your Houdini preferences directory. If unsure, run this in **Windows → Python Shell** before closing Houdini:

   ```python
   print(hou.getenv("HOUDINI_USER_PREF_DIR"))
   ```

   On Windows this is usually `Documents/houdini20.5`; use the value Houdini reports, especially with redirected Documents folders.

2. Close Houdini.
3. Extract **both** items from `Hou-lm-0.1.0-houdini20.5.zip` into the `packages` subfolder of that directory. Create `packages` if needed. The final layout must be:

   ```text
   houdini20.5/
     packages/
       hou-lm.json
       hou-lm/
         houdini/
           python3.11libs/houlm/
           python_panels/hou_lm.pypanel
         scripts/
           launch_houdini.py
           reload_houdini.py
         LICENSE
         README.md
         package-manifest.json
   ```

   Keep `hou-lm.json` directly in `packages`, with the `hou-lm` folder beside it. Do not leave an extra ZIP-name folder around these two items. Paths in the package resolve relative to its own location, so no JSON path editing or `houdini.env` changes are needed.

4. Restart Houdini. In the pane where you want the assistant, choose **+ → New Pane Tab Type → Hou-lm Assistant**. Choose that named entry, rather than the generic **Misc → Python Panel**. No launch command is needed.
5. Open **Connections** using the sliders icon. Enter your Open WebUI base URL and **Open WebUI user API key**, then connect and select a model. Use the same service and account as your existing setup.

Use Houdini's pane controls to dock, split or move the assistant. Save your desktop layout if you want that arrangement restored.

### Replacing a source-checkout installation

If you previously ran `install_houdini.py`, `packages/hou-lm.json` points to your checkout. With Houdini closed, keep a backup of that registration **outside all package search folders**, then replace it with the supplied `hou-lm.json` and copy the supplied `hou-lm` folder beside it. Keep only one active Hou-lm registration; do not also load a checkout through another package or `HOUDINI_PATH` entry.

The development checkout can remain on disk. Its launcher is not needed for the installed plug-in.

## First test

With **Scene** enabled, send:

```text
Use scene tools to list the immediate children of /obj. Report their names and types. Do not modify anything or force a cook.
```

Expect a scene-query receipt and an answer matching its result. Then close and reopen the assistant through the named pane menu entry. Your saved login should return. For a saved HIP, its saved conversation should return without replaying commands.

Full controls, editing tests and history behavior are documented in the [panel quickstart](https://github.com/Byter3/Hou-lm/blob/main/docs/houdini-panel-quickstart.md).

## Upgrading

Close Houdini before replacing files. Back up the old `hou-lm.json` and `hou-lm` folder outside the package search folders, then replace them with the new archive's pair. Use a fresh `hou-lm` folder rather than merging releases, so removed modules cannot remain behind. Restart Houdini afterward.

Preferences and saved login are stored separately under `%LOCALAPPDATA%/Hou-lm` on Windows. Scene histories live in `.houlm` beside their HIP files. Replacing the plug-in folder does not replace those files. Save the HIP and conversation before closing; an unsaved HIP's in-memory history does not survive quitting Houdini.

The included reload helper is for development of compatible child-panel changes. It preserves the stable native host; updates to the host or panel definition require a restart. For installed packages, the helper can be run inside Houdini with:

```python
import hou, runpy; _ = runpy.run_path(hou.expandString("$HOULM_ROOT/scripts/reload_houdini.py"), run_name="__main__")
```

## Uninstall

Close Houdini and remove the installed `packages/hou-lm.json` and `packages/hou-lm` folder. Restart Houdini. Leave `%LOCALAPPDATA%/Hou-lm` and HIP-adjacent `.houlm` folders in place if you want to retain login, preferences and conversations for later use.

## Studio distribution

A studio can place the same `hou-lm.json` and `hou-lm` pair in a centrally managed directory listed in `HOUDINI_PACKAGE_DIR`, which must be set before Houdini starts. Put the JSON directly in that directory, with its sibling folder; no additional `packages` level is needed. The package loads once by filename. Avoid simultaneous personal and studio registrations so each artist loads the intended version.

Each artist uses their own Open WebUI account and acts on their own open Houdini scene. The plug-in archive contains the client, its launch/reload helpers, installation instructions, license and file checksums. It contains no server stack, gateway image, environment file, API key, private scene history, HIP or diagnostic log. Continue using your existing central model service; installing the client does not deploy or change it.

## Building from source

From the repository root, run in a terminal:

```sh
python scripts/package_houdini.py
```

The archive is written to `dist/Hou-lm-0.1.0-houdini20.5.zip`. Use `--output /path/to/package.zip` to choose another output file. Building requires Python 3.11 and does not import Houdini or contact a model service. Generated archives stay outside source control.

For development directly from a checkout, the [quickstart](https://github.com/Byter3/Hou-lm/blob/main/docs/houdini-panel-quickstart.md#development-launch-a-source-checkout) retains the one-line session launcher and optional checkout registration workflow.

## Troubleshooting

- **No Hou-lm pane entry:** check the two-item layout, restart Houdini and confirm a Houdini 20.5 Python 3.11 / PySide2 build. The package intentionally remains disabled on other Houdini/Python versions.
- **An older panel appears:** check for another `hou-lm.json`, checkout launcher or `HOUDINI_PATH` entry, then restart with one registration.
- **Connection or model request fails:** use **Error details** and the [response troubleshooting guide](https://github.com/Byter3/Hou-lm/blob/main/docs/tool-stream-troubleshooting.md). A package install does not configure model access on the server.
- **History location is unclear:** click **Log → Open folder**. History follows the saved HIP's location, not the plug-in directory.

Hou-lm is licensed under [MIT](https://github.com/Byter3/Hou-lm/blob/main/LICENSE). Houdini and server/provider dependencies have their own licenses. Hou-lm is an independent project, not an official SideFX product.
