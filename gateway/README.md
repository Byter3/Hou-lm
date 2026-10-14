# Optional LiteLLM streaming adapter

This directory contains an opt-in compatibility adapter for LiteLLM's **ChatGPT provider**. Hou-lm does not require this adapter for other providers or for a route that already delivers complete tool calls and answers.

The repository contains revision **`streamfix2`**, which repairs missing tool arguments and assistant text when the underlying Responses stream supplies them in final snapshots instead of incremental deltas. It appends matching missing suffixes, preserves call identities, suppresses duplicate snapshots, and rejects conflicting or incomplete streams. It does not invent arguments, rewrite generated Python, or expose private reasoning as an answer.

The adapter has offline regression coverage against specific LiteLLM source versions. **The confirmed working Houdini panel state is not a verification of this gateway image.** The exact deployed server image was not established by the panel acceptance tests. Validate the adapter on the intended server before treating it as part of a working deployment.

For a new installation, start with [server setup](../docs/server-setup.md). For a failing response, use [stream troubleshooting](../docs/tool-stream-troubleshooting.md) to identify the failure before changing an image.

## Compatibility and scope

- The deployment script requires an existing **LiteLLM 1.103.2** container using the verified production entrypoint and `/app` layout.
- The adapter checks package version and source fingerprints and runs a synthetic self-check. Unsupported installations fail explicitly.
- The module also contains guarded support for the older 1.83.14 test environment; the supplied image-building workflow is specifically for 1.103.2.
- Importing the module does not install the adapter. `gateway/bin/litellm` installs it before the ordinary LiteLLM CLI and in supported spawned workers.
- The Houdini panel never imports this adapter. It is not a fix for every `empty_response` or timeout.

The packaged offline probe tests synthetic provider events through the installed stock converter. Running that probe by itself does not install or verify the worker adapter.

## Build without changing the running service

The build uses the **exact local image ID of an existing LiteLLM container**, without pulling a replacement base. It preserves the inherited entrypoint, command and database initialization. Build-time checks use the base image's Python and dependencies with networking disabled.

From the repository root, create the source bundle:

```sh
python scripts/package_gateway.py
```

This creates `dist/houlm-gateway-1.103.2-streamfix2.tar.gz` from an explicit list of adapter source and test files, this guide and the MIT license. It excludes credentials, environment files, preferences and scenes. It is a source archive, not a Docker image export. Links to `../docs/` in this guide refer to the full repository checkout; those additional guides are not included in the compact adapter bundle.

Transfer the archive to the Docker host using your normal file-transfer method. In a host terminal, from the directory containing the archive:

```sh
tar -xzf houlm-gateway-1.103.2-streamfix2.tar.gz
cd houlm-gateway-1.103.2-streamfix2
sh gateway/build-image.sh YOUR_LITELLM_CONTAINER
```

Replace `YOUR_LITELLM_CONTAINER` with the actual container name. The account needs Docker access. Pass the name explicitly; do not rely on the script's development-environment default.

The script builds `houlm-litellm:1.103.2-streamfix2` and prints a `houlm-litellm-base:...` rollback tag for the exact previous image. Save that tag. Building does not restart the running service. If compatibility checks fail, retain the existing deployment and review the reported mismatch.

## Select the image in Portainer

Before updating, preserve the current stack definition, its secret values and a backup of its persistent volumes. In the **existing stack's Editor**, change only the `litellm` service's image:

```yaml
  litellm:
    image: houlm-litellm:1.103.2-streamfix2
```

This is a service excerpt, not a replacement stack. Retain service names, ports, environment variables and volume mounts. Update with **Re-pull image disabled** because this image was built locally on that Docker host.

In LiteLLM startup logs, check for:

```text
Hou-lm stream repair revision: streamfix2 (tool arguments and answer text).
```

Follow it through normal server startup. The marker confirms the launcher revision; successful requests still need to be checked through the actual client route. Keep Open WebUI's existing Chat Completions connection and model aliases.

## Verify the client route

Connect the panel and select the intended model first. From a terminal at the repository root, use the Python environment documented in the [troubleshooting guide](../docs/tool-stream-troubleshooting.md#run-synthetic-checks):

```powershell
& $houdiniPython scripts/diagnose_model_answer.py --repeat 3
```

Each repeat makes at most two synthetic requests: one tool call, then a canned tool result and final answer. It executes no tools or scene operations. Expect `passed: true`, matching synthetic arguments in the first turn, and `answer_matches_synthetic_result: true` in the second. The tool-only probe cannot establish that the final answer path also works.

Then test in Hou-lm:

```text
Use scene tools to list the immediate children of /obj. Report their names and types. Do not modify anything or force a cook.
```

Expect a visible scene-query receipt and an answer based on that result. Offline adapter checks and a gateway success entry alone do not establish that the panel received a usable reply.

## Roll back or upgrade

Set `litellm.image` to the saved `houlm-litellm-base:...` tag and update the same stack with re-pull disabled. Keep the persistent volumes and secret values.

This adapter is tied to verified source fingerprints. For a LiteLLM upgrade, first test the intended stock version. If it handles the covered stream patterns correctly, use it without the adapter. Otherwise review compatibility before building a new image; do not bypass the version or source checks.
