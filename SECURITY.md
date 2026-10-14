# Data and execution boundaries

Hou-lm is an assistant inside the artist's Houdini process. General Python execution has that process's permissions; syntax/import/output checks and change review are guardrails, not a security sandbox. Auto-run removes the review popup. Use it only with a model and service you trust to act in that session.

## What is stored and sent

- The Open WebUI API key is stored separately from project history using Windows current-user DPAPI protection.
- A HIP's `.houlm/<hip filename>.json` sidecar contains conversation text, tool results, commands, paths and scene observations. It is plain JSON, not encrypted.
- Model requests send a bounded selection of conversation and scene text to the configured Open WebUI service and its selected model provider. A local sidecar does not imply local-only inference.
- Error diagnostics report bounded status, timing and stream counters. Full histories and command receipts contain more detail and may include proprietary scene information.

## Failure behavior

An Undo group does not make Python execution transactional. Exceptions can leave partial changes; callbacks, file writes, cooks and renders may have effects outside Undo. Stop cancels queued work and future requests but cannot reliably interrupt a synchronous Houdini operation already running. The panel retains evidence of completed work after later request failures.

The optional gateway adapter is version-checked and separate from the panel. Treat server image, authentication and routing changes as deployment changes, with their own testing and rollback plan.

## Reporting

Use a private GitHub vulnerability report if the repository's **Security → Report a vulnerability** option is available. Otherwise, ask the maintainer for a private reporting channel without posting exploit details or credentials publicly. For ordinary bugs, use a sanitized reproduction and diagnostic counters. Never attach `.env`, credential exports, production HIPs or complete `.houlm` histories to a public issue.
