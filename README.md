# Hou-lm

A dockable Houdini assistant that can inspect your scene, write and run Houdini Python, and help build or repair node networks through conversation.

The model runs through your configured Open WebUI service. Scene tools execute inside the artist's current Houdini session. There is no separate scene server to install.

**Early release · Windows · Houdini 20.5.445 / Python 3.11 / PySide2**

## What it does

- Inspects node networks, authored parameters, connections and available diagnostics on demand.
- Runs model-generated Python with the native `hou` module, alongside typed inspection and editing shortcuts. Python is general-purpose; there is no library of task-specific scene recipes.
- Reviews proposed changes before execution, or executes them with the optional **Auto-run** setting.
- Shows assistant replies separately from collapsed activity and exact command receipts.
- Supports dragged node references, `@` path completion and links back to nodes.
- Saves conversations, commands and scene observations as JSON beside the HIP, with history carried forward on **Save As**.
- Keeps a bounded model working context separate from the complete local conversation archive.
- Remembers login through Windows-protected credential storage.

Routine inspection reads authored state and available last-cook diagnostics without forcing a cook. A task that executes Python, changes nodes or evaluates geometry can still trigger Houdini cooking. General Python runs with the artist process's permissions; it is not a sandbox, and an exception can leave partial changes.

## Start in Houdini

You need a working Open WebUI account, its user API key, and a model that supports tool calls. The panel uses Houdini's bundled Python and Qt; it needs no `pip install` in Houdini.

1. Clone or download this repository to a permanent local folder.
2. Open **Windows → Python Shell** in Houdini. Replace the example path and run:

   ```python
   import runpy; _ = runpy.run_path(r"C:\path\to\Hou-lm\scripts\launch_houdini.py", run_name="__main__")
   ```

3. In the pane where you want the assistant, choose **+ → New Pane Tab Type → Hou-lm Assistant**. Use that named entry instead of the generic Python Panel.
4. Connect using your Open WebUI base URL and user API key, then choose a model.

For persistent installation, reload instructions, history and controls, see the [panel quickstart](docs/houdini-panel-quickstart.md).

Start with a read-only prompt:

> Use scene tools to list the immediate children of /obj. Report their names and types. Do not modify anything or force a cook.

## How it fits together

```text
Houdini panel → Open WebUI → configured model connection
                            └─ optionally LiteLLM → local or cloud model

Model tool requests → artist's Houdini session → results returned to the model
```

Each panel acts on its own Houdini process. Provider availability and user access are managed by the central service. If you already have a working service, use it; the example Docker stack and optional gateway adapter are separate deployment choices.

| Guide | Contents |
| --- | --- |
| [Panel quickstart](docs/houdini-panel-quickstart.md) | Launch, install, login, controls, save/resume and test prompts |
| [Scene integration](docs/houdini-connection.md) | Architecture, scene scope and typed tools |
| [Python execution](docs/agent-execution.md) | General Python, review, cancellation and partial failures |
| [Server setup](docs/server-setup.md) | Optional Open WebUI / LiteLLM / Ollama / PostgreSQL example |
| [Gateway compatibility](gateway/README.md) | Optional version-checked LiteLLM stream adapter |
| [Troubleshooting](docs/tool-stream-troubleshooting.md) | Response diagnostics and bounded delivery checks |
| [Working baseline](docs/working-baseline.md) | Confirmed behavior, regression checks and validation limits |
| [Contributing](CONTRIBUTING.md) | Source layout and local tests |

## Status

The working panel baseline was confirmed by an artist on **15 October 2026**: live scene inspection completed, and a subsequent message recalled the exact earlier tool result without running another tool. Earlier artist tests also exercised node creation and ramp editing. This public preparation preserves that runtime unchanged.

This is an early release, not a guarantee that a model will correctly solve every Houdini task. Other Houdini versions and platforms, complex production simulations, and concurrent multi-user operation have not been established by these checks. See the [validation record](docs/working-baseline.md).

HIP files, `.houlm` histories, credentials, diagnostic output and local backups are excluded from the repository. The sidecar can contain scene text and executed Python; keep it with the project rather than attaching it to a public issue. See [data and execution boundaries](SECURITY.md).

## License

[MIT](LICENSE), copyright © 2026 Byter3. Houdini and the separately installed model/server dependencies have their own licenses. This is an independent project, not an official SideFX product.
