# Security and project data

Python runs with Houdini's permissions. Review and Undo groups are not a sandbox; failures can leave partial changes. Auto-run skips review. Viewport access is separate, resets off on panel load and may evaluate the current frame.

Open WebUI credentials use Windows current-user protection. Relevant scene context, active references and requested captures go to the configured service/provider. Local `.houlm` JSON and image assets are unencrypted project data; keep them out of public reports.

Stop cancels pending work, not completed effects. Retry and history restoration never replay completed commands.

Report vulnerabilities privately through GitHub if available, or request a private contact. [Backend security](https://github.com/Byter3/studio-assistant-backend/blob/main/SECURITY.md).
