FROM node:26-slim

ENV CI=true \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/home/node/.local/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl git python3 ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Install claude-code — auth via ~/.claude volume mount at runtime
RUN npm install -g @anthropic-ai/claude-code

# Install ubs and toon as the node user (glibc image so arm64 binaries work)
USER node
ENV HOME=/home/node
RUN curl -fsSL "https://raw.githubusercontent.com/Dicklesworthstone/ultimate_bug_scanner/main/install.sh" \
    | bash -s -- --easy-mode || true
RUN curl -fsSL "https://raw.githubusercontent.com/Dicklesworthstone/toon_rust/main/install.sh" \
    | bash && \
    ln -sf /home/node/.local/bin/toon /home/node/.local/bin/tru

WORKDIR /workspace
