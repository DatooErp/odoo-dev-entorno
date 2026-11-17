#!/bin/sh
# Arranca Remote Control cuando hay una sesión de Claude iniciada.
# La cuenta se conecta desde el panel (sección Claude). Remote Control corre
# dentro de tmux para que el panel pueda leer su estado y responder a sus
# preguntas iniciales.
git config --global safe.directory /workspace/datoov16
until claude auth status 2>/dev/null | grep -q '"loggedIn": true'; do
  echo "Sin cuenta de Claude conectada. Esperando..."
  sleep 20
done
cd /workspace/datoov16
tmux new-session -d -s rc -x 250 -y 50 \
  "claude remote-control --name \"${ENV_NAME:-Entorno de desarrollo}\" --spawn=same-dir"
echo "Remote Control arrancado."
while tmux has-session -t rc 2>/dev/null; do sleep 10; done
echo "Remote Control ha terminado."
