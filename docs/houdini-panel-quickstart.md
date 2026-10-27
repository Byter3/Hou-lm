# Panel reference

[Install](package-install.md) · [Backend setup](https://github.com/Byter3/studio-assistant-backend/blob/main/docs/server-setup.md) · [Ae-lm](https://github.com/Byter3/Ae-lm)

## Connect

**Connections** (sliders icon): Open WebUI base URL, user API key, then select a model. Windows saves the login and reconnects on panel load. **Sign out** removes it. Use a tool-capable model; visual tasks also require image input.

## Controls

**Ctrl+Enter** sends. Drag nodes or type `@` to reference them.

| Control | Function |
| --- | --- |
| Scene | Enable host tools; changing it starts a new chat. |
| Auto-run | Skip command review; remembers the local setting. |
| Nodes | Optional selected-node details and local preview. |
| Attach | Add PNG/JPEG/WebP references; active until removed. |
| Viewport access | Permit native current-frame capture; resets off on panel load. May cook. |
| + / History | New conversation / resume saved conversations. |
| Log | History path, save status and folder access. |
| Stop | Cancel pending work; completed changes remain. |

Expand activity/command cards for exact source and results. Routine reads inspect authored state and existing diagnostics. [Visual workflow](visual-reference.md) · [Execution details](agent-execution.md).

## Conversations saved with the HIP

History: `.houlm/<HIP filename>.json`; images: adjacent `.json.assets/`. **Save As** carries both forward. **Log → Open folder** locates them. Unsaved scenes keep history only in process memory.

Resume restores the transcript without replaying commands. Full local history is retained separately from bounded model context.

## Test auto-run, command receipts and resume

Inspection:

> List the immediate children of /obj using scene tools. Do not modify anything or force a cook.

Recent-result recall, two successive messages:

> Find installed SOP types matching grid. Reply only Ready.

> Using only that tool result, list the exact type names. Do not call tools again.

Edits, in a disposable scene:

> Create /obj/HOULM_TEST with a Grid → Attribute Wrangle → OUT chain. Add amplitude and frequency controls to produce a sine wave. Check authored wiring and available errors without forcing a cook.

Compare receipts with the scene. Test review and Auto-run, then save, Save As and reopen the panel; history should return without execution.

For updates use [package replacement](package-install.md#upgrading) or the developer [reload command](../CONTRIBUTING.md#load-panel-updates). For failed requests, use [diagnostics](tool-stream-troubleshooting.md).
