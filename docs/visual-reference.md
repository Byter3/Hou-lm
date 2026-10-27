# Visual workflow

Use a vision/tool-capable model from the [shared backend](https://github.com/Byter3/studio-assistant-backend). These features require the current source build; published v0.1.0 predates them.

## References

**Attach**, drop or paste up to three PNG/JPEG/WebP images. References remain active for follow-ups until removed; New chat clears them. Model copies are limited to 1280 pixels/512 KiB, without modifying originals.

## Viewport feedback

Enable **Scene** and **Viewport access**. Keep one Scene Viewer visible; in split views, select the intended viewport. Detection is automatic, including Context View wrappers.

Capture uses a native single-frame flipbook: **current-frame evaluation/cooking is possible**. No MPlay, simulation initialization, frame-range playback or desktop capture. Saved flipbook settings are preserved.

Solaris progressive waiting is limited to five seconds, not total cook time. The preview may be unconverged. Capture permission resets off on panel load; a turn permits three attempts. Follow up for further refinement.

## Example prompts

Capture only:

> Capture the selected viewport and describe the image. Current-frame evaluation is allowed. Do not edit nodes, change frames or start a final render.

With a reference:

> Match the reference's composition and proportions while keeping the scene procedural. Inspect, edit, capture and correct the largest differences within the capture budget. Current-frame evaluation is allowed; no frame changes, simulations or final render.

Expand the capture card to inspect the actual image. For ambiguity, leave one Scene Viewer visible; for an empty grid, check display flags and framing.

## Storage

Keep `.houlm/<HIP>.json.assets` with its JSON. Save As carries assets forward. Only active references and the current turn's latest capture are sent; older captures remain local.

[Panel reference](https://github.com/Byter3/Hou-lm/blob/main/docs/houdini-panel-quickstart.md) · [Ae-lm visual workflow](https://github.com/Byter3/Ae-lm/blob/main/docs/visual-workflow.md) · [Data handling](https://github.com/Byter3/Hou-lm/blob/main/SECURITY.md)
