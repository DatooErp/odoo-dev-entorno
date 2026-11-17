# Entorno de desarrollo Odoo (`odoo-dev`)

Compose autocontenido para que un cliente desarrolle sobre Odoo 16 sin tocar
producción. Vive en su propio repositorio, separado de los módulos, para que
no llegue a los servidores de producción ni a la rama de trabajo del cliente.

## Piezas

| Servicio | Qué hace |
|---|---|
| `web` | Odoo 16 con imagen propia (`web/Dockerfile`). Se ejecuta sin root. |
| `db` | PostgreSQL exclusivo del entorno. |
| `panel` | Panel de control (`panel/`): bases de datos, copias, módulos, reinicio. |
| `dockerproxy` | Único acceso a Docker: solo actúa sobre `odoo-dev-web`. |
| `claude` | Claude Code en sesión remota. Escribe solo en `datoov16`. |
| `cloudflared` | Túnel de Cloudflare. El entorno no publica puertos en el servidor. |

## Despliegue

1. Clonar este repositorio en el servidor (por ejemplo en `/home/odoo-dev`).
2. Clonar dentro `datoov16` (en su rama `desarrollo/<entorno>`) y
   `modulos_github16` (solo lectura) desde GitHub, con claves de despliegue.
3. Crear `.env` a partir de `.env.example` y `conf/odoo.conf` a partir de
   `conf/odoo.conf.example`, con las mismas contraseñas en ambos.
4. Crear el túnel (`cloudflared tunnel login`, `tunnel create`,
   `tunnel route dns`) y `cloudflared/config.yml` a partir del ejemplo.
5. `docker compose up -d --build`.

## Panel

Dos perfiles, `cliente` y `datoo`, con contraseña en `.env`. Todas las
bases de datos que se duplican o restauran quedan neutralizadas: sin correo,
sin tareas programadas y sin envíos al SII, Veri*Factu ni TicketBAI.

Pendiente (fase 2): solicitud de paso a producción, revisión y merge con
inicio de sesión de GitHub.

## Claude Code

El contenedor `claude` arranca Remote Control en cuanto hay una cuenta
conectada. La cuenta se conecta desde el panel, en la sección Claude: genera
el enlace de autorización y recoge el código que devuelve Claude.

La cuenta de Claude del entorno debe ser la del cliente, o una dedicada, **sin
conectores con acceso a otros sistemas**: una sesión iniciada con esa cuenta
puede usar sus conectores aunque el contenedor esté aislado.
