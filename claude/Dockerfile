# Contenedor de desarrollo con Claude Code. No tiene acceso a Docker ni a
# los secretos del entorno: solo ve el código y habla con Odoo por la red.
FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      git openssh-client tmux ca-certificates curl ripgrep python3 less procps \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code
RUN useradd -m -u 10002 -s /bin/bash dev
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh
USER dev
WORKDIR /workspace/datoov16
CMD ["entrypoint.sh"]
