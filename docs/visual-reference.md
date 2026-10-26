# Reference images and viewport capture

These features are available in the current source build. They are not included in the published v0.1.0 ZIP. Use a model route that supports both image input and tool calls.

## Attach a reference

Click **Attach**, drop an image into the prompt, or paste an image. You can attach up to three PNG, JPEG or WebP files.

References stay in the composer for follow-up messages. Remove a thumbnail to stop sending it, or start a new chat to clear active references. Source images are unchanged; the model receives a copy with metadata removed, reduced to at most 1280 pixels on its longest edge and 512 KiB. Documents and project files are not supported attachments.

## Let the assistant inspect its work

1. Keep the intended Scene Viewer visible. If several Scene Viewers are visible, leave just the one you want to capture visible.
2. Enable **Scene** and **Viewport access**. In a split Scene Viewer, select the intended viewport in Houdini.
3. Ask for a capture or a comparison with your reference.

The selected viewport is found automatically, including inside a Context View. There is no screen-region picker. Each capture appears in an expandable image card in the conversation.

Capture uses Houdini's native single-frame flipbook at the current frame. **It may evaluate or cook that frame.** It does not open MPlay, initialize simulations, play a frame range or capture the desktop. Your saved flipbook settings are preserved.

For Solaris, progressive-render waiting is limited to five seconds. This does not limit cooking time, and the image may still be converging. A viewport preview is not final-render verification. If your task forbids all cooking or evaluation, leave viewport access off.

Viewport access starts off whenever the panel loads and is independent of **Auto-run**. It grants capture access; edits still follow the command-review setting. A turn allows up to three capture attempts. Continue with a follow-up message for further refinement.

## Example prompts

To check image input, attach a reference and send:

> Describe the attached image's composition, colors and proportions. Do not use scene tools or modify anything. If the image is unavailable, say so.

To check capture:

> Capture my selected viewport once and describe what you see. Current-frame evaluation is allowed. Do not modify nodes, change frames, initialize simulations or start a final render.

To compare without editing:

> Compare my current viewport with the attached reference. Identify the three largest differences in composition and proportions. Current-frame evaluation is allowed; do not edit the scene or start a final render.

To refine a scene:

> Use the attached reference to improve this scene's composition and proportions while keeping it procedural. Inspect the relevant nodes first, then make focused changes. Capture the result and correct the largest remaining differences within the available capture budget. Current-frame evaluation is allowed. Do not change frames, initialize simulations or start a final render. Report any differences you could not resolve.

## Save and resume

Images are saved in `.houlm/<HIP filename>.json.assets` beside the conversation JSON. Keep both when moving history. **Save As** copies the referenced assets with the history. Unsaved scenes keep images only in memory until the HIP is saved.

The model receives active references and the latest capture from the current turn. Older captures remain in local history without being resent on every later turn. Missing or unreadable assets produce an error; reattach the reference if needed.

## Troubleshooting

- **No Scene Viewer found:** open a Scene Viewer and keep it visible.
- **More than one candidate:** leave one Scene Viewer visible, then retry.
- **The wrong split view is captured:** select the desired viewport in Houdini before asking again.
- **Only the grid is visible:** check the displayed geometry, visibility and framing in the selected viewport.
- **Capture fails or the model cannot see the image:** expand the capture receipt and check that the chosen model route accepts images. Do not treat the assistant's description alone as proof of a successful capture.
- **Capture takes a long time:** native evaluation may be waiting on the scene. Test with a simple scene before using it on a heavy setup.
