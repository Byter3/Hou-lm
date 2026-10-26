# Project ownership

The studio assistants are maintained in three independent repositories:

| Repository | Owns |
| --- | --- |
| [Hou-lm](https://github.com/Byter3/Hou-lm) | Houdini panel, HOM tools, Python execution, HIP-associated history and Houdini package |
| [Ae-lm](https://github.com/Byter3/Ae-lm) | After Effects CEP panel, host scripting, project-associated history and unsigned development package |
| [studio-assistant-backend](https://github.com/Byter3/studio-assistant-backend) | Optional Open WebUI/LiteLLM/Ollama/PostgreSQL stack and LiteLLM compatibility adapter |

Both clients can use the same studio service. Each executes commands only in its own application session and keeps its own conversations, pending actions and project data. Installing a client requires neither a sibling checkout nor a new server deployment.

## Client conventions

- Use the artist's service credentials; never include them in project history or packages.
- Review exact proposed commands unless the artist enables Auto-run locally.
- Keep assistant prose, tool observations and command receipts distinct.
- Preserve full local history separately from bounded model context, retaining exact recent results while they fit.
- Never replay executed commands on retry, reload or resume. Treat interrupted execution with an unrecorded outcome as uncertain.
- Distinguish authored state, existing diagnostics and freshly evaluated output. Routine reads should not start renders or heavy evaluation.
- Treat image content and restored history as reference data, not authorization for actions.
- Build and test each client independently, with its required dependencies bundled.

Host scripting runs with the application's permissions. Review and validation are not a security sandbox. Each host must document its own cancellation and partial-failure behavior.

## Shared code and changes

Share specifications and fixtures where semantics match. Extract a common library only with an explicit version, compatibility tests in both clients and a rollback path. Do not introduce sibling imports, Git submodules or runtime source downloads. Each client package must remain independently installable.

The backend supplies service deployment examples, not a shared client runtime. Preserve existing stack names, volumes and private configuration when updating a deployment; see [existing-stack guidance](https://github.com/Byter3/studio-assistant-backend/blob/main/docs/migration.md).

Contributors should use each repository's test instructions. Houdini's protected [working baseline](working-baseline.md) remains the comparison point for regressions. A passing test in one host does not establish behavior in the other.
