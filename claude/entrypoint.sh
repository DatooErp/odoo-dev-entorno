#!/bin/sh
# Mantiene arrancado el control remoto de cada asistente que tenga una cuenta
# conectada. Las cuentas se conectan desde el panel (secciones Claude y
# ChatGPT), así que puede ocurrir en cualquier momento.
git config --global safe.directory /workspace/datoov16
cd /workspace/datoov16
echo "Contenedor de desarrollo arrancado."
while true; do
  # Claude Code: Remote Control dentro de tmux, para que el panel lea su
  # estado y responda a sus preguntas iniciales.
  if ! tmux has-session -t rc 2>/dev/null; then
    if claude auth status 2>/dev/null | grep -q '"loggedIn": true'; then
      tmux new-session -d -s rc -x 250 -y 50 \
        "claude remote-control --name \"${ENV_NAME:-Entorno de desarrollo}\" --spawn=same-dir"
      echo "Remote Control de Claude arrancado."
    fi
  fi
  # Codex (ChatGPT): su control remoto es un servicio en segundo plano.
  if ! pgrep -f "app-server --remote-control" >/dev/null 2>&1; then
    if codex login status 2>/dev/null | grep -q "Logged in"; then
      codex remote-control start >/dev/null 2>&1 && echo "Control remoto de Codex arrancado."
    fi
  fi
  sleep 30
done
