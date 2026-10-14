# Working on Hou-lm

- Read `docs/working-baseline.md` before changing behavior. The `working-2026-10-15` tag is the artist-confirmed reference; preserve it and compare regressions against it.
- Keep documentation/publication cleanup separate from runtime changes. Test one subsystem at a time when changing the panel, model context, transport or gateway.
- Preserve general model-written Python execution. Test fixtures must not become hardcoded recipes for artist requests.
- Retain exact recent tool results while they fit; keep full local JSON archives separate from bounded model context. Never replay executed commands on retry, reload or resume.
- Routine scene checks must not force heavy cooks. Distinguish authored state and existing diagnostics from fresh evaluated output.
- Follow the named native Python Panel and stable-host reload lifecycle. Create Qt objects and run HOM on Houdini's UI thread.
- Use the offline test commands in `CONTRIBUTING.md`. Live model requests, server deployment changes and native scene fixtures are separate operations, not prerequisites for ordinary documentation cleanup.
- Keep credentials, private scene histories, HIPs, diagnostic output and local backups out of commits. Include a useful test prompt with panel behavior changes.
