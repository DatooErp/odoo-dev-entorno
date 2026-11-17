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
| `asistentes` | Claude Code y Codex en sesión remota, un puesto por usuario. |
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

## Modelo

Un entorno es de **una empresa**: un Odoo, una rama (`desarrollo/<ENV_SLUG>`)
y una base de datos de desarrollo compartidos por varios usuarios de esa
empresa. Cada usuario tiene su asistente (Claude o ChatGPT, con su cuenta) y
hace sus propias solicitudes de paso a producción. Los cambios siguen
funcionando en desarrollo hasta que Datoo los publica.

No se deben mezclar empresas en un mismo entorno: el código de cualquier
usuario se ejecuta en el mismo Odoo y puede leer todas sus bases de datos.

## Panel

Los usuarios se dan de alta desde el panel (perfil Datoo). El primero,
`datoo`, se crea al arrancar con la contraseña de `.env`.

| | Cliente | Datoo |
|---|---|---|
| Reiniciar Odoo, ver el registro | sí | sí |
| Instalar y actualizar módulos en la base de datos de la empresa | sí | sí |
| Sustituir la base de datos por un zip o dump | no | sí |
| Conectar su cuenta de Claude o ChatGPT | sí | sí |
| Solicitar el paso a producción de sus commits | sí | sí, de cualquiera |
| Crear, duplicar, copiar y eliminar bases de datos; archivo de copias | no | sí |
| Usuarios; estado de las solicitudes; traer `main` a desarrollo | no | sí |

**Refrescar la base de datos de la empresa.** Datoo sube un zip de Odoo o un
dump de PostgreSQL en Copias (o lo deja en la carpeta `entrada/` del servidor
si pasa de 100 MB) y pulsa «Sustituir». El panel lo restaura neutralizado (sin
correo, sin tareas programadas, sin SII, Veri*Factu ni TicketBAI), sustituye
la base de datos de la empresa y vuelve a aplicar los módulos que desarrollo
tiene distintos de `main`. Con `SOURCE_SSH` configurado, el cliente puede
además pedir la copia directamente a producción.

**Solicitar paso a producción** toma los commits elegidos, los aplica sobre
`main` en una rama `solicitud/<ENV_SLUG>-<usuario>-<n>` y la sube. Si hay
`GITHUB_TOKEN`, abre el pull request. Si un commit depende de otros que no van
en la solicitud, se rechaza con el motivo.

**Compilación de los frontends.** Si la solicitud toca el `frontend/` de un
módulo React, el panel lo compila (`npm ci && npm run build`) sobre `main` más
la solicitud y añade el resultado en un commit propio. La compilación se
ejecuta en el contenedor de asistentes, no en el panel. Lo que haya compilado
en la carpeta común no viaja: allí conviven los cambios de todos.

**Revisión automática.** Cada solicitud se prueba aislada: sus módulos se
actualizan o instalan en una copia de la base de datos de la empresa cargando
solo `main` más la solicitud. Después un agente (el Claude de un usuario de
Datoo, sin herramientas: solo lee el encargo) redacta un informe con
clasificación, riesgos, revisión del código y veredicto. El informe se ve en
el panel y, si hay token, se publica en el pull request.

**Ventana de terminal.** Cada usuario puede abrir Claude Code o Codex dentro
del panel, sin instalar nada. El panel hace de puente autenticado hasta el
contenedor de asistentes (`asistentes/terminal.py`), que lanza la herramienta
con el usuario del puesto dentro de tmux, de modo que la conversación
sobrevive a cerrar la página.

Las claves que usa el panel (subir ramas, pedir la copia) están en
`ssh-panel/`, que los asistentes no ven.

## Desarrollo ágil: la orden `entorno`

Los asistentes aplican ellos mismos lo que desarrollan, sin acceso a Docker:

| Orden | Qué hace |
|---|---|
| `entorno actualizar <módulos>` | actualiza los módulos en la base de datos de la empresa y reinicia Odoo |
| `entorno instalar <módulos>` | instala módulos y reinicia Odoo |
| `entorno reiniciar` | reinicia Odoo |
| `entorno registro [líneas]` | final del registro de Odoo |
| `entorno estado` | estado de Odoo y de la base de datos |
| `entorno pendientes` | commits del usuario aún sin solicitar |
| `entorno solicitar "<título>" "<descripción>"` | solicita el paso a producción de esos commits |

La orden llama al panel con una credencial propia de cada puesto
(`~/.entorno-token`, que se renueva en cada inicio de sesión en el panel), y
todo queda en el historial de actividad a nombre del usuario. Los frontends
React se compilan en el propio contenedor (`npm run build`) y se ven al
recargar, sin reiniciar Odoo.

## Seguridad: el panel y la carpeta compartida

Los asistentes pueden modificar la carpeta `datoov16`, incluida la
configuración y los ganchos de su `.git`. Por eso **el panel nunca ejecuta git
sobre ella**: tiene su propia copia del repositorio (`repo.git`, en su
volumen), consulta el estado de la carpeta compartida dentro del contenedor de
asistentes y recibe los commits en un fichero de intercambio (`revision/`).
Por el mismo motivo, en el servidor no se debe ejecutar git como root dentro
de `datoov16/`; se hace con un usuario de puesto:

    docker exec -u u1 odoo-dev-asistentes git -C /workspace/datoov16 status

## Asistentes: Claude y ChatGPT

El contenedor `asistentes` lleva Claude Code y Codex (ChatGPT). Cada usuario
del panel ocupa un puesto: un usuario Unix con su carpeta personal, donde
quedan su cuenta, su firma de git y las instrucciones del asistente. Todos
trabajan sobre la misma carpeta `datoov16`, y el control remoto de cada uno
arranca en cuanto conecta su cuenta desde el panel:

- **Claude**: el panel genera el enlace de autorización y recoge el código.
  La sesión se abre desde claude.ai/code o la aplicación de Claude.
- **ChatGPT**: el panel muestra el enlace y el código de dispositivo, y genera
  los códigos de emparejamiento. Se maneja desde la aplicación móvil de
  ChatGPT o la de escritorio de Codex; la web de Codex no sirve para esto.
  El control remoto de Codex es experimental.

Las cuentas del entorno deben ser las del cliente, o dedicadas, **sin
conectores con acceso a otros sistemas**: una sesión iniciada con esa cuenta
puede usar sus conectores aunque el contenedor esté aislado.
