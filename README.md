# Hou-lm

A Houdini assistant for studios. Inspect scenes, build procedural networks and run Houdini Python through a dockable chat panel connected to your studio's Open WebUI service.

**Windows · Houdini 20.5.445 · Python 3.11 / PySide2 · Early release**

## Install

1. Download the [Hou-lm plug-in ZIP](https://github.com/Byter3/Hou-lm/releases/download/v0.1.0/Hou-lm-0.1.0-houdini20.5.zip).
2. Close Houdini. Extract **both** `hou-lm.json` and the `hou-lm` folder into `$HOUDINI_USER_PREF_DIR/packages`.
3. Start Houdini and choose **+ → New Pane Tab Type → Hou-lm Assistant**.
4. Open **Connections** (the sliders icon), enter your Open WebUI base URL and user API key, then connect and choose a model.

No extra Python packages or local server are needed. Your model must support tool calls. See [installation](docs/package-install.md) for the folder layout, upgrades and studio distribution.

The release link above is **v0.1.0**. Reference images and viewport capture described below are in the current source and are not included in that published ZIP. To try those features before the next release, [build a package from source](CONTRIBUTING.md#build-the-houdini-plug-in).

## Use it

Describe your task and press **Ctrl+Enter**. Drag nodes into the prompt or type `@` to reference them.

- **Scene** lets the assistant inspect and edit the current Houdini session.
- **Auto-run** allows commands without an Apply popup. Leave it off to review changes first.
- **Attach** adds reference images. **Viewport access** lets the assistant capture the current viewport and compare its work with the reference. This requires a model that accepts images.
- Expand **Tool Activity** or **Inspect commands** to see observations, exact commands and their results.
- **History** resumes conversations; **Log** opens the HIP-associated history location.

Start with:

> Use scene tools to list the immediate children of /obj. Report their names and types. Do not modify anything or force a cook.

Routine inspection reads authored values and existing diagnostics. Native viewport capture may evaluate or cook the current frame. Model-written Python runs inside Houdini with your process permissions; an error can leave partial changes. See [execution and recovery](docs/agent-execution.md).

## Conversations and images

Save the HIP to keep conversations automatically in `.houlm/<HIP filename>.json` beside it. **Save As** carries the accumulated history into the new version. Image assets are stored beside the JSON; keep them together when moving a project. Unsaved scenes retain history only for the current Houdini process.

The full archive stays local; each model request uses a bounded working context. Relevant scene information, active references and requested captures go to your configured service and model provider. Restoring history never replays commands. See [data handling](SECURITY.md).

## Guides

| Guide | Use it for |
| --- | --- |
| [Installation](docs/package-install.md) | Packages folder, updates, uninstall and studio deployment |
| [Using the panel](docs/houdini-panel-quickstart.md) | Controls, login, history and example prompts |
| [Images and viewport capture](docs/visual-reference.md) | Reference-based work and visual checks |
| [Troubleshooting](docs/tool-stream-troubleshooting.md) | Connection and response errors |
| [Contributing](CONTRIBUTING.md) | Source launch, reload, tests and package builds |

[Studio assistant backend](https://github.com/Byter3/studio-assistant-backend) provides optional Open WebUI/LiteLLM deployment examples. [Ae-lm](https://github.com/Byter3/Ae-lm) is the companion After Effects assistant. Each client installs independently and works on its own application session. An existing Open WebUI service can serve both clients.

## License

[MIT](LICENSE). Independent project, not an official SideFX product. Houdini and model/server dependencies have their own licenses.
