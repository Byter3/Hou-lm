# Install Hou-lm

Hou-lm is a dockable Houdini assistant connected to your studio's Open WebUI service.

**Requirements:** Windows, Houdini 20.5.445 with Python 3.11/PySide2, an Open WebUI account and user API key, and a model with tool-call support. Image features also need image input support. No `pip install` or additional workstation service is required. Houdini 21, Qt6 and other platforms are not supported by this package.

## Install

1. Download the named plug-in ZIP from [GitHub Releases](https://github.com/Byter3/Hou-lm/releases). Use the plug-in asset, not the automatic **Source code** archives. To build the current source yourself, see [Contributing](https://github.com/Byter3/Hou-lm/blob/main/CONTRIBUTING.md#build-the-houdini-plug-in).
2. Locate Houdini's preferences directory. Run this in **Windows → Python Shell** if unsure:

   ```python
   print(hou.getenv("HOUDINI_USER_PREF_DIR"))
   ```

3. Close Houdini. Extract both `hou-lm.json` and the `hou-lm` folder into the `packages` subfolder, creating it if needed:

   ```text
   houdini20.5/
     packages/
       hou-lm.json
       hou-lm/
         houdini/
         scripts/
         README.md
         LICENSE
         package-manifest.json
   ```

   Do not leave an extra ZIP-name folder around the two items. No path editing or `houdini.env` change is needed.

4. Restart Houdini and choose **+ → New Pane Tab Type → Hou-lm Assistant** where you want the panel.
5. Open **Connections** (the sliders icon), enter your Open WebUI base URL and **Open WebUI user API key**, then connect and choose a model. Use the base URL without `/api` or `/v1`.

Save your Houdini desktop layout to retain the panel arrangement. Login is saved with Windows user protection and reconnects when the panel opens.

## First use

Enable **Scene** and send:

> Use scene tools to list the immediate children of /obj. Report their names and types. Do not modify anything or force a cook.

Expand the tool receipt to see what was read. Leave **Auto-run** off to review proposed changes before applying them.

See [Using the panel](https://github.com/Byter3/Hou-lm/blob/main/docs/houdini-panel-quickstart.md) for controls and history. Source builds with image features include the [visual reference guide](https://github.com/Byter3/Hou-lm/blob/main/docs/visual-reference.md): native viewport capture may evaluate the current frame. The published v0.1.0 ZIP predates those features.

## Upgrading

Close Houdini. Move the previous `hou-lm.json` and `hou-lm` folder outside the package search directories, then extract the new pair. Replace the folder rather than merging files. Restart Houdini.

If you previously registered a source checkout, replace its package JSON and keep only one active Hou-lm registration. Remove duplicate checkout entries from your package configuration or `HOUDINI_PATH`.

Preferences and saved login remain under `%LOCALAPPDATA%/Hou-lm`. Conversations remain in `.houlm` beside each HIP; image assets must stay with their JSON. Save an unsaved HIP before closing Houdini if you want to keep its in-memory conversation.

## Studio distribution

Place the same JSON and client folder together in a directory listed in `HOUDINI_PACKAGE_DIR`, set before Houdini starts. Put the JSON directly in that directory; do not add another `packages` level. Avoid duplicate personal and studio registrations.

Each artist uses their own Open WebUI account and works in their own Houdini session. Credentials and scene histories are not part of the package. Use your existing service or follow the optional [backend setup](https://github.com/Byter3/studio-assistant-backend).

## Uninstall

Close Houdini and remove `packages/hou-lm.json` and `packages/hou-lm`. Leave preferences and project-side `.houlm` folders in place to retain login and conversations.

## Troubleshooting

| Problem | Check |
| --- | --- |
| No Hou-lm pane entry | Confirm the two-item layout and the supported Houdini/Python build, then restart. |
| An older panel appears | Remove duplicate package or checkout registrations. |
| Cannot connect or send | Check the server URL and user API key, then open **Error details**. See [response troubleshooting](https://github.com/Byter3/Hou-lm/blob/main/docs/tool-stream-troubleshooting.md). |
| Cannot find chat history | Save the HIP, then use **Log → Open folder**. |

[MIT license](https://github.com/Byter3/Hou-lm/blob/main/LICENSE). Independent project, not an official SideFX product.
