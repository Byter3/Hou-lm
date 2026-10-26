# Connect to the studio service

Hou-lm connects to an Open WebUI base URL using the artist's Open WebUI user API key. The selected model must support tool calls; reference images and viewport comparisons also require image input.

If your studio already has a working service, use it. No backend checkout or provider/master key is needed on the artist workstation. Follow [Connect](houdini-panel-quickstart.md#connect) to configure the panel.

Studio administrators can use the separate [studio-assistant-backend repository](https://github.com/Byter3/studio-assistant-backend):

- [Server setup](https://github.com/Byter3/studio-assistant-backend/blob/main/docs/server-setup.md): optional Open WebUI, LiteLLM, Ollama and PostgreSQL deployment.
- [Existing stacks](https://github.com/Byter3/studio-assistant-backend/blob/main/docs/migration.md): preserve stack names, volumes and private configuration during updates.
- [Gateway adapter](https://github.com/Byter3/studio-assistant-backend/blob/main/gateway/README.md): optional LiteLLM stream compatibility support.

Client installation does not require rebuilding or restarting the server. For failed model requests, see [response troubleshooting](tool-stream-troubleshooting.md).
