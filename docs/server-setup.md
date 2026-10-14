# Optional central server setup

Hou-lm runs inside the artist's Houdini process and connects to Open WebUI. You can use an existing compatible deployment. The included [Compose file](../compose.yaml) is an optional example with Open WebUI, LiteLLM, PostgreSQL and NVIDIA-backed Ollama:

```text
Houdini panel -> Open WebUI -> LiteLLM -> configured local or cloud model
                                          |
                                          +-> Ollama, when used
```

Each panel executes tools in its own Houdini session. The server provides model inference and does not need to open HIP files or run a separate scene-routing service. Choose a model that supports the panel's native tool calls and validate both tool arguments and follow-up answers. No particular cloud provider, account subscription or model alias is required.

## Example prerequisites and limits

- A Linux Docker host with Compose support, or Portainer managing Docker Standalone.
- Working NVIDIA Container Toolkit/GPU access for the example's Ollama service, which reserves one NVIDIA GPU. A cloud-only or CPU deployment needs an appropriately adapted stack.
- Persistent storage for accounts, model files and gateway configuration.
- A workstation that can reach Open WebUI and a model configured for that user.

The example uses floating tags: `ollama/ollama:latest`, Open WebUI `main`, LiteLLM `main-stable` and PostgreSQL `16`. **It is not a pinned, reproducible release baseline.** Validate and record exact versions/image digests for your deployment. The confirmed working panel state does not certify the current images behind those tags, and the optional adapter in `gateway/` has narrower version requirements.

The example publishes HTTP ports 8088 and 4000 and does not configure TLS, a reverse proxy or network access rules. Configure those for the intended environment before exposing it beyond trusted access.

## Deploy a new example stack

Copy [.env.example](../.env.example) to a private `.env` on the Docker host and supply distinct values:

| Variable | Purpose |
| --- | --- |
| `LITELLM_MASTER_KEY` | Gateway administration key; use `sk-` followed by a long random value. |
| `LITELLM_SALT_KEY` | Protects stored provider credentials; retain it across updates. |
| `LITELLM_DB_PASSWORD` | PostgreSQL password; use letters and numbers because the example interpolates it into a connection URL. |

For example, `openssl rand -hex 32` generates a value; run it separately for each secret. Keep populated environment files and credentials outside Git.

From the repository root on the Docker host:

```sh
docker compose up -d
docker compose ps
```

For Portainer, create a stack from `compose.yaml`, enter the same three variables in its environment-variable settings, and deploy. The required-variable expressions reject empty values. Register model credentials in LiteLLM after deployment; they are not needed merely to start these services.

For an existing installation, compare its stack definition first. Preserve its stack/project name, secret values and volume mappings, and back up its data before adopting changes. A different stack name can create new volumes rather than attach existing ones.

## Services and persistent data

| Service | Example address | Purpose |
| --- | --- | --- |
| Open WebUI | `http://YOUR_SERVER:8088` | Artist authentication, model selection and completion API |
| LiteLLM | `http://YOUR_SERVER:4000/ui` | Gateway administration and model routing |
| Ollama | `http://ollama:11434` inside the stack | Local inference; no host port published |
| PostgreSQL | `litellm-db:5432` inside the stack | LiteLLM database; no host port published |

| Volume | Container path | Contents |
| --- | --- | --- |
| `ollama_data` | `/root/.ollama` | Ollama models and state |
| `open-webui-data` | `/app/backend/data` | Open WebUI accounts, chats and saved configuration |
| `litellm_db_data` | `/var/lib/postgresql/data` | LiteLLM models, keys and configuration |
| `litellm_chatgpt_data` | `/var/lib/litellm/chatgpt` | Optional ChatGPT-provider authentication files, if that provider is used |

Volume names normally acquire the Compose project/Portainer stack prefix. Keep the salt key with database backups. Changing the database password environment variable alone does not rotate an already initialized database's password; the [PostgreSQL image initializes these values only for an empty data directory](https://hub.docker.com/_/postgres). Use the database's rotation procedure when needed.

## Register models and connect Open WebUI

1. Open the LiteLLM admin UI and sign in using the authentication configured for that installation. For a first deployment, follow the [LiteLLM admin UI guide](https://docs.litellm.ai/docs/proxy/ui).
2. Register the local or cloud models you want to offer, with their provider credentials and a public alias such as `houdini-model`.
3. Create a LiteLLM virtual key restricted to those model aliases. Use the [virtual-key configuration](https://docs.litellm.ai/docs/proxy/virtual_keys) appropriate to your deployment; keep the master key for administration.
4. In Open WebUI's admin connection settings, configure an **OpenAI-compatible / Chat Completions** connection with URL `http://litellm:4000/v1` and that virtual key. See [Open WebUI's connection guide](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/).
5. Select the alias in Open WebUI and send a short test message. Confirm it appears in the intended gateway's request records.

Open WebUI persists connection settings, so an existing saved configuration may take precedence over Compose defaults. Verify the saved URL and key in its UI when updating an existing deployment; see its [environment configuration reference](https://docs.openwebui.com/reference/env-configuration/).

The example disables Open WebUI's native Ollama connection and user direct connections to route inference through LiteLLM. It also leaves `OPENAI_API_KEY` empty intentionally; configure the restricted key in the UI. Review any pre-existing direct connections if central routing is required.

The Houdini panel then uses **the Open WebUI URL and an Open WebUI user API key**, not the LiteLLM master key, virtual key or upstream provider credential. Enable API-key access for the user as needed using [Open WebUI's API-key settings](https://docs.openwebui.com/features/authentication-access/api-keys/), then follow the [panel quickstart](houdini-panel-quickstart.md).

### Optional Ollama model

On the Docker host, list installed models:

```sh
docker compose exec ollama ollama ls
```

Download a chosen model with `docker compose exec ollama ollama pull MODEL_TAG`, replacing `MODEL_TAG` with its full actual tag. In Portainer, the equivalent `ollama ls` or `ollama pull MODEL_TAG` command runs in the Ollama container console. The supplied Open WebUI defaults disable its native Ollama management controls.

Register the model in LiteLLM with:

| Setting | Example |
| --- | --- |
| Upstream model | `ollama_chat/MODEL_TAG` |
| API base | `http://ollama:11434` |
| Public alias | `houdini-local` |
| API key | Empty for this example's internal Ollama service |

Keep the exact tag from `ollama ls`. LiteLLM's [Ollama provider guide](https://docs.litellm.ai/docs/providers/ollama) documents the `ollama_chat/` route and tool-call configuration. Model and tool-call support still need testing; a successful plain text reply is not sufficient.

Other providers can use their own supported authentication and configuration in LiteLLM. The optional token volume in this example does not require use of the ChatGPT provider, and no provider sign-in procedure is part of the panel installation.

## Validate before scene edits

Use the [synthetic diagnostics](tool-stream-troubleshooting.md#run-synthetic-checks) to check tool argument delivery and a final answer after a canned tool result. These make model requests but execute no Houdini commands.

Then, in a new panel conversation with **Scene** enabled, send:

```text
Use scene tools to list the immediate children of /obj. Report their names and types. Do not modify anything or force a cook.
```

Expect scene-query activity followed by an answer. Also verify model access restrictions and persistence after a planned restart. General studio policy enforcement and concurrent-user load testing are not established by a successful single-panel test.

If a supported LiteLLM ChatGPT-provider version demonstrably loses completed tool arguments or answer text, review the [optional adapter](../gateway/README.md). Do not rebuild a working gateway simply to install or update the Houdini panel.
