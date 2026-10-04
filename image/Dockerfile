# SPDX-License-Identifier: BSD-3-Clause
# The image an agent runs in under `[isolation] backend = "docker"`: pi and what a
# Python repository's tests need. Build it, then extend it with your repository's
# toolchain - the tools in the image are the agent's tools, so the scenario names
# the image it measured with:
#
#   docker build -t trysquare-agent image/
#
# Everything is pinned. The base by digest, Node by version and checksum, pi by
# version: an image rebuilt next year must give the agent the same tools, or the
# matrix it measures is not the same experiment.
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

ARG NODE_VERSION=22.21.1
ARG PI_VERSION=1.0.2

# fd-find is what pi searches files with; without it pi downloads its own copy on
# first use, which a container starting fresh for every run would do every time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils git python3 fd-find \
    && rm -rf /var/lib/apt/lists/*

# From nodejs.org rather than the distribution, whose Node is older than pi needs.
RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
        amd64) arch=x64 ;; \
        arm64) arch=arm64 ;; \
        *) echo "unsupported architecture" >&2; exit 1 ;; \
    esac; \
    tarball="node-v${NODE_VERSION}-linux-${arch}.tar.xz"; \
    cd /tmp; \
    curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/${tarball}"; \
    curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt"; \
    grep " ${tarball}\$" SHASUMS256.txt | sha256sum -c -; \
    mkdir -p /opt/node; \
    tar -xJf "${tarball}" -C /opt/node --strip-components=1; \
    rm -f "${tarball}" SHASUMS256.txt

ENV PATH="/opt/node/bin:${PATH}"

RUN npm install -g "@earendil-works/pi-coding-agent@${PI_VERSION}" && pi --version

# A run calls its provider and nothing else: no update check, no install telemetry.
ENV PI_SKIP_VERSION_CHECK=1 \
    PI_TELEMETRY=0
