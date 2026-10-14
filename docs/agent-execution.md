# Agent execution and history

The central model writes Python for the artist's request. Hou-lm compiles it, then runs the prepared source with the **real `hou` module in the current Houdini process**. This is one general executor alongside efficient read tools and typed edit shortcuts. Task-specific test scripts are not used as assistant recipes.

The default workflow reviews source before Apply. **Auto-run** bypasses that popup while retaining preparation, validation, target checks and command receipts. It starts off, persists as a local preference, and is not enabled by loading a HIP history file. Each panel controls only its own open session.

## Python tool contract

`houlm_propose_python` accepts:

| Field | Meaning |
| --- | --- |
| `summary` | Purpose and intended changes, including callbacks, file writes or expensive processing |
| `code` | Actual Python source, up to 24 KiB UTF-8, without Markdown fences |
| `target_paths` | Up to 32 distinct existing node/parent paths for identity checks; empty for a scene-global action |

Preparation does not execute code. Apply or Auto-run executes the exact prepared source once. Reject or closing a review executes nothing. Target identities, selection and session metadata are checked again before execution. These checks detect replaced targets; they are not a snapshot of every value arbitrary Python might read. Restoring history never authorizes replay.

Each action has a fresh namespace containing `hou`, `print`, `checkpoint()` and `result`. Ordinary synchronous Python functions, classes, loops, comprehensions and common builtins are supported. Allowed imports are `hou`, `math`, `json`, `re`, `collections`, `itertools`, `functools`, `statistics`, `random`, and installed `pxr` modules. Direct shell/network/credential access, UI capture and runtime source evaluators are not provided.

The executor has no per-node or per-parameter HOM method catalog. Model-written code can combine supported public scene APIs for parameter layouts, ramps, expressions, keyframes, callbacks and graph editing. Existing data should be preserved unless the artist requests replacement. File/cache/render actions must fit the request and be apparent in the source; creating a cache node does not itself authorize running it.

Set `result` to a small JSON-compatible value describing observed results and changed paths. Returned output includes bounded result data and printed text. Failures include the exception type/message and a source line when available, without traceback locals or arbitrary HOM object representations. Total output is bounded to 32 KiB and truncation is explicit. Python variables do not persist across actions; state lives in Houdini and the conversation.

## Inspect, execute and correct

The agent can inspect nodes, discover installed types/parameters, and read installed HOM documentation before composing Python. `houlm_hom_help` returns docstrings and signatures without invoking the documented API. This avoids relying only on examples for a different Houdini version.

Execution returns observations or errors to the model, which can inspect affected nodes and propose a correction. Routine checks use authored parameters, wiring and available last-cook/VEX diagnostics. They do not require a fresh cook, simulation or full-scene integrity pass. Existing diagnostics must be distinguished from freshly evaluated output.

An older parameter read is not fresh verification. The model is instructed to use narrow current reads or an adequate fresh selection snapshot when reporting the current scene. Those instructions do not automatically verify every claim in its prose. **Text reply only** confirms that no model-requested tools ran for that message; previous history or a selected-node snapshot may still have supplied evidence.

## Execution and failure semantics

HOM runs on Houdini's main thread inside an Undo group for supported operations. **An Undo group is not a transaction.** Exceptions do not reverse earlier Python statements, and filesystem/render/callback effects may not be undoable. Python failures report whether execution started, possible partial changes and `rollback: not_attempted`; the panel does not call global Undo automatically. The agent should inspect before proposing a repair.

Typed edit batches have a separate best-effort rollback path. Inspect its reported status rather than assuming everything was restored. Completed edits and their records survive a later model or HTTP failure.

Python has a cooperative 15-second / 200,000-trace-event budget. Loops can call `checkpoint()`, and Houdini's interrupt dialog can request cancellation. A native call can block past the deadline until it returns. Stop, New Chat, disconnect and changed targets invalidate pending work, but do not undo completed operations.

Source and output filters catch common mistakes and direct access to unrelated facilities. **They are guardrails, not a security sandbox.** HOM, installed assets and callbacks run with the artist's permissions. In-process Python cannot guarantee studio-wide filesystem, network or screenshot isolation.

## Request limits and recovery

A user turn allows up to **24 model requests and 96 tool calls**, with at most **eight calls and one proposal per response**. Tool results automatically feed the next request; eight calls is a batch limit, not the whole task's allowance. The final allowed model request is reserved for text.

After discovery followed by a text answer with no proposal attempted, one completion check can ask the model whether authorized work remains. It preserves the request's scope and may correctly conclude that a read-only task is complete. It does not force edits or override review settings. A clean typed rollback can also permit a bounded recovery step; uncertain rollback requires inspection.

A completed response with no answer/tool request, or a malformed argument batch, can trigger one recovery request from valid history. They share one allowance per turn and consume the ordinary budget. No tools from the rejected response execute. Recovery preserves completed results and does not itself replay an action. Repeated failure stops with diagnostics; Stop and panel closure cancel queued recovery.

Network inactivity defaults to 10 minutes, configurable in Connections. Each model response has a 30-minute total limit. Timeouts do not automatically retry. See [connection diagnostics](tool-stream-troubleshooting.md) for safe evidence collection.

## Model context and local history

The archive and model request are separate:

| Storage | Purpose |
| --- | --- |
| `.houlm/<full HIP filename>.json` beside the HIP | Full accumulated conversations, saved messages/tool results, drafts, action records and bounded scene cache |
| Outgoing model request | A bounded working context built from the active conversation and relevant scene hints |

Save As carries the accumulated archive into the new HIP version's sidecar. The archive is not pruned to fit the model. Unsaved scenes retain history only in the current Houdini process until first save. Resume restores chat without sending a request or executing saved code. Interrupted reviews are cancelled; interrupted execution is recorded as uncertain. Corrupt files, conflicting writers and write failures produce storage warnings. See [saving and resuming](houdini-panel-quickstart.md#conversations-saved-with-the-hip).

Working context keeps up to six recent assistant responses/tool batches and targets 128 KiB for the complete serialized request, including instructions and tool definitions. Recent retained tool results remain exact while they fit; they are not automatically replaced merely because a turn completed. Older turns or batches can be removed, with compact action outcomes and explicit historical excerpts retained for continuity. This deterministic local reduction does not call another model or cook the scene.

The current artist prompt is never truncated. Large tool results may become explicitly incomplete excerpts; retained calls stay paired with their results. Provider continuation state stays exact or leaves with its batch. A large prompt or required action record may exceed the 128 KiB target while fitting the 512 KiB hard request limit. These are byte budgets, not a provider token-window guarantee. An unfit request stops with a size error. None of this changes saved messages or executes commands.

The scene cache holds shallow `/obj` and `/stage` outlines, selected/referenced paths and bounded observations from successful node, child and parameter reads. Restored observations are stale hints. Node callbacks invalidate them where available; fresh narrow reads remain necessary before relying on earlier values. The cache does not index the entire HIP or guarantee recall of every archived detail.

## Visible commands and change review

Assistant explanations, **Tool Activity** and command receipts use separate transcript cards. Activity and receipts start collapsed, including after restore. **Inspect commands** reveals exact Python or typed arguments, execution outcomes and a Copy action. Expanding/copying a record never runs it; a prepared or rejected command is distinguished from an executed one.

Typed text edits show unified before/after diffs. Python review shows the exact source without predicting its result. **History → Changes** records status and bounded authored before/after observations for declared targets: parameters, wiring and immediate children. Captures can be partial; no observed difference is not proof that nothing changed. The journal adds no forced cook or functional validation.

## Verification scope

Unit tests use synthetic scenes and local HTTP fixtures to exercise parsing, exact-source review, output bounds, stale targets, cancellation, partial failures, context reduction and saved history. They do not prove a model will generate a correct scene or that a deployed server path is reliable.

`scripts/verify_houdini_python.py` runs unrelated Python snippets through the executor in a separate, fresh Houdini process. Those are regression tests, not templates used by the assistant. `scripts/verify_houdini_history.py` exercises first save, Save As, Load and Clear using temporary HIPs. Neither is a substitute for interactive model-to-panel checks.

Use the [artist test prompts](houdini-panel-quickstart.md#test-auto-run-command-receipts-and-resume) after a relevant update. Check receipts against actual nodes, review with Auto-run off, popup-free execution with it on, persistence and Undo. The supported target is Windows Houdini 20.5.445/Python 3.11; broader versions, platforms and multi-user deployments need their own acceptance testing.
