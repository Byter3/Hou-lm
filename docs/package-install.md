# Install Hou-lm

Houdini client for the [studio assistant backend](https://github.com/Byter3/studio-assistant-backend). **Windows · Houdini 20.5.445 · Python 3.11 / PySide2.** No extra Python dependencies.

## Install

1. Download the [plug-in ZIP](https://github.com/Byter3/Hou-lm/releases/download/v0.1.0/Hou-lm-0.1.0-houdini20.5.zip), or [build current source](https://github.com/Byter3/Hou-lm/blob/main/CONTRIBUTING.md#build-the-houdini-plug-in). Published v0.1.0 predates image/capture features.
2. With Houdini closed, extract into `$HOUDINI_USER_PREF_DIR/packages`:

   ```text
   packages/
     hou-lm.json
     hou-lm/
   ```

3. Restart; open **+ → New Pane Tab Type → Hou-lm Assistant**.
4. In **Connections**, enter the Open WebUI base URL and user API key. Choose a tool-capable model; images require vision support.

Keep one active package registration. The supplied paths are relative; no `houdini.env` edit is needed.

## Upgrading

Close Houdini and replace both package items completely. Move old copies outside package search paths, including any previous checkout registration. Preferences and project histories are separate and retained.

## Studio distribution

Place the JSON/folder pair directly in a directory on `HOUDINI_PACKAGE_DIR`. Each artist uses their own Open WebUI account. [Backend configuration](https://github.com/Byter3/studio-assistant-backend/blob/main/docs/server-setup.md).

## Uninstall

Remove the pair with Houdini closed. Settings remain under `%LOCALAPPDATA%/Hou-lm`; conversations and images remain in HIP-adjacent `.houlm` folders.

## Check

> List the immediate children of /obj using scene tools. Do not modify anything or force a cook.

Missing panel: check the package layout, duplicate registrations and supported build. Connection errors: check **Error details**. History location: **Log → Open folder**.

[Controls](https://github.com/Byter3/Hou-lm/blob/main/docs/houdini-panel-quickstart.md) · [Visual workflow](https://github.com/Byter3/Hou-lm/blob/main/docs/visual-reference.md) · [Diagnostics](https://github.com/Byter3/Hou-lm/blob/main/docs/tool-stream-troubleshooting.md) · [After Effects client](https://github.com/Byter3/Ae-lm) · [MIT](https://github.com/Byter3/Hou-lm/blob/main/LICENSE)
