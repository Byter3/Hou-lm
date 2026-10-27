# Repository ownership

| Repository | Owns |
| --- | --- |
| [Hou-lm](https://github.com/Byter3/Hou-lm) | Houdini panel, HOM execution, HIP history and package |
| [Ae-lm](https://github.com/Byter3/Ae-lm) | CEP panel, ExtendScript execution, project history and package |
| [studio-assistant-backend](https://github.com/Byter3/studio-assistant-backend) | Shared service setup, routing and optional LiteLLM adapter |

Clients share model access, not application sessions or execution. Each builds independently without sibling imports or runtime downloads. See the [architecture diagram](../README.md#shared-backend).

Common behavior: local review/Auto-run, exact command receipts, complete local archives, bounded model context and no replay on retry/resume. Host-specific execution and capture remain in each client.

Shared-code extraction requires versioning and tests in both clients. Server deployment changes are separate from client releases. Preserve the Houdini [working baseline](working-baseline.md) when comparing regressions.
