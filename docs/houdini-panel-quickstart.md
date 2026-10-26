# Using the Houdini panel

Hou-lm is a native, dockable assistant for an artist's open Houdini session. It uses your Open WebUI account and centrally configured models to inspect the scene, explain it, and perform requested changes. The supported target is **Windows, Houdini 20.5.445, Python 3.11 and bundled PySide2**. Other Houdini builds and platforms are not yet validated.

Install the [Houdini package](package-install.md) and connect to your studio service. For source-checkout launch and reload commands, see [Contributing](../CONTRIBUTING.md#launch-a-source-checkout). Reference images and viewport capture require the current source build; they are not included in the published v0.1.0 ZIP.

## Open the panel

For a normal installation, extract `hou-lm.json` and the `hou-lm` folder from the plug-in ZIP into `$HOUDINI_USER_PREF_DIR/packages`, then restart Houdini. Choose **+ → New Pane Tab Type → Hou-lm Assistant**. No launch command is needed. Follow the [package installation guide](package-install.md) for the exact layout and replacing an older checkout registration.

Use Houdini's normal split-pane and tab controls to dock the assistant where you want it. Save your desktop layout to retain the pane arrangement.

## Connect

1. Open **Connections**, the sliders icon at the top right.
2. Enter your Open WebUI base URL, for example `https://openwebui.example.com`. Do not append `/api` or `/v1`.
3. Paste your **Open WebUI user API key** and select **Connect**. This is not a LiteLLM master key or provider API key.
4. Choose a permitted model at the bottom of the composer, then send `Reply with exactly: OK`.

Your Open WebUI administrator must enable API keys for your account and allow the panel's `/api/models` and `/api/chat/completions` endpoints if endpoint restrictions are in use. Login fields stay in the popup. The connection dot's tooltip shows connection status.

After a successful connection, Windows stores the key encrypted with current-user DPAPI, bound to that exact server URL. Reopening or reloading restores the login and reconnects. **Connections → Sign out** removes the saved key for that server. Preferences live in `%LOCALAPPDATA%/Hou-lm/preferences.json`; encrypted credentials are kept separately in `%LOCALAPPDATA%/Hou-lm/credentials`. Credentials are not saved in scene history.

## Use the assistant

**Ctrl+Enter** sends; Enter adds a new line. Describe the task in the same conversation, whether you need an explanation, inspection or edits. There is no separate build mode.

| Control | Behavior |
| --- | --- |
| **Scene** | Enables tools for the open Houdini session. Starts enabled. Turning it off starts a text-only conversation. |
| **Auto-run** | Runs prepared Python and typed scene edits without an Apply popup. Off by default; remembers your choice on this workstation. |
| **Attach** | Adds image references by file picker; drag/drop and paste are also supported. References stay active until removed. |
| **Viewport access** | Allows native current-frame viewport capture. Starts off on each panel load. Capture may cook; see [visual workflow](visual-reference.md). |
| **Nodes** | Contains optional **Attach selected node details on send** and a local preview. **Nodes +** means the attachment is enabled. |
| **+** in the header | Starts a new conversation; older conversations remain in History. |
| **≡ History** | Browses saved conversations and the Changes journal; resumes a conversation without executing it. |
| **Log** | Shows the history file's path and storage status, with Save now, Copy path and Open folder actions. |

Drag nodes into the composer, or type `@` to reference them. A dropped node adds its path, not a parameter dump. With **Scene** enabled, the assistant can inspect referenced or unselected nodes as needed; the optional selection attachment is not required for that. Previewing a selection is local; sending reads the selection again.

Assistant explanations, **Tool Activity**, and exact command receipts appear separately. Activity and command receipts start collapsed. Expand an activity summary for observations, or **Inspect commands** for the actual Python/tool arguments, execution result and Copy action. Expanding a card never runs anything.

The activity banner shows the current stage and elapsed time, including waiting for the server, receiving data, inspecting nodes, reviewing or applying changes. **Stop** cancels pending panel work; changes already applied remain in Houdini. A blocking native Houdini operation may take time to return.

With **Auto-run** off, review the proposed source or operations before applying them. With it on, the same preparation and validation run without the popup. General Python can create and connect nodes, edit parameter interfaces, add ramps, and use other supported HOM APIs. It is not limited to task-specific scripts. See [execution behavior and limitations](agent-execution.md).

Routine checks inspect authored parameters, wiring and available error/warning messages without forcing a cook. They do not establish rendered or simulated correctness. Edits can still trigger Houdini's automatic cooking.

## Conversations saved with the HIP

Save the HIP once to create its history file. Chat then saves automatically in a `.houlm` subdirectory beside it:

```text
shots/
  shot_v001.hip
  shot_v002.hip
  .houlm/
    shot_v001.hip.json
    shot_v002.hip.json
```

Each JSON contains conversations, tool results, drafts and action records for that HIP version. **Save As** carries the accumulated history into the new version's sidecar; later work updates the new file. Image assets live beside it in `<HIP filename>.json.assets`; keep that folder with the JSON. Saving chat does not save HIP geometry.

Click **Log → Open folder** to find the exact location. A save failure changes the button to **Log !**. The path follows the HIP's actual location, including redirected Desktop or project folders. A saved file named `untitled.hip` is supported.

Unsaved scenes keep history in process memory, including across panel reloads, until the first save. That memory does not survive a Houdini crash. Unreadable files, conflicting writers and unwritable folders produce a warning instead of overwriting the conflicting file.

**≡ History → Resume selected conversation** restores the transcript and draft. It does not resend messages or run saved commands. Interrupted reviews are cancelled; interrupted executions are marked uncertain so their targets can be inspected before continuing.

The complete archive stays local and is not all sent with every request. A separate working context retains recent exchanges and tool batches, with explicit excerpts and action outcomes when older content is reduced. The current prompt remains intact. The assistant may need a fresh scene read when older details no longer fit. **Log** reports when context has been shortened; no saved history is deleted. See [model context](agent-execution.md#model-context-and-local-history) for limits.

## Test auto-run, command receipts and resume

Start with this read-only prompt:

```text
Use scene tools to list the immediate children of /obj. Report their names and types. Do not modify anything or force a cook.
```

Expect a scene-query receipt and an answer based on its result. To check recent tool-result memory, send these two prompts consecutively in the same chat:

```text
Use scene tools to find installed SOP node types matching grid. Do not modify anything. When finished, reply only 'Ready'.
```

```text
Using only that previous tool result, list the exact node-type names you found. Do not call any tools again.
```

The second answer should match the first receipt and report no new tool calls. Installed type names can vary between Houdini setups.

For edits, use a disposable HIP and enable **Scene**. With **Auto-run** off, send:

```text
Create a Geometry object called HOULM_TEST. Inside it create a Grid, a point Attribute Wrangle named wave, and an OUT null. Add amplitude and frequency float controls plus a Float Ramp called falloff_ramp to the wrangle. Use them to make a sine wave whose displacement fades at both X edges. Wire the nodes and make OUT the display node. Perform the changes, then inspect authored parameters, wiring and available errors. Do not force a cook or write files.
```

Review the proposed commands and apply them. Inspect the resulting nodes and expand the command receipts. Then enable **Auto-run** and send:

```text
Set amplitude to 0.25 on /obj/HOULM_TEST/wave. Keep the network and other controls intact. Perform the change and read back its authored value without forcing a cook.
```

That change should execute without a review popup. Save the scene as `shot_v001.hip`, leave an unsent draft, and reload the panel: transcript, receipts and draft should return. Use **Save As** to create `shot_v002.hip` and confirm both history files exist through **Log**. Resuming history alone must not execute any command.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| Installed package has no named pane entry | Confirm `hou-lm.json` is directly inside `packages` beside `hou-lm/`, restart Houdini, and check the supported Python/PySide2 build. See [package installation](package-install.md). |
| Updated files have no visible effect | Restart after replacing an installed package; source developers can use the [reload helper](../CONTRIBUTING.md#load-panel-updates). |
| No history file | Save the HIP, then open **Log** for the actual path or save error. |
| Text answer claims fresh inspection but no tools ran | Expand the receipt. Ask explicitly for a fresh scene-tool read; an attached snapshot or older history may be its source. |
| Request fails or waits for data | Open **Error details** and follow [stream troubleshooting](tool-stream-troubleshooting.md). A successful browser chat alone does not test the panel's tool flow. |

**Connections → Wait without response data** controls the inactivity limit for new requests, from 1–30 minutes (default 10). Incoming data resets that limit; each model response also has a 30-minute total limit. Timeouts do not automatically retry, execute partial tool calls or undo completed edits. Safe diagnostics contain timings and counters rather than prompts, keys or raw responses.
