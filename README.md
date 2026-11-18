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

Un entorno tiene un Odoo y una rama (`desarrollo/<ENV_SLUG>`) comunes, y
varias bases de datos. Cada usuario tiene un **dominio de bases de datos**:
las que Datoo le asigna. Solo ve y opera esas, y solo ve la actividad, las
tareas, las solicitudes y los commits de quienes comparten alguna con él.
Datoo las ve todas.

Lo normal es un entorno por empresa, con una base de datos. También se puede
compartir un entorno entre usuarios de varias empresas con poco uso, cada uno
con su dominio. En ese caso hay que tener presente lo que **no** se separa:

- El Odoo es uno: un reinicio o un módulo roto afecta a todos, y el código de
  cualquier usuario se ejecuta en el mismo proceso que el de los demás.
- El código y la rama son comunes: los asistentes ven todos los módulos.
- El túnel cierra desde fuera el gestor, el selector y el listado de bases de
  datos de Odoo; cada usuario entra a las suyas desde el panel. Aun así, el
  nombre de una base de datos se puede averiguar con una llamada técnica a
  Odoo, y quien tenga un usuario válido en ella podría entrar.

## Panel

Los usuarios se dan de alta desde el panel (perfil Datoo). El primero,
`datoo`, se crea al arrancar con la contraseña de `.env`.

| | Cliente | Datoo |
|---|---|---|
| Reiniciar Odoo, ver el registro | sí | sí |
| Instalar y actualizar módulos en sus bases de datos | sí | sí |
| Restaurar la base de datos desde producción (si está configurado) | sí | sí |
| Sustituir la base de datos por un zip o dump | no | sí |
| Conectar su cuenta de Claude o ChatGPT | sí | sí |
| Solicitar el paso a producción de sus commits | sí | sí, de cualquiera |
| Crear, duplicar, copiar y eliminar bases de datos; archivo de copias | no | sí |
| Usuarios y sus bases de datos; origen de producción de cada una | no | sí |
| Estado de las solicitudes; traer `main` a desarrollo | no | sí |

**Refrescar la base de datos de la empresa.** Datoo sube un zip de Odoo o un
dump de PostgreSQL en Copias (o lo deja en la carpeta `entrada/` del servidor
si pasa de 100 MB) y pulsa «Sustituir». El panel lo restaura neutralizado (sin
correo, sin tareas programadas, sin SII, Veri*Factu ni TicketBAI), sustituye
la base de datos de la empresa y vuelve a aplicar los módulos que desarrollo
tiene distintos de `main`.

**Restaurar desde producción.** Datoo configura en el panel, para cada base
de datos, su origen: `usuario@servidor`, el nombre de la base de datos de
producción y la clave pública de ese servidor. En el servidor de producción se
instala `produccion/entregar-copia` y se autoriza la clave del entorno
(`ssh-panel/origen`, el panel la muestra) limitada a ese programa, que solo
entrega el volcado de las bases de datos permitidas y el commit desplegado.
Con el origen configurado, los usuarios de esa base de datos tienen el botón.
El volcado va sin adjuntos y los módulos se reaplican comparando con el commit
que tiene producción, que puede ir por detrás de `main`.

**Solicitar paso a producción** toma los commits elegidos, los aplica sobre
`main` en una rama `solicitud/<ENV_SLUG>-<usuario>-<n>` y la sube. Si hay
`GITHUB_TOKEN`, abre el pull request. Si un commit depende de otros que no van
en la solicitud, se rechaza con el motivo.

**Qué supone cada solicitud.** El panel calcula, y pone al principio del
informe y en el pull request, si la solicitud crea módulos nuevos, si modifica
módulos existentes y qué módulos ya existentes (de `datoov16`, de
`modulos_github16` o de Odoo) se han instalado en desarrollo y producción no
tiene. Para esto último guarda, en cada restauración desde producción, la
lista de módulos instalados. Si no hay código y solo se ha reutilizado un
módulo existente, la solicitud es de instalación: no lleva rama.

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

## Restauración: la copia nunca es visible sin neutralizar

Odoo ejecuta las tareas programadas de toda base de datos que pueda ver, y una
copia de producción trae las suyas activas, con sus buzones y sus envíos. El
restaurador de Odoo carga los datos y neutraliza después, y en ese intervalo
el gestor de tareas ya está trabajando sobre la copia. Por eso el panel no lo
usa: carga la copia en una base de datos de otro propietario (el rol
`restauracion`), que Odoo no lista, la neutraliza ahí y solo entonces le
cambia el propietario. Si algo falla, la copia se elimina.

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

## Instrucciones de los asistentes

Cada puesto recibe las mismas instrucciones para Claude (`~/.claude/CLAUDE.md`)
y Codex (`~/.codex/AGENTS.md`); están en `INSTRUCCIONES`, en `panel/app.py`,
y se renuevan en cada inicio de sesión en el panel. Piden, entre otras cosas:

- Comprobar antes de desarrollar si lo pedido ya existe, entero o en parte,
  en `datoov16` o `modulos_github16`, y reutilizarlo.
- No modificar nunca `modulos_github16`: se sobrecarga con un módulo `datoo_`.
- Crear los módulos nuevos con la estructura de Datoo (nombre, manifiesto,
  carpetas), con Datoo como autor y el usuario en `contributors`.
- Aplicar y comprobar cada cambio, y solicitar solo a petición del usuario.

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
