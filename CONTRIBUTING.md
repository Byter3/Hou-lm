# Development

Houdini client only. [Ae-lm](https://github.com/Byter3/Ae-lm) owns AE integration; [studio-assistant-backend](https://github.com/Byter3/studio-assistant-backend) owns deployment and routing. Preserve the [working baseline](docs/working-baseline.md).

| Path | Purpose |
| --- | --- |
| `houdini/python3.11libs/houlm/` | Panel, tools, transport, context and history |
| `houdini/python_panels/` | Named panel definition |
| `scripts/`, `packaging/` | Launch/reload, diagnostics and package builder |
| `tests/` | Offline fixtures and native test helpers |

## Run tests

```sh
python -m unittest discover -s tests -q
```

For bundled Qt coverage on Windows:

```powershell
& 'C:/Program Files/Side Effects Software/Houdini 20.5.445/python311/python.exe' -m unittest discover -s tests -q
```

Offline tests use synthetic data and loopback fixtures. Missing Qt/platform dependencies skip. Native `scripts/verify_houdini_*.py` fixtures require a separate empty `hython` session; do not run them in an artist's project.

## Build the Houdini plug-in

```sh
python scripts/package_houdini.py
```

Output: `dist/Hou-lm-0.1.0-houdini20.5.zip`. Use `--output PATH` for a new filename; existing archives are not overwritten. The allowlisted ZIP includes checksums. CI builds it after the test jobs pass.

`docs/package-install.md` becomes the packaged README, so keep its links valid outside a checkout. [Release checklist](docs/publishing.md).

## Launch a source checkout

In Houdini's Python Shell:

```python
import runpy; _ = runpy.run_path(r"C:/path/to/Hou-lm/scripts/launch_houdini.py", run_name="__main__")
```

Open **+ → New Pane Tab Type → Hou-lm Assistant**. The launcher registers/focuses the named interface; it does not reload modules.

## Load panel updates

With the named pane open, run in Houdini:

```python
import runpy; _ = runpy.run_path(r"C:/path/to/Hou-lm/scripts/reload_houdini.py", run_name="__main__")
```

Installed packages can resolve the helper through `$HOULM_ROOT`. Child-panel reload preserves the stable host, login and history. Changes to `panel_host.py` or the panel definition require restart.

## Register a source checkout persistently

Run `scripts/install_houdini.py` through the same `runpy` pattern inside Houdini. It creates a package pointing to the checkout; keep only one active registration.

## Change scope

Keep runtime, documentation and deployment changes separate. Run Qt/HOM on the UI thread; preserve the stable-host lifecycle. Keep full archives separate from bounded model context, retain exact recent results while they fit, and never replay commands on retry/resume. Routine reads must not force heavy cooks.

Include a useful [test prompt](docs/houdini-panel-quickstart.md#test-auto-run-command-receipts-and-resume) for behavior changes. Use synthetic fixtures, not private scene material. [MIT](LICENSE).
