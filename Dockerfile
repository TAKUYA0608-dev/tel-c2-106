# syntax=docker/dockerfile:1
# Build from the repo root: docker build --secret id=agentcore_deploy_token,env=AGENTCORE_DEPLOY_TOKEN \
#   --build-arg AGENTCORE_WHEEL_SPEC="agenticstar-agentcore[marketplace,openai,platform-rag,platform-memory,platform-db,platform-storage-azure]==1.0.2" \
#   --build-arg AGENTCORE_INDEX_URL="<package registry host>/api/v4/projects/<id>/packages/pypi/simple/" \
#   -t <registry>/tel-c2-106:<tag> .
#
# Marketplace one-shot Pod: no server, no port. cli.py compiles the agent,
# runs exactly one Marketplace execution via shared.bootstrap.marketplace_app,
# then exits — the platform tracks Pod liveness through PodRuntime/execution_pods,
# not an HTTP healthcheck, so this image has no EXPOSE/HEALTHCHECK
# (mirrors agentcore's own agents/base/chat_agent/Dockerfile).
#
# This is the scaffold's canonical Marketplace image — same wheel spec / registry
# pins as deploy/local-stg.yml and the CI deploy-stg job, but installed at build
# time (not container start) because the Marketplace unit of execution is an
# immutable, pre-pushed image, not an ephemeral runtime.

FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY config/ config/
COPY src/ src/
COPY cli.py .

# AGENTCORE_DEPLOY_TOKEN is a BuildKit secret mount (--secret), never a
# --build-arg or ENV — either of those would leave the token readable in the
# image's layer history (`docker history`) forever. AGENTCORE_WHEEL_SPEC and
# AGENTCORE_INDEX_URL are ordinary build-args (CoE bumps/sets them the same way
# it bumps the matching CI variables) since neither carries a credential on its
# own — the package registry host is never hardcoded here so this file stays
# safe to publish; the CoE-internal host value is supplied at build time.
ARG AGENTCORE_WHEEL_SPEC=agenticstar-agentcore[marketplace,openai,platform-rag,platform-memory,platform-db,platform-storage-azure]==1.0.2
ARG AGENTCORE_INDEX_URL
RUN --mount=type=secret,id=agentcore_deploy_token \
    pip install --no-cache-dir uv && \
    uv pip install --system "${AGENTCORE_WHEEL_SPEC}" -e . \
      --extra-index-url "https://agentcore-pkg-read:$(cat /run/secrets/agentcore_deploy_token)@${AGENTCORE_INDEX_URL}"

ENV PYTHONPATH=/app

# AGENTIC STAR のサンドボックスは runAsNonRoot=true を強制する（runAsUser の指定は無い）。
# そのため非rootユーザーで実行する必要がある。USER はユーザー名ではなく数値UIDで指定すること
# （名前指定だと kubelet が UID を解決できず CreateContainerConfigError で起動失敗する）。
RUN useradd --uid 1000 --create-home appuser && chown -R 1000:1000 /app
USER 1000

CMD ["python", "cli.py"]
