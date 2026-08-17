FROM node:26-slim

ARG TARGETARCH
ARG UBS_VERSION=5.3.8
ARG UBS_SHA256=4a7d7b8575a06ffa7cc017e048dd01069deed11499f9f35386a464225087b929
ARG AST_GREP_VERSION=0.40.1
ARG TOON_VERSION=0.2.3

ENV CI=true \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/home/node/.local/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl git python3 ca-certificates xz-utils jq ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Install claude-code — auth via ~/.claude volume mount at runtime
RUN npm install -g @anthropic-ai/claude-code

# Pin UBS and its AST engine. The UBS easy-mode installer must not be used here:
# when npm cannot write to the global prefix, its binary fallback can select the
# small `sg` launcher from ast-grep's archive and rename it to `ast-grep`. That
# launcher then recursively execs itself until the container exhausts its PIDs.
RUN set -eux; \
    curl -fsSLo /usr/local/bin/ubs \
      "https://github.com/Dicklesworthstone/ultimate_bug_scanner/releases/download/v${UBS_VERSION}/ubs"; \
    echo "${UBS_SHA256}  /usr/local/bin/ubs" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/ubs; \
    npm install -g "@ast-grep/cli@${AST_GREP_VERSION}"; \
    ubs --version | grep -F "v${UBS_VERSION}"; \
    ast-grep --version | grep -F "ast-grep ${AST_GREP_VERSION}"

# Install toon from a pinned, checksummed release archive.
RUN set -eux; \
    asset="toon-linux-${TARGETARCH}.tar.xz"; \
    base="https://github.com/Dicklesworthstone/toon_rust/releases/download/v${TOON_VERSION}"; \
    curl -fsSLo "/tmp/${asset}" "${base}/${asset}"; \
    curl -fsSLo "/tmp/${asset}.sha256" "${base}/${asset}.sha256"; \
    cd /tmp; \
    sha256sum -c "${asset}.sha256"; \
    tar -xJf "${asset}" -C /usr/local/bin; \
    chmod 0755 /usr/local/bin/toon; \
    ln -sf /usr/local/bin/toon /usr/local/bin/tru; \
    toon --version

USER node
ENV HOME=/home/node
RUN ubs doctor --fix

WORKDIR /workspace
