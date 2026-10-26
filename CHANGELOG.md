# Changelog

## Unreleased

- Reference image picker, drag/drop and paste, with persistent removable thumbnails for follow-ups.
- Separate session-only viewport permission, automatic native Scene Viewer identification and bounded visual feedback. Capture now uses Houdini's native single-frame flipbook at the current frame and may cook the scene; it does not open MPlay, initialize simulations or capture desktop pixels.
- Content-addressed image sidecars and late multimodal request construction separate from text/tool context fitting. Saved commands are not replayed.
- Reload without an open assistant pane now explains how to open the named panel instead of requesting an unnecessary restart.
- Installation and usage guides now separate artist workflows from source development and release instructions.

## 0.1.0 — 21 October 2026

- Ready-to-copy Houdini package archive with `hou-lm.json` and a relocatable client folder.
- Offline client packager with an explicit source allowlist and SHA-256 file manifest.
- Artist installation, studio distribution, upgrade and uninstall instructions; the source-checkout launch workflow remains available.
- Downloadable plug-in ZIP on GitHub Releases, linked directly from the README.

### Included working baseline

- Native dockable Houdini panel with saved login and central model selection.
- General Python/HOM execution, scene discovery and typed editing shortcuts.
- Reviewed changes or optional auto-run, with collapsed activity and exact command receipts.
- HIP-adjacent cumulative JSON history, conversation resume and bounded model context.
- Optional version-checked LiteLLM compatibility adapter and example server stack.
- MIT license, public setup guides, test documentation and CI configuration.
