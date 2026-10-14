#!/bin/sh
# Run on the Docker VM. Builds an image; does not restart or modify containers.
set -eu

container=${1:-ollama-openweb-litellm-1}
result_tag=houlm-litellm:1.103.2-streamfix2
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
context_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)

command -v docker >/dev/null 2>&1 || { printf '%s\n' 'Docker is required on this VM.' >&2; exit 1; }
base_id=$(docker container inspect --format '{{.Image}}' "$container")
case "$base_id" in
    sha256:*) ;;
    *) printf '%s\n' 'Could not identify the running container image.' >&2; exit 1 ;;
esac

entrypoint=$(docker image inspect --format '{{json .Config.Entrypoint}}' "$base_id")
case "$entrypoint" in
    '["docker/prod_entrypoint.sh"]'|'["/app/docker/prod_entrypoint.sh"]') ;;
    *) printf '%s\n' 'The base image uses an unverified entrypoint; no image was built.' >&2; exit 1 ;;
esac
workdir=$(docker image inspect --format '{{.Config.WorkingDir}}' "$base_id")
runtime_entrypoint=$(docker container inspect --format '{{json .Config.Entrypoint}}' "$container")
if [ "$workdir" != /app ] || [ "$runtime_entrypoint" != "$entrypoint" ]; then
    printf '%s\n' 'The current launcher differs from the verified image layout; no image was built.' >&2
    exit 1
fi

# Confirm the user's actual runtime, without reading its environment or keys.
version=$(docker exec "$container" python -c 'import importlib.metadata; print(importlib.metadata.version("litellm"))')
if [ "$version" != 1.103.2 ]; then
    printf '%s\n' 'This deployment bundle expects LiteLLM 1.103.2; no image was built.' >&2
    exit 1
fi

digest=${base_id#sha256:}
base_tag="houlm-litellm-base:$digest"
docker image tag "$base_id" "$base_tag"
docker build --pull=false --build-arg "BASE_IMAGE=$base_tag" \
    --file "$script_dir/Dockerfile" --tag "$result_tag" "$context_dir"

# The build runs checks with the same Python and dependencies as the VM image.
printf '\n%s\n' "Built and checked: $result_tag"
printf '%s\n' "Rollback image: $base_tag"
printf '%s\n' 'In the EXISTING Portainer stack, change only litellm.image to the built tag.'
printf '%s\n' 'Turn OFF re-pull image before updating. Keep all volumes and variables.'
printf '%s\n' 'No running container has been changed by this build.'
