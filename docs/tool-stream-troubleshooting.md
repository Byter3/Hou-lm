# Troubleshooting model replies and tool streams

Hou-lm receives streamed Chat Completions through Open WebUI. A model request can succeed upstream while the panel receives an empty, incomplete or invalid response. Diagnose the observed boundary before changing the panel, container image or connection settings.

## Read the panel evidence

Expand **Error details** and note the UTC timestamp, error code, request kind, HTTP status, response bytes and wire counters. These diagnostics omit prompts, scene data, credentials and raw upstream text.

| Observation | What it establishes |
| --- | --- |
| `Waiting for server`, no observed HTTP status, zero response bytes | The panel has not observed response headers or body. It does not identify the component responsible. |
| HTTP 200 with an empty delta, `finish_reason: stop` and no text or tools | The client received a completed but unusable reply. HTTP success alone is insufficient. |
| `tool_arguments_missing` | A tool identity arrived without its required JSON arguments. No tools from that rejected response execute. |
| `invalid_tool_arguments` | A returned tool batch failed argument validation. The panel does not repair it by guessing. |
| A visible scene-query or command receipt | That local tool was processed; inspect its result to distinguish success, rejection and failure. |
| A LiteLLM success record | The gateway recorded success at its boundary. Match the timestamp and request before relating it to a panel failure. |

Earlier successful tools in a turn may already have changed the scene before a later model reply fails. A timeout does not undo those changes or replay them. The panel shares one bounded recovery attempt for invalid argument batches and completed empty replies; a timeout is not automatically retried.

The panel's **Connections** settings control response inactivity waiting. The default is ten minutes, with a separate thirty-minute total cap per response. Raising a limit does not repair a reply that never arrives. Use the short delivery probe below when diagnosing such a wait.

## Response delivery stalls

First distinguish ordinary browser chat from the panel's tool-enabled request. A successful browser greeting shows that that request worked; it does not test the same tool schema, saved history or result continuation. Conversely, a panel failure does not establish that the whole server is unreachable.

The panel reports upload, header and first-byte timing when available. Correlate those with the Open WebUI completion route and LiteLLM records using UTC timestamps. An Open WebUI HTTP status line and a gateway success entry still do not prove complete client receipt. Preserve the working versions and compare one variable at a time.

## Run synthetic checks

Run these commands in **PowerShell from the repository root**, not Houdini's Python Shell. Set the executable path to your installation:

```powershell
$houdiniPython = 'C:\Program Files\Side Effects Software\Houdini 20.5.445\python311\python.exe'
```

The client probes use the panel's saved Open WebUI connection, credential and selected model. Connect the panel first if they report `saved_connection_unavailable`. They make real model requests with synthetic prompts, but never import a live scene, execute returned tools or change saved history. Their output contains counts and outcomes rather than response text or credentials.

### Delivery timing

```powershell
& $houdiniPython scripts/diagnose_response_delivery.py --route chat --full-schema --deadline 45
```

This sends one synthetic request with the real panel tool definitions to `/api/chat/completions`. It measures headers, first byte, stream termination and HTTP EOF, with a 45-second socket/request deadline. `--deadline` accepts 1–60 seconds. A completion outcome describes delivery, not scene execution or model correctness.

Omit `--route chat` to compare that route with `/openai/chat/completions`; the default is both, sequentially. The passthrough probe removes Open WebUI-specific fields. It does not alter the panel connection. A route may be unavailable in a particular Open WebUI deployment.

### Tool arguments and final answer

```powershell
& $houdiniPython scripts/diagnose_model_answer.py --repeat 3
```

Each repeat makes at most two requests: a synthetic addition tool call, then a canned result and an expected final answer. Expect `passed: true` and matching argument/answer flags. This tests the continuation after a tool result as well as the initial call.

For a separate multiple-tool check:

```powershell
& $houdiniPython scripts/diagnose_model_tools.py --repeat 3
```

Expect three parsed tool calls and `arguments_match_synthetic_request: true` in each report. Add `--compare` to run both default calls and `parallel_tool_calls: false` (six requests with `--repeat 3`). Disabling parallel calls is a diagnostic comparison, not an assumed fix.

These two scripts accept 1–5 repeats and use a 180-second socket timeout per request. Unlike the delivery probe, they do not enforce a fixed total elapsed deadline. Exit code 0 indicates the script's checks passed, 1 a failed check/request, and 2 an unavailable saved connection. A successful synthetic request does not verify a complete Houdini task.

## Missing final text or arguments

The client supports consistent final-message snapshots as well as incremental tool arguments. It preserves repeated partial text but rejects conflicting identities, invalid JSON and missing required arguments. It cannot recover content that an upstream converter omitted before transmission.

The optional [LiteLLM adapter](../gateway/README.md) addresses specific reproduced ChatGPT-provider conversion patterns. It preserves final tool arguments and answer text in supported versions. It is not required for every model or evidence that all empty responses have that cause.

To inspect an installed LiteLLM converter offline, copy `scripts/diagnose_litellm_stream.py` into that environment and run:

```sh
python /path/to/diagnose_litellm_stream.py --json
```

It uses synthetic provider events and makes no model requests. Exit 0 means covered fixtures passed, 1 a conversion failure, and 2 that installed interfaces could not be tested. It probes the stock converter in its own process; it does not install the worker adapter. For an adapter deployment, use its source checks, startup marker and a client-route probe together.

## Minimal Houdini retest

In a new conversation with **Scene** enabled:

```text
Use scene tools to list the immediate children of /obj. Report their names and types. Do not modify anything or force a cook.
```

For a memory check, send these consecutively in the same conversation:

```text
Use scene tools to find installed SOP node types matching grid. Do not modify anything. When finished, reply only Ready.
```

```text
Using only that previous tool result, list the exact node-type names you found. Do not call any tools again.
```

Compare the second answer with the first query's expanded result. It should require no new tool call. A fresh-conversation pass and an older-conversation failure are separate observations; record that distinction when reporting a problem.

For a public issue, include versions, the sanitized Error details, relevant probe outcomes and a minimal synthetic prompt. Do not upload the HIP sidecar as a diagnostic log: it contains full conversation and tool history, including scene content and executed source.
