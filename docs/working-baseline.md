# Working baseline — 15 October 2026

The `working-2026-10-15` Git tag preserves the artist-confirmed panel runtime used for initial public preparation. Use it as the comparison point for future changes. Documentation cleanup must not silently change that behavior.

## Confirmed interactively

On Windows with Houdini 20.5.445, Python 3.11 and bundled PySide2:

1. A new conversation called the live scene tool to list `/obj` children, then returned an answer matching the scene.
2. A query for installed SOP types matching `grid` completed with a tool result.
3. A subsequent message recalled all three exact type names without calling tools again. The response was checked against the saved JSON tool result, not just the displayed prose.

Earlier artist acceptance also exercised model-driven node creation, wiring, spare parameters and a float-ramp edit. These observations cover specific tasks; they do not certify every generated scene or all service configurations.

## Recorded local validation

At public preparation, the existing suite completed under Houdini's plain Python:

```text
Ran 659 tests
OK (skipped=48)
```

That is **611 passing tests and 48 optional dependency/platform skips**. The run includes offscreen Qt and loopback HTTP fixtures. It sends no model request and does not edit a live Houdini session. The optional LiteLLM and POSIX checks need their respective environments. See [test instructions](../CONTRIBUTING.md#run-tests).

A second local run disabled site packages and Houdini discovery to check the dependency-free path used by CI: **431 passed, 228 skipped** from the same 659 discovered tests. This was a Windows run; it does not establish the result on a hosted Linux runner.

All Houdini runtime files, panel definition, launch/reload/install scripts, test files, gateway runtime and Compose configuration were compared against a local pre-cleanup SHA-256 snapshot and remained byte-identical. Existing changes are confined to documentation, exclusions and adding `LICENSE` to the gateway source archive. New files provide licensing, contributor/release guidance and CI.

The isolated gateway packaging check confirmed that the source archive contains only its explicit allowlist, including the MIT license. It did not replace an older build archive or deploy an image.

GitHub-hosted CI is configured but has not been run as part of this local preparation. Do not interpret configuration as a passing hosted run.

## Retain these regression prompts

With **Scene** enabled, start a new conversation:

```text
Use scene tools to list the immediate children of /obj. Report their names and types. Do not modify anything or force a cook.
```

Expect a scene-query receipt and an answer matching the returned names and types. In the same conversation, send:

```text
Use scene tools to find installed SOP node types matching grid. Do not modify anything. When finished, reply only Ready.
```

Then:

```text
Using only that previous tool result, list the exact node-type names you found. Do not call any tools again.
```

Compare the final answer with the earlier expanded query result. Type names vary by installed assets; success means exact recall without another call. The working implementation retains recent tool results while they fit. Replacing completed turns with lossy summaries below the budget would regress this behavior.

Use the [quickstart edit and persistence prompts](houdini-panel-quickstart.md#test-auto-run-command-receipts-and-resume) when changing execution or history code. Run them on a disposable scene.

## Limits of this baseline

- It records a working panel, not proof that the earlier response-delivery timeouts have an established root cause.
- The exact server image deployed during acceptance was not verified. The repository's optional gateway adapter is a separate component with its own compatibility checks.
- Compose uses floating image tags and is an example, not a reproduction of the accepted server environment.
- Other Houdini builds/platforms, concurrent studio workloads, permission-denial scenarios and complex production simulations need further acceptance testing.
- Restored scene observations can be stale. Authored parameter/wiring inspection and available last-cook diagnostics do not establish freshly cooked geometry or render correctness.

Preserve this reference and change one subsystem at a time when investigating a regression.
