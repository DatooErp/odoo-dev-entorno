#!/bin/sh
# Arranca Remote Control cuando hay una sesión de Claude iniciada.
# Sin sesión, espera: el inicio de sesión se hace una vez con
#   docker exec -it odoo-dev-claude claude auth login
git config --global safe.directory /workspace/datoov16
until claude auth status 2>/dev/null | grep -q '"loggedIn": true'; do
  echo "Sin sesión de Claude. Esperando al inicio de sesión..."
  sleep 30
done
cd /workspace/datoov16
exec claude remote-control --name "${ENV_NAME:-Entorno de desarrollo}" --spawn=same-dir
