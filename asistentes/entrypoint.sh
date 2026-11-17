#!/bin/sh
# Mantiene arrancado el control remoto de cada asistente, para cada puesto que
# tenga una cuenta conectada. Las cuentas se conectan desde el panel, así que
# puede ocurrir en cualquier momento. Corre como root solo para poder lanzar
# cada proceso con el usuario de su puesto.
echo "Contenedor de asistentes arrancado."
while true; do
  # Puente de las ventanas de terminal del panel.
  if [ -s /run/terminal.secret ] && ! pgrep -f "terminal.py" >/dev/null 2>&1; then
    python3 /usr/local/bin/terminal.py &
    echo "Puente de terminal arrancado."
  fi
  for i in 1 2 3 4 5 6 7 8; do
    u="u$i"; h="/homes/$u"
    como() { setpriv --reuid "$u" --regid devs --init-groups env HOME="$h" USER="$u" "$@"; }
    # Claude Code: Remote Control dentro de tmux, para que el panel lea su
    # estado y responda a sus preguntas iniciales.
    if como test -s "$h/.claude/.credentials.json" && ! como tmux has-session -t rc 2>/dev/null; then
      como sh -c 'cd /workspace/datoov16 && umask 002 && tmux new-session -d -s rc -x 250 -y 50 \
        "claude remote-control --name \"$(cat ~/.nombre-sesion 2>/dev/null || echo Desarrollo)\" --spawn=same-dir"' \
        && echo "Puesto $i: Remote Control de Claude arrancado."
    fi
    # Codex (ChatGPT): su control remoto es un servicio en segundo plano.
    if como test -s "$h/.codex/auth.json" && ! pgrep -u "$u" -f "app-server --remote-control" >/dev/null 2>&1; then
      como sh -c 'cd /workspace/datoov16 && umask 002 && codex remote-control start >/dev/null 2>&1' \
        && echo "Puesto $i: control remoto de Codex arrancado."
    fi
  done
  sleep 30
done
