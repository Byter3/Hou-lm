# Project boundaries

Hou-lm is the Houdini assistant. The After Effects assistant is developed separately in [Ae-lm](https://github.com/Byter3/Ae-lm), currently a development scaffold with no installable plug-in. Each application owns its host integration, tests, installation package, releases and acceptance baseline. The third repository, [studio-assistant-backend](https://github.com/Byter3/studio-assistant-backend), owns the shared server example and optional gateway adapter.

The repository separation preserves the confirmed Houdini runtime and its current package paths. The [working baseline](working-baseline.md), existing release tags and downloadable Houdini package remain reference points for Houdini changes.

## Ownership

| Area | Owner and location |
| --- | --- |
| Houdini UI, HOM tools, Python review/execution, HIP history and package | This repository: `houdini/`, client scripts, `packaging/` and Houdini tests |
| After Effects UI, composition/layer tools, host execution, project history and package | Separate [Ae-lm repository](https://github.com/Byter3/Ae-lm); host implementation and first interactive acceptance are separate milestones |
| Optional Open WebUI / LiteLLM / Ollama / PostgreSQL example | [studio-assistant-backend](https://github.com/Byter3/studio-assistant-backend), with its Compose file and server guide |
| Optional LiteLLM compatibility adapter | The backend repository's `gateway/`, source-archive builder and offline checks |
| Cross-application behavior | Conventions below; each client implements and tests them for its host |

Both clients can be configured to use the same studio service and account/model-access system. They keep their own conversations, project data and pending actions. A request from one client must execute only in that client's application session. Sharing a backend does not share an open scene or composition between applications.

The existing server setup is optional. Introducing an After Effects client does not require deploying another stack, changing the accepted Houdini service or installing an inference server on artists' machines. Server changes require their own review and validation. Moving the backend source to another repository does not migrate running containers or data; preserve the existing Compose project/Portainer stack name, volume mappings and private settings if adopting a later backend update. See the [backend migration notes](https://github.com/Byter3/studio-assistant-backend/blob/main/docs/migration.md).

## Shared conventions

These are behavioral requirements for the After Effects implementation. Hou-lm's current data formats and tool names remain unchanged.

- **Connection:** use the configured studio service and the artist's credentials. Keep keys outside project histories and release artifacts. Preserve certificate validation and reject credential-bearing redirects.
- **Tools:** use application-specific tool names and arguments. Parse complete tool requests before dispatch and distinguish read-only observations from changes. The clients do not share a universal scene API.
- **Execution:** review exact proposed source before execution unless the artist explicitly enables Auto-run. Record the source and actual outcome. Describe process-level execution honestly; review and input checks are not a sandbox.
- **Receipts:** distinguish assistant prose from tool activity. Keep exact commands and results inspectable, with activity and command details collapsed by default.
- **History:** retain the full local conversation and executed-command record beside the application project. Keep model context bounded independently, preserving exact recent tool results while they fit. History restoration must not replay commands or grant Auto-run approval.
- **Observation:** label authored state, existing diagnostics and fresh evaluated results accurately. Routine inspection should not launch renders or expensive evaluation. A task that explicitly changes or evaluates content may have broader host effects.
- **Failure:** preserve completed-action evidence after a request fails. A retry must not replay completed actions. Cancellation and undo limits must be stated for the specific host.
- **Installation:** bundle the client and its required runtime code in its own versioned release artifact. Artists should not need a second repository, Git or a package manager to install it.

Project history is application-specific. A common design does not make a `.houlm` file importable into After Effects, and no cross-application history migration is introduced by this separation.

## When to extract a shared library

First prove the After Effects workflow end to end: inspect the active composition, make one reviewed change, display the exact executed command, and save/resume the conversation without replaying the change.

Then compare the two implementations. Extract code only where both clients use the same semantics and can test the same contract. Host-independent protocol handling, context budgeting or history utilities are candidates; host APIs, widgets and execution lifecycle stay with their application. Different implementation languages may make a shared specification and equivalent fixtures more useful than a common library.

Any extraction must have an explicit version, compatibility tests in both clients and a rollback path. Each artist package must bundle the required version so installing or updating one client cannot silently change the other. A shared client-core repository is deferred until these conditions are met. This is separate from the backend repository, which already owns server deployment sources and does not supply a client runtime library.

## Independent validation

Hou-lm continues to use [its offline tests](../CONTRIBUTING.md#run-tests), Houdini packaging checks and [artist acceptance prompts](working-baseline.md#retain-these-regression-prompts). After Effects needs its own native-host acceptance; passing Houdini or protocol tests does not establish After Effects support.

The first After Effects acceptance should use a disposable project and check these behaviors in order:

1. Inspect the active composition and report its dimensions, duration, frame rate and immediate layers without changing anything or rendering.
2. Propose one simple layer change. Reject it once, then request and approve a fresh proposal; verify the exact command receipt and actual result.
3. Save, close and reopen the assistant. Confirm the conversation is restored and the completed change is not repeated.
4. Ask for the previous exact tool result without calling tools again and compare the answer with the retained receipt.

These are planned acceptance criteria, not a claim that an After Effects plug-in is already implemented or tested.

Ae-lm records its implementation requirements in a versioned [draft behavior contract](https://github.com/Byter3/Ae-lm/blob/main/docs/assistant-conventions.md) and [first milestone with test prompts](https://github.com/Byter3/Ae-lm/blob/main/docs/first-milestone.md). Changes there do not silently redefine Hou-lm's runtime or archive format; any future adoption needs an explicit change and validation in this repository.
