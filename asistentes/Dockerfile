# Contenedor de los asistentes de desarrollo: Claude Code y Codex (ChatGPT).
# Cada usuario del panel ocupa un puesto, que es un usuario Unix con su
# propia carpeta personal (su cuenta y su firma de git). Todos trabajan sobre
# la misma carpeta de código. No hay acceso a Docker ni a los secretos.
FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      git openssh-client tmux ca-certificates curl ripgrep python3 python3-websockets less procps \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code @openai/codex
# Grupo común (dueño de la carpeta de código) y ocho puestos.
RUN groupadd -g 10002 devs && mkdir /homes \
    && for i in 1 2 3 4 5 6 7 8; do \
         useradd -m -d /homes/u$i -u $((10100 + i)) -g devs -s /bin/bash u$i \
         && chmod 700 /homes/u$i; \
       done \
    && git config --system --add safe.directory '*'
COPY --chmod=755 entrypoint.sh terminal.py entorno /usr/local/bin/
COPY --chmod=644 tmux.conf /etc/tmux.conf
WORKDIR /workspace/datoov16
CMD ["entrypoint.sh"]
