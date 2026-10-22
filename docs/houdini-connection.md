# Houdini connection and scene tools

For launching, login and everyday use, start with the [panel quickstart](houdini-panel-quickstart.md). This document describes the connection boundary and tool interface.

## Architecture

```text
Artist A: Houdini panel + local scene tools --\
                                            > Open WebUI -> LiteLLM -> configured models
Artist B: Houdini panel + local scene tools --/
```

Each artist uses their own Open WebUI account and permitted model list. Model requests go through the central service; returned scene tools run only inside that artist's Houdini process. Tool results return through the same connection. No model inference runs on the workstation.

Each panel owns its conversation and pending review. The panel opens no listening port and needs no MCP server or shared scene router. It operates on an artist's open session, not unopened HIPs or headless jobs. Concurrent inference capacity and account permissions are managed centrally; large multi-user deployments still need their own acceptance testing.

The panel sends text/JSON: prompts, selected-node snapshots when enabled, bounded scene observations, tool arguments/results and retained conversation context. It does not upload the entire HIP, capture the screen or provide image uploads. The native gray/teal interface is painted locally.

## Server requirements and login

- Open WebUI must allow the artist's user API key to access `/api/models` and `/api/chat/completions`.
- The selected model and gateway path must support streamed native function/tool calls. Successful text chat alone does not establish this.
- Enter the Open WebUI base URL in **Connections**, for example `https://openwebui.example.com`. The panel has no fixed studio address.
- Use an Open WebUI user key, not the LiteLLM master key or a provider credential.

The existing central stack can provide local or hosted models through LiteLLM. The shared [backend repository](https://github.com/Byter3/studio-assistant-backend) owns server deployment sources. Deployment-specific ChatGPT streaming compatibility is described in its [gateway guide](https://github.com/Byter3/studio-assistant-backend/blob/main/gateway/README.md); model configuration belongs to the server, not the Houdini launcher.

Windows saves a successful login using current-user DPAPI, bound to the normalized server URL, including scheme, port and base path. Preferences save the URL, model and local Auto-run setting without the key. A different URL cannot inherit the old server's credential. Sign out removes the saved key for the current server; closing the panel retains it. There is no plaintext credential fallback.

Redirects are rejected and HTTPS certificate validation is enabled. HTTP URLs are accepted for local deployments but do not encrypt traffic. Use your deployment's approved transport and treat scene sidecars as project data.

## Scene access

**Scene** starts enabled. The model chooses inspection or edits from the artist's request; there is no separate build mode. Disabling it starts a text-only chat. Optional selected-node attachment under **Nodes** is independent of live tools: the assistant can inspect unselected nodes with Scene enabled.

Read tools expose bounded authored data and existing diagnostics without deliberately cooking nodes or evaluating expressions. Existing errors/warnings can be stale. General Python can use broader HOM APIs when the task requires them; see [execution semantics](agent-execution.md).

| Read tool | Result |
| --- | --- |
| `houlm_session_info` | Houdini version, session identity and scene basename |
| `houlm_get_selection` | Selected node paths |
| `houlm_inspect_node` | Type, connections, bounded authored parameters/VEX and existing diagnostics |
| `houlm_list_children` | Paginated immediate children of a network |
| `houlm_list_node_types` | Exact installed type names, searched by parent or category |
| `houlm_parameter_schema` | Instantiated parameter names, authored/default values, ranges, menus and editability |
| `houlm_node_type_schema` | Static templates and defaults before creating a node |
| `houlm_hom_help` | Installed HOM docstrings, signatures and paginated public members; does not call the documented method |

HOM operations run on Houdini's main thread. Streamed tool calls must be fully assembled and validated before dispatch. A malformed response does not execute a partial command batch.

## Changes

`houlm_propose_python(summary, code, target_paths)` provides general Python execution using the real `hou` module. Preparation compiles the source; execution runs that exact prepared source after review or when **Auto-run** is enabled. This is the path for ramps, parameter interfaces, expressions, callbacks and other tasks beyond the typed shortcuts.

`houlm_propose_edits` is a convenience interface with up to 32 ordered operations per proposal:

| Operation | Change |
| --- | --- |
| `create_node` | Create an explicitly named node of an installed type |
| `add_spare_parameter` | Append a supported artist control |
| `set_parameter` | Set an existing supported scalar literal, including VEX text |
| `connect_nodes` | Connect or disconnect an input |
| `set_flags` | Set supported display/render flags |
| `set_position` | Place a node in the network editor |

Later operations can reference nodes created earlier in the proposal. Typed parameter edits cannot overwrite expressions/animation or invoke preset callbacks. General Python is available when a requested task needs those operations.

Both proposal paths recheck relevant scene state and record outcomes. One model response may contain only one proposal. With Auto-run off, Reject leaves it unapplied. Restoring history or expanding receipts never applies it.

Typed batches attempt best-effort restoration after a partial failure. General Python does not automatically roll back previous statements. Inspect the reported outcome before retrying either path. A later HTTP failure leaves completed edits in place. Undo groups cover only operations Houdini records; edits may trigger automatic cooking or asset callbacks.

### Parameter controls

The typed spare-parameter shortcut supports float, integer, string, toggle, vector, color and fixed-menu controls. It appends to the instance's parameter interface, preserving existing controls and authored channels. It does not edit the HDA definition or unlock assets. Numeric controls can specify soft or strict ranges; menu controls use explicit token/label entries.

Ramps, folders, multiparms, existing-definition edits and default expressions use general Python/HOM. These are examples of that executor's scope, not a catalog of hardcoded recipes. Static discovery cannot always predict an HDA's instantiated parameters; inspect the created node when necessary.

## Central API behavior

Requests use `/api/chat/completions`, native `tools` and `stream: true`. They omit Open WebUI chat/socket IDs, allowing the panel to receive tool calls for local execution. The panel sends matching tool results back with call IDs and retained provider continuation state.

The parser assembles SSE fragments and rejects conflicting metadata, missing required arguments and invalid completion state. Completed empty replies and malformed argument batches share one bounded recovery attempt per user turn. Previously completed results are retained; the recovery does not itself replay commands. See [request limits and recovery](agent-execution.md#request-limits-and-recovery).

**Error details** provides fixed diagnostic messages, HTTP status, UTC time, timings and stream counters. Raw response text, prompts, scene content and credentials are omitted. These counters describe what reached the panel; they do not by themselves identify the failing service. See [stream troubleshooting](tool-stream-troubleshooting.md).

## Acceptance checks

Use a disposable HIP and the [quickstart test prompts](houdini-panel-quickstart.md#test-auto-run-command-receipts-and-resume):

1. Confirm login survives reload, model access is correct, and live reads match the scene.
2. Test a small change with review enabled, Reject, a fresh Apply, and Houdini Undo.
3. Enable Auto-run and inspect its exact command receipt and resulting scene.
4. Save, Save As, reload, and resume history without replaying commands.
5. For each deployment, test separate artist accounts, model permissions and concurrent requests in independent Houdini sessions.

Automated fixtures cover protocol and executor behavior; they do not guarantee a model's generated scene is correct. The current validated target remains Windows Houdini 20.5.445 with Python 3.11/PySide2.
