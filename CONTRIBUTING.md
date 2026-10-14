# Contributing to Hou-lm

Use Python 3.11. The supported interactive target is Windows Houdini 20.5.445 with its bundled PySide2. Keep changes to the panel separate from gateway or deployment changes so regressions can be isolated.

## Source layout

| Path | Purpose |
| --- | --- |
| `houdini/python3.11libs/houlm/` | Panel, transport, agent loop, scene tools, history and model context |
| `houdini/python_panels/` | Named native Houdini panel definition |
| `scripts/launch_houdini.py`, `reload_houdini.py`, `install_houdini.py` | Artist launch, safe reload and optional persistent registration |
| `scripts/diagnose_*.py`, `inspect_webui_route.py` | Opt-in diagnostics; some contact the configured model service |
| `scripts/verify_houdini_*.py` | Native HOM regression fixtures for separate empty `hython` processes |
| `gateway/` | Optional server-side stream compatibility adapter |
| `tests/` | Unit, synthetic provider and offscreen Qt/loopback tests |

The verification scripts are test fixtures. The panel does not import them as task recipes.

## Run tests

From the repository root, ordinary Python can run the dependency-free portion:

```sh
python -m unittest discover -s tests -q
```

On Windows, use Houdini's plain Python to include the bundled Qt tests:

```powershell
& 'C:\Program Files\Side Effects Software\Houdini 20.5.445\python311\python.exe' -m unittest discover -s tests -q
```

These tests use synthetic data, temporary files and loopback HTTP fixtures. They do not request a real model response or edit an artist's scene. Qt tests skip when PySide2 is unavailable. LiteLLM integration tests skip without that optional package; POSIX-only tests skip on Windows. Set `HFS` if testing another compatible Houdini installation. Do not install a different Qt binding into Houdini to satisfy these tests.

GitHub Actions runs the ordinary Python suite on Windows and Linux. That checks portable logic and fixtures, not native Houdini behavior. The [gateway guide](gateway/README.md) describes its separate dependency-specific checks.

Native HOM checks require a fresh, empty `hython` process with a Houdini license, for example:

```powershell
& 'C:\Program Files\Side Effects Software\Houdini 20.5.445\bin\hython.exe' scripts/verify_houdini_python.py
```

Never run these fixtures in the artist's interactive session. They create disposable scene content; the history fixture also saves temporary HIPs.

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
