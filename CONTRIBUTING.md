# Contributing to Hou-lm

Use Python 3.11. The supported interactive target is Windows Houdini 20.5.445 with its bundled PySide2. Keep changes to the panel separate from gateway or deployment changes so regressions can be isolated.

This repository owns the Houdini client. After Effects integration belongs in the separate [Ae-lm project](https://github.com/Byter3/Ae-lm), currently a development scaffold. The server stack, optional gateway adapter and their checks belong in [studio-assistant-backend](https://github.com/Byter3/studio-assistant-backend). Read [project boundaries](docs/project-boundaries.md) before extracting shared client code or adding another application's tools.

## Source layout

| Path | Purpose |
| --- | --- |
| `houdini/python3.11libs/houlm/` | Panel, transport, agent loop, scene tools, history and model context |
| `houdini/python_panels/` | Named native Houdini panel definition |
| `scripts/launch_houdini.py`, `reload_houdini.py`, `install_houdini.py` | Artist launch, safe reload and optional persistent registration |
| `scripts/package_houdini.py`, `packaging/` | Copy-to-packages client ZIP builder and package definition |
| `scripts/diagnose_*.py`, `inspect_webui_route.py` | Opt-in diagnostics; some contact the configured model service |
| `scripts/verify_houdini_*.py` | Native HOM regression fixtures for separate empty `hython` processes |
| `tests/` | Client unit, synthetic protocol and offscreen Qt/loopback tests |

The verification scripts are test fixtures. The panel does not import them as task recipes.

Each client must build and release independently. Do not add sibling-checkout imports, Git submodules, runtime source downloads or an install-time package-manager requirement to connect the projects. A future shared library needs an explicit version and must be bundled in the artist package.

## Run tests

From the repository root, ordinary Python can run the dependency-free portion:

```sh
python -m unittest discover -s tests -q
```

On Windows, use Houdini's plain Python to include the bundled Qt tests:

```powershell
& 'C:\Program Files\Side Effects Software\Houdini 20.5.445\python311\python.exe' -m unittest discover -s tests -q
```

These tests use synthetic data, temporary files and loopback HTTP fixtures. They do not request a real model response or edit an artist's scene. Qt tests skip when PySide2 is unavailable; POSIX-only tests skip on Windows. Set `HFS` if testing another compatible Houdini installation. Do not install a different Qt binding into Houdini to satisfy these tests.

GitHub Actions runs the ordinary Python suite on Windows and Linux. That checks portable logic and fixtures, not native Houdini behavior. Gateway and installed-LiteLLM checks now run in the [backend repository](https://github.com/Byter3/studio-assistant-backend); they are not a dependency of the Houdini client suite.

Native HOM checks require a fresh, empty `hython` process with a Houdini license, for example:

```powershell
& 'C:\Program Files\Side Effects Software\Houdini 20.5.445\bin\hython.exe' scripts/verify_houdini_python.py
```

Never run these fixtures in the artist's interactive session. They create disposable scene content; the history fixture also saves temporary HIPs.

## Build the Houdini plug-in

From the repository root:

```sh
python scripts/package_houdini.py
```

This produces `dist/Hou-lm-0.1.0-houdini20.5.zip`, containing `hou-lm.json` beside the `hou-lm` client folder. Use `--output /path/to/package.zip` for another output file. The builder uses an explicit file allowlist and includes a SHA-256 manifest; it does not import Houdini, execute scene commands or contact a model. Keep generated archives out of commits.

The **Tests** workflow is also configured to build and upload this installable ZIP after both Windows and Linux test jobs pass. Download it from that run's **Artifacts** section; it is uploaded as the ZIP itself, without an outer archive. Check the selected run's result before using its artifact. Configuring the job does not establish a passing hosted run, and it does not create a GitHub release.

The source `docs/package-install.md` is included as the archive's `hou-lm/README.md`, so its links must also work outside a checkout. Keep the `scripts/` and `houdini/` sibling layout intact: the existing launch and reload helpers resolve their paths from it. See [package installation](docs/package-install.md) for artist and studio deployment. The [backend repository](https://github.com/Byter3/studio-assistant-backend) builds its own separate gateway source artifact.

## Preserve behavior

- Keep general Python execution available. Add targeted regression fixtures rather than task-specific solutions to the product.
- Execute HOM and create Qt objects on Houdini's UI thread. Preserve the named-panel and stable-host reload lifecycle.
- Do not turn routine inspection into geometry evaluation or a forced cook.
- Preserve full JSON archives independently of the bounded model context. Keep tool calls paired with results, and test exact recent result retention across messages.
- Never replay completed scene commands when retrying a model response, reloading or restoring history.
- Distinguish observed results, cached observations and model claims in user-facing activity.

The [working baseline](docs/working-baseline.md) records the acceptance prompts to retain. A passing offline suite is evidence about the tested paths, not proof of live model reliability.

## Pull requests and reports

Describe the trigger, resulting behavior and relevant checks. Include a repeatable test prompt for panel changes. Use a small synthetic scene when needed. Do not submit credentials, raw provider responses, private HIP files or `.houlm` archives. Report a sanitized error code and timing counters instead.

Contributions to the project are under the [MIT license](LICENSE).
