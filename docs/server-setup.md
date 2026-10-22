# Shared backend setup

The server stack and optional LiteLLM compatibility adapter now live in the separate [studio-assistant-backend repository](https://github.com/Byter3/studio-assistant-backend), shared by Hou-lm and the planned After Effects client.

- [Server setup](https://github.com/Byter3/studio-assistant-backend/blob/main/docs/server-setup.md): optional Open WebUI, LiteLLM, Ollama and PostgreSQL stack.
- [Migration notes](https://github.com/Byter3/studio-assistant-backend/blob/main/docs/migration.md): source relocation and preservation of existing stack names, volumes and private settings.
- [Gateway compatibility](https://github.com/Byter3/studio-assistant-backend/blob/main/gateway/README.md): opt-in, version-checked stream adapter and its separate validation.

A working studio service can continue serving Hou-lm. This source move does not require restarting, rebuilding or redeploying it. Do not create a second stack merely because the repository directory has a new name: a different Compose project name can create different persistent volumes.

The Houdini client remains independently installable. It connects to the Open WebUI base URL with the artist's Open WebUI user API key; it does not require a backend checkout or a provider/master key on the workstation. Follow the [panel quickstart](houdini-panel-quickstart.md) for login and client use, or the [synthetic client checks](tool-stream-troubleshooting.md#run-synthetic-checks) when testing response delivery.

Historical releases retain the original backend files in their tagged source snapshots. New backend development belongs in the shared repository; the [working Houdini baseline](working-baseline.md) remains preserved.
