# Build on the exact image already running on the VM. build-image.sh supplies
# a local tag referring to that image ID; it never pulls a replacement base.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

COPY gateway/litellm_stream_compat.py /opt/houlm/gateway/litellm_stream_compat.py
COPY gateway/check_entrypoint.py /opt/houlm/gateway/check_entrypoint.py
COPY gateway/bin/litellm /opt/houlm/gateway/bin/litellm
COPY tests/test_litellm_stream_compat.py /opt/houlm/tests/test_litellm_stream_compat.py
COPY tests/test_litellm_stream_probe.py /opt/houlm/tests/test_litellm_stream_probe.py
COPY tests/test_gateway_bootstrap.py /opt/houlm/tests/test_gateway_bootstrap.py
COPY scripts/diagnose_litellm_stream.py /opt/houlm/scripts/diagnose_litellm_stream.py
COPY tests/test_gateway_entrypoint.py /opt/houlm/tests/test_gateway_entrypoint.py

RUN --network=none chmod 755 /opt/houlm/gateway/bin/litellm \
    && LITELLM_LOCAL_MODEL_COST_MAP=True python /opt/houlm/gateway/litellm_stream_compat.py --check \
    && LITELLM_LOCAL_MODEL_COST_MAP=True python -m unittest discover -s /opt/houlm/tests -q

# Preserve the parent's database initialization, ENTRYPOINT, CMD and user.
# Its normal `litellm` command now enters the verified bootstrap first.
ENV PATH="/opt/houlm/gateway/bin:${PATH}"

RUN --network=none python /opt/houlm/gateway/check_entrypoint.py
