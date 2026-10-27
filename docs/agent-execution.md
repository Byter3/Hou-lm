# Execution and history reference

The model can inspect nodes, read installed HOM documentation, execute Python and inspect the result before proposing a correction. Routine verification uses authored values, wiring and existing cook/VEX diagnostics; fresh evaluated output is a separate operation.

## Python tool contract

`houlm_propose_python(summary, code, target_paths)` prepares exact source for review or Auto-run. Limits: 24 KiB source, 32 declared target paths, 32 KiB returned output.

The namespace provides `hou`, `print`, `checkpoint()` and `result`. Return compact JSON-compatible observations through `result`. Standard permitted imports include math/JSON/collection helpers and installed `pxr`; direct shell/network access and source evaluators are not provided. This remains general HOM scripting, not a task-recipe system.

## Execution and failures

HOM runs on the UI thread with an Undo group where supported. Python errors can leave partial changes; inspect before repair. Typed edit batches have separate best-effort rollback. **Stop** prevents pending work but cannot guarantee interruption of a blocking native call.

Python's cooperative budget is 15 seconds / 200,000 trace events. Native calls can exceed it until they return. Source checks and review are not a sandbox. [Data handling](../SECURITY.md).

## Request limits and recovery

A turn allows 24 model requests and 96 tool calls; each response allows eight calls and one proposal. Empty replies and malformed argument batches share one recovery attempt. Completed commands are never replayed on retry, reload or resume.

Response inactivity defaults to 10 minutes, configurable in Connections, with a 30-minute total cap per response. Timeouts stop without automatic retry. [Diagnostics](tool-stream-troubleshooting.md).

## Model context and local history

`.houlm/<HIP filename>.json` retains the complete conversation, tool results and actions. Image assets sit beside it. Save As carries history forward; unsaved scenes retain it only in process memory.

Outgoing context is bounded separately. Recent results stay exact while they fit; older observations may become excerpts or stale hints. Active references and the latest current-turn capture are added separately. Restored history does not authorize or replay execution.

[Panel controls and test prompts](houdini-panel-quickstart.md#test-auto-run-command-receipts-and-resume) · [Visual workflow](visual-reference.md) · [Developer checks](../CONTRIBUTING.md#run-tests)
