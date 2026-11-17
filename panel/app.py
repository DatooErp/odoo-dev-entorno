"""Panel del entorno de desarrollo Odoo (fase 1: operar el entorno).

El panel no tiene acceso a Docker ni a producción. Habla con:
  - Odoo, por la red interna, para crear, duplicar, copiar y restaurar
    bases de datos (servicio de bases de datos con la contraseña maestra).
  - PostgreSQL del entorno, para listar y para la neutralización adicional.
  - Un proxy del socket de Docker que solo permite reiniciar, leer el
    registro y ejecutar comandos en el contenedor de Odoo de desarrollo.
"""
import collections
import datetime
import hmac
import json
import os
import re
import secrets
import struct
import subprocess
import threading
import time

import psycopg2
import requests
from flask import (Blueprint, Flask, abort, flash, jsonify, redirect,
                   render_template, request, send_from_directory, session,
                   url_for)
from requests_toolbelt.multipart.encoder import MultipartEncoder
from werkzeug.utils import secure_filename

env = os.environ.get
PREFIX = env("PANEL_PREFIX", "/dev").rstrip("/")
ODOO_URL = env("ODOO_URL", "http://web:8069")
MASTER = env("ODOO_MASTER_PASSWORD", "")
PG = dict(host=env("PGHOST", "db"), port=env("PGPORT", "5432"),
          user=env("PGUSER", "odoo"), password=env("PGPASSWORD", ""))
DOCKER_URL = env("DOCKER_URL", "http://dockerproxy:2375") + "/v1.41"
ODOO_CONTAINER = env("ODOO_CONTAINER", "odoo-dev-web")
CLAUDE_CONTAINER = env("CLAUDE_CONTAINER", "odoo-dev-claude")
BACKUP_DIR = env("BACKUP_DIR", "/backups")
SOURCE_DIR = env("SOURCE_DIR", "")          # copias de producción (solo lectura)
DATA_DIR = env("DATA_DIR", "/data")
REPOS_DIR = env("REPOS_DIR", "/repos")
PUBLIC_URL = env("PUBLIC_URL", "")
ENV_NAME = env("ENV_NAME", "Entorno de desarrollo")
USERS = {u: p for u, p in (("cliente", env("PANEL_CLIENTE_PASSWORD", "")),
                           ("datoo", env("PANEL_DATOO_PASSWORD", ""))) if p}
ROLE_LABEL = {"cliente": "Cliente", "datoo": "Datoo"}

DB_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,49}$")
MODS_RE = re.compile(r"^[a-z0-9_]+(,[a-z0-9_]+)*$")
CODE_RE = re.compile(r"^[A-Za-z0-9_\-#]{20,400}$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")
BACKUP_EXT = (".zip", ".dump")

app = Flask(__name__, static_folder=None)
app.secret_key = env("PANEL_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_NAME="devpanel",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env("PANEL_INSECURE_COOKIE") != "1",
    SESSION_COOKIE_PATH=PREFIX or "/",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=12),
)
bp = Blueprint("panel", __name__, url_prefix=PREFIX or None)


# ---------------------------------------------------------------- utilidades
def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def client_ip():
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "?"


def audit(user, action, detail="", result=""):
    rec = dict(ts=now(), user=user, action=action, detail=detail, result=result)
    try:
        with open(os.path.join(DATA_DIR, "actividad.jsonl"), "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_audit(limit=150):
    try:
        with open(os.path.join(DATA_DIR, "actividad.jsonl")) as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    return [json.loads(x) for x in reversed(lines) if x.strip()]


# -------------------------------------------------------------------- tareas
JOBS = collections.OrderedDict()
BUSY = threading.Lock()


class Job:
    def __init__(self, title, user):
        self.id = secrets.token_hex(6)
        self.title, self.user = title, user
        self.status, self.lines = "en curso", collections.deque(maxlen=3000)
        self.started, self.ended, self.note = now(), "", ""

    def log(self, text):
        for line in str(text).splitlines() or [""]:
            self.lines.append(line)


def start_job(title, fn):
    """Lanza fn(job) en segundo plano. Solo una operación a la vez."""
    if not BUSY.acquire(blocking=False):
        flash("Hay otra operación en curso. Espera a que termine.", "error")
        return None
    job = Job(title, session["user"])
    JOBS[job.id] = job
    while len(JOBS) > 40:
        JOBS.popitem(last=False)
    audit(job.user, title, result="iniciada")

    def run():
        try:
            fn(job)
            job.status = "correcto"
        except Exception as e:  # el error se muestra en la tarea
            job.log(f"ERROR: {e}")
            job.status = "error"
        finally:
            job.ended = now()
            audit(job.user, title, result=job.status)
            BUSY.release()

    threading.Thread(target=run, daemon=True).start()
    return job


def job_redirect(job, fallback):
    return redirect(url_for("panel.job_view", jid=job.id) if job else fallback)


# -------------------------------------------------------------------- docker
def docker(method, path, **kw):
    kw.setdefault("timeout", 30)
    return requests.request(method, DOCKER_URL + path, **kw)


def demux(raw):
    """Flujo multiplexado de Docker (cabeceras de 8 bytes) a texto."""
    out, i = [], 0
    while i + 8 <= len(raw):
        size = struct.unpack(">I", raw[i + 4:i + 8])[0]
        out.append(raw[i + 8:i + 8 + size])
        i += 8 + size
    return (b"".join(out) if out else raw).decode("utf-8", "replace")


def odoo_state():
    try:
        r = docker("GET", f"/containers/{ODOO_CONTAINER}/json", timeout=8)
        r.raise_for_status()
        st = r.json()["State"]
        return dict(ok=st.get("Running", False), status=st.get("Status", "?"),
                    started=st.get("StartedAt", "")[:19].replace("T", " "))
    except Exception as e:
        return dict(ok=False, status="desconocido", started="", error=str(e))


def odoo_restart(job=None):
    r = docker("POST", f"/containers/{ODOO_CONTAINER}/restart", timeout=120)
    if r.status_code != 204:
        raise RuntimeError(f"No se pudo reiniciar Odoo ({r.status_code}): {r.text[:200]}")
    if job:
        job.log("Odoo reiniciado. Esperando a que responda...")
    for _ in range(60):
        time.sleep(2)
        try:
            if requests.get(ODOO_URL + "/web/login", timeout=5).status_code < 500:
                if job:
                    job.log("Odoo responde.")
                return
        except requests.RequestException:
            pass
    raise RuntimeError("Odoo no responde tras el reinicio. Revisa el registro.")


def odoo_logs(tail=300):
    r = docker("GET", f"/containers/{ODOO_CONTAINER}/logs",
               params=dict(stdout=1, stderr=1, tail=tail), timeout=20)
    r.raise_for_status()
    return demux(r.content)


def odoo_exec(job, cmd):
    """Ejecuta un comando en el contenedor de Odoo y vuelca su salida."""
    r = docker("POST", f"/containers/{ODOO_CONTAINER}/exec", json=dict(
        AttachStdout=True, AttachStderr=True, Tty=True, User="odoo", Cmd=cmd))
    if r.status_code != 201:
        raise RuntimeError(f"No se pudo lanzar el comando ({r.status_code}): {r.text[:200]}")
    eid = r.json()["Id"]
    job.log("$ " + " ".join(cmd))
    with docker("POST", f"/exec/{eid}/start", json=dict(Detach=False, Tty=True),
                stream=True, timeout=(30, 7200)) as s:
        for line in s.iter_lines():
            job.log(line.decode("utf-8", "replace").rstrip("\r"))
    code = docker("GET", f"/exec/{eid}/json").json().get("ExitCode")
    if code != 0:
        raise RuntimeError(f"El comando terminó con código {code}")


# -------------------------------------------------------------------- claude
def cexec(cmd, timeout=40):
    """Ejecuta un comando corto en el contenedor de los asistentes."""
    r = docker("POST", f"/containers/{CLAUDE_CONTAINER}/exec", json=dict(
        AttachStdout=True, AttachStderr=True, Tty=True, Cmd=cmd,
        WorkingDir="/workspace/datoov16"))
    if r.status_code != 201:
        raise RuntimeError(f"El contenedor de Claude no responde ({r.status_code}).")
    eid = r.json()["Id"]
    out = docker("POST", f"/exec/{eid}/start", json=dict(Detach=False, Tty=True), timeout=timeout).text
    code = docker("GET", f"/exec/{eid}/json").json().get("ExitCode")
    return code, ANSI_RE.sub("", out)


def tmux_pane(name):
    """Texto de una ventana de tmux del contenedor, o None si no existe."""
    code, out = cexec(["tmux", "capture-pane", "-p", "-J", "-t", name])
    return out if code == 0 else None


def claude_info():
    """Estado de la cuenta de Claude y de Remote Control en el entorno."""
    info = dict(ok=True, logged=False, email="", org="", plan="", rc="parado", rc_url="", login_url="")
    try:
        _code, out = cexec(["claude", "auth", "status"])
        m = re.search(r"\{.*\}", out, re.S)
        st = json.loads(m.group(0)) if m else {}
        info.update(logged=bool(st.get("loggedIn")), email=st.get("email") or "",
                    org=st.get("orgName") or "", plan=st.get("subscriptionType") or "")
        pane = tmux_pane("login")
        if pane and not info["logged"]:
            m = re.search(r"https://\S+", pane)
            info["login_url"] = m.group(0) if m else ""
        pane = tmux_pane("rc")
        if pane is not None:
            # Preguntas iniciales de Remote Control: confiar en la carpeta del
            # entorno, activarlo y compartir el directorio de trabajo.
            last = pane.strip().splitlines()[-1] if pane.strip() else ""
            answer = ("y" if re.search(r"\[y/N\]\s*$|\(y/n\)\s*$", last)
                      else "1" if re.search(r"Choose \[1/2\].*:\s*$", last) else "")
            if answer:
                cexec(["tmux", "send-keys", "-t", "rc", answer, "Enter"])
                info["rc"] = "configurando"
            elif "Connected" in pane:
                info["rc"] = "conectado"
                m = re.search(r"https://claude\.ai/code\S*", pane)
                info["rc_url"] = m.group(0) if m else "https://claude.ai/code"
            else:
                info["rc"] = "arrancando"
    except Exception as e:
        info.update(ok=False, error=str(e))
    return info



def codex_info():
    """Estado de la cuenta de ChatGPT (Codex) y de su control remoto."""
    info = dict(ok=True, logged=False, rc=False, login_url="", login_code="")
    try:
        _code, out = cexec(["codex", "login", "status"])
        info["logged"] = "Logged in" in out
        if info["logged"]:
            info["rc"] = cexec(["pgrep", "-f", "app-server --remote-control"])[0] == 0
        else:
            pane = tmux_pane("cxlogin") or ""
            url = re.search(r"https://auth\.openai\.com/\S+", pane)
            code = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b", pane)
            if url and code:
                info.update(login_url=url.group(0), login_code=code.group(0))
    except Exception as e:
        info.update(ok=False, error=str(e))
    return info



# ------------------------------------------------------------- bases de datos
def pg(dbname="postgres"):
    return psycopg2.connect(dbname=dbname, connect_timeout=8, **PG)


def list_dbs():
    out = []
    with pg() as c, c.cursor() as cr:
        cr.execute("""SELECT datname, pg_database_size(datname) FROM pg_database
                      WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY 1""")
        rows = cr.fetchall()
    for name, size in rows:
        info = dict(name=name, size=human(size), neutral=None, modules=None)
        try:
            with pg(name) as c, c.cursor() as cr:
                cr.execute("SELECT count(*) FROM ir_module_module WHERE state='installed'")
                info["modules"] = cr.fetchone()[0]
                cr.execute("SELECT value FROM ir_config_parameter WHERE key='database.is_neutralized'")
                row = cr.fetchone()
                info["neutral"] = bool(row and row[0] in ("true", "True", "1"))
        except Exception:
            pass
        out.append(info)
    return out


def db_names():
    with pg() as c, c.cursor() as cr:
        cr.execute("SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres'")
        return {r[0] for r in cr.fetchall()}


def odoo_db(path, fields, timeout=3600):
    """Llama al gestor de bases de datos de Odoo por la red interna.

    Odoo responde con una redirección si todo va bien y con la página del
    gestor (con el mensaje de error) si falla.
    """
    fields = dict(fields, master_pwd=MASTER)
    if any(hasattr(v, "__len__") and not isinstance(v, str) for v in fields.values()):
        enc = MultipartEncoder(fields=fields)
        r = requests.post(ODOO_URL + path, data=enc, timeout=timeout, allow_redirects=False,
                          headers={"Content-Type": enc.content_type})
    else:
        r = requests.post(ODOO_URL + path, data=fields, timeout=timeout, allow_redirects=False)
    if r.status_code in (302, 303):
        return
    m = re.search(r'alert-danger[^>]*>(.*?)</div>', r.text, re.S)
    msg = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else f"respuesta {r.status_code}"
    raise RuntimeError(f"Odoo ha rechazado la operación: {msg}")


# Ajustes que Odoo no neutraliza por sí mismo: (tabla, columna, valor).
EXTRA_NEUTRALIZE = [
    ("res_company", "sii_enabled", "false", "SII desactivado"),
    ("res_company", "verifactu_enabled", "false", "Veri*Factu desactivado"),
    ("res_company", "tbai_enabled", "false", "TicketBAI desactivado"),
    ("ir_mail_server", "active", "false", "Servidores de correo saliente desactivados"),
    ("fetchmail_server", "active", "false", "Servidores de correo entrante desactivados"),
]


def extra_neutralize(job, name):
    """Remata la neutralización de Odoo: SII, correo y URL del entorno."""
    with pg(name) as c, c.cursor() as cr:
        for table, col, value, label in EXTRA_NEUTRALIZE:
            cr.execute("""SELECT 1 FROM information_schema.columns
                          WHERE table_name=%s AND column_name=%s""", (table, col))
            if cr.fetchone():
                cr.execute(f'UPDATE "{table}" SET "{col}" = {value}')
                job.log(f"  {label} ({cr.rowcount} registros).")
        if PUBLIC_URL:
            cr.execute("UPDATE ir_config_parameter SET value=%s WHERE key='web.base.url'", (PUBLIC_URL,))
        cr.execute("SELECT value FROM ir_config_parameter WHERE key='database.is_neutralized'")
        row = cr.fetchone()
        if not (row and row[0] in ("true", "True", "1")):
            raise RuntimeError("Odoo no ha marcado la base de datos como neutralizada. "
                               "Elimínala y revisa la copia antes de usarla.")
    job.log("Base de datos neutralizada: sin correo, sin tareas programadas y sin SII.")


def backup_files(folder):
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    for root, _dirs, files in os.walk(folder):
        if root[len(folder):].count(os.sep) > 2:
            continue
        for f in files:
            if f.endswith(BACKUP_EXT):
                p = os.path.join(root, f)
                st = os.stat(p)
                out.append(dict(rel=os.path.relpath(p, folder), size=human(st.st_size),
                                mtime=datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                                ts=st.st_mtime))
    return sorted(out, key=lambda x: -x["ts"])[:300]


def resolve_backup(origin, rel):
    folder = SOURCE_DIR if origin == "origen" else BACKUP_DIR
    if origin == "origen" and session.get("user") != "datoo":
        abort(403)
    if rel not in {b["rel"] for b in backup_files(folder)}:
        abort(404)
    return os.path.join(folder, rel)


def repos():
    out = []
    if not os.path.isdir(REPOS_DIR):
        return out
    for name in sorted(os.listdir(REPOS_DIR)):
        path = os.path.join(REPOS_DIR, name)
        try:
            git = ["git", "-c", "safe.directory=*", "-C", path]
            branch = subprocess.run(git + ["rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                    text=True, timeout=10, check=True).stdout.strip()
            log = subprocess.run(git + ["log", "-1", "--format=%h|%ad|%s", "--date=format:%Y-%m-%d %H:%M"],
                                 capture_output=True, text=True, timeout=10, check=True).stdout.strip()
            sha, date, subject = log.split("|", 2)
            out.append(dict(name=name, branch=branch, sha=sha, date=date, subject=subject))
        except Exception:
            out.append(dict(name=name, branch="?", sha="?", date="", subject="No se pudo leer el repositorio"))
    return out


# ------------------------------------------------------------------- sesión
FAILS = {}


@bp.before_request
def guard():
    if request.endpoint in ("panel.login", "panel.health"):
        return None
    if session.get("user") not in USERS:
        return redirect(url_for("panel.login"))
    if request.method == "POST" and not hmac.compare_digest(
            request.form.get("_csrf", ""), session.get("csrf", "x")):
        abort(400, "Formulario caducado. Vuelve atrás y recarga la página.")
    return None


@app.context_processor
def inject():
    return dict(env_name=ENV_NAME, role=ROLE_LABEL.get(session.get("user"), ""),
                csrf=session.get("csrf", ""), odoo_url=PUBLIC_URL, busy=BUSY.locked())


@bp.route("/salud")
def health():
    return "ok"


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = client_ip()
        fails, until = FAILS.get(ip, (0, 0))
        if time.time() < until:
            flash("Demasiados intentos. Espera unos minutos.", "error")
            return render_template("login.html"), 429
        user = request.form.get("user", "")
        pw = request.form.get("password", "")
        if user in USERS and hmac.compare_digest(pw.encode(), USERS[user].encode()):
            FAILS.pop(ip, None)
            session.clear()
            session.permanent = True
            session.update(user=user, csrf=secrets.token_hex(16))
            audit(user, "Inicio de sesión", ip)
            return redirect(url_for("panel.home"))
        fails += 1
        FAILS[ip] = (fails, time.time() + 300 if fails >= 5 else 0)
        audit(user[:20] or "?", "Inicio de sesión fallido", ip)
        time.sleep(1)
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@bp.route("/salir", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("panel.login"))


# ------------------------------------------------------------------ páginas
@bp.route("/")
def home():
    try:
        dbs = list_dbs()
    except Exception as e:
        dbs = []
        flash(f"No se puede conectar con PostgreSQL: {e}", "error")
    return render_template("entorno.html", state=odoo_state(), repos=repos(), dbs=dbs,
                           jobs=list(reversed(JOBS.values()))[:5])


@bp.route("/registro")
def logs():
    try:
        text = odoo_logs(int(request.args.get("lineas", 300)))
    except Exception as e:
        text = f"No se pudo leer el registro: {e}"
    return render_template("registro.html", text=text)


@bp.route("/odoo/reiniciar", methods=["POST"])
def restart():
    job = start_job("Reiniciar Odoo", odoo_restart)
    return job_redirect(job, url_for("panel.home"))


@bp.route("/bases")
def bases():
    try:
        dbs = list_dbs()
    except Exception as e:
        dbs = []
        flash(f"No se puede conectar con PostgreSQL: {e}", "error")
    return render_template("bases.html", dbs=dbs)


def form_db(field="name", must_exist=None):
    name = request.form.get(field, "").strip().lower()
    if not DB_RE.match(name):
        flash("Nombre no válido: usa minúsculas, números y guion bajo (2 a 50 caracteres).", "error")
        return None
    exists = name in db_names()
    if must_exist is True and not exists:
        flash(f"La base de datos {name} no existe.", "error")
        return None
    if must_exist is False and exists:
        flash(f"Ya existe una base de datos llamada {name}.", "error")
        return None
    return name


@bp.route("/bases/crear", methods=["POST"])
def db_create():
    name = form_db(must_exist=False)
    if not name:
        return redirect(url_for("panel.bases"))
    password = secrets.token_urlsafe(12)

    def run(job):
        job.log(f"Creando la base de datos vacía {name} (tarda alrededor de un minuto)...")
        odoo_db("/web/database/create", dict(name=name, lang="es_ES", password=password,
                                              login="admin", country_code="es", phone=""))
        job.log("Base de datos creada.")
        job.note = f"Usuario: admin · Contraseña: {password} (solo se muestra aquí; cámbiala al entrar)"

    return job_redirect(start_job(f"Crear base de datos {name}", run), url_for("panel.bases"))


@bp.route("/bases/duplicar", methods=["POST"])
def db_duplicate():
    name, new = form_db(must_exist=True), form_db("new_name", must_exist=False)
    if not name or not new:
        return redirect(url_for("panel.bases"))

    def run(job):
        job.log(f"Duplicando {name} en {new}...")
        odoo_db("/web/database/duplicate", dict(name=name, new_name=new, neutralize_database="on"))
        extra_neutralize(job, new)

    return job_redirect(start_job(f"Duplicar {name} en {new}", run), url_for("panel.bases"))


@bp.route("/bases/eliminar", methods=["POST"])
def db_drop():
    name = form_db(must_exist=True)
    if not name:
        return redirect(url_for("panel.bases"))
    if request.form.get("confirm", "").strip() != name:
        flash("Para eliminar, escribe el nombre exacto de la base de datos.", "error")
        return redirect(url_for("panel.bases"))

    def run(job):
        job.log(f"Eliminando {name}...")
        odoo_db("/web/database/drop", dict(name=name))
        job.log("Base de datos eliminada.")

    return job_redirect(start_job(f"Eliminar base de datos {name}", run), url_for("panel.bases"))


@bp.route("/bases/copia", methods=["POST"])
def db_backup():
    name = form_db(must_exist=True)
    if not name:
        return redirect(url_for("panel.bases"))

    def run(job):
        fname = f"{name}_{datetime.datetime.now():%Y%m%d_%H%M%S}.zip"
        dest = os.path.join(BACKUP_DIR, fname)
        job.log(f"Generando la copia de {name} (base de datos y ficheros)...")
        with requests.post(ODOO_URL + "/web/database/backup", stream=True, timeout=7200,
                           data=dict(master_pwd=MASTER, name=name, backup_format="zip")) as r:
            if "attachment" not in r.headers.get("Content-Disposition", ""):
                raise RuntimeError("Odoo no ha devuelto la copia. Revisa el registro de Odoo.")
            with open(dest + ".tmp", "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        os.replace(dest + ".tmp", dest)
        job.log(f"Copia archivada: {fname} ({human(os.path.getsize(dest))}).")

    return job_redirect(start_job(f"Archivar copia de {name}", run), url_for("panel.bases"))


@bp.route("/bases/modulos", methods=["POST"])
def db_modules():
    name = form_db(must_exist=True)
    mods = request.form.get("modules", "").replace(" ", "").strip(",").lower()
    mode = "-i" if request.form.get("mode") == "instalar" else "-u"
    if not name:
        return redirect(url_for("panel.bases"))
    if not MODS_RE.match(mods):
        flash("Indica los módulos por su nombre técnico, separados por comas (o «all»).", "error")
        return redirect(url_for("panel.bases"))
    do_restart = request.form.get("restart") == "on"

    def run(job):
        odoo_exec(job, ["odoo", "-d", name, mode, mods, "--stop-after-init", "--no-http",
                        "--workers=0", "--max-cron-threads=0", "--log-level=info"])
        job.log("Módulos procesados sin errores.")
        if do_restart:
            odoo_restart(job)

    verb = "Instalar" if mode == "-i" else "Actualizar"
    return job_redirect(start_job(f"{verb} {mods} en {name}", run), url_for("panel.bases"))


@bp.route("/copias")
def backups():
    source = backup_files(SOURCE_DIR) if session["user"] == "datoo" else []
    return render_template("copias.html", archive=backup_files(BACKUP_DIR), source=source,
                           has_source=bool(SOURCE_DIR) and session["user"] == "datoo")


@bp.route("/copias/restaurar", methods=["POST"])
def backup_restore():
    path = resolve_backup(request.form.get("origin", "archivo"), request.form.get("rel", ""))
    name = form_db(must_exist=False)
    if not name:
        return redirect(url_for("panel.backups"))

    def run(job):
        job.log(f"Restaurando {os.path.basename(path)} como {name}, neutralizada...")
        with open(path, "rb") as f:
            odoo_db("/web/database/restore", dict(
                name=name, copy="true", neutralize_database="on",
                backup_file=(os.path.basename(path), f, "application/octet-stream")))
        extra_neutralize(job, name)

    return job_redirect(start_job(f"Restaurar copia en {name}", run), url_for("panel.backups"))


@bp.route("/copias/subir", methods=["POST"])
def backup_upload():
    f = request.files.get("file")
    fname = secure_filename(f.filename) if f and f.filename else ""
    if not fname.endswith(BACKUP_EXT):
        flash("Sube una copia de Odoo en formato .zip o .dump.", "error")
        return redirect(url_for("panel.backups"))
    if os.path.exists(os.path.join(BACKUP_DIR, fname)):
        flash(f"Ya hay una copia archivada llamada {fname}.", "error")
        return redirect(url_for("panel.backups"))
    f.save(os.path.join(BACKUP_DIR, fname))
    audit(session["user"], f"Subir copia {fname}", result="correcto")
    flash(f"Copia {fname} archivada.", "ok")
    return redirect(url_for("panel.backups"))


@bp.route("/copias/descargar")
def backup_download():
    resolve_backup("archivo", request.args.get("rel", ""))
    audit(session["user"], f"Descargar copia {request.args['rel']}", client_ip(), "correcto")
    return send_from_directory(BACKUP_DIR, request.args["rel"], as_attachment=True)


@bp.route("/copias/eliminar", methods=["POST"])
def backup_delete():
    path = resolve_backup("archivo", request.form.get("rel", ""))
    os.remove(path)
    audit(session["user"], f"Eliminar copia {os.path.basename(path)}", result="correcto")
    flash("Copia eliminada.", "ok")
    return redirect(url_for("panel.backups"))


@bp.route("/claude")
def claude_page():
    return render_template("claude.html", c=claude_info())


@bp.route("/claude/entrar", methods=["POST"])
def claude_login():
    try:
        cexec(["tmux", "kill-session", "-t", "login"])
        cexec(["tmux", "new-session", "-d", "-s", "login", "-x", "400", "-y", "50", "claude auth login"])
        for _ in range(10):
            time.sleep(1.5)
            if re.search(r"https://\S+", tmux_pane("login") or ""):
                break
        audit(session["user"], "Iniciar conexión de la cuenta de Claude", client_ip())
    except Exception as e:
        flash(f"No se pudo iniciar la conexión: {e}", "error")
    return redirect(url_for("panel.claude_page"))


@bp.route("/claude/codigo", methods=["POST"])
def claude_code():
    code = request.form.get("code", "").strip()
    if not CODE_RE.match(code):
        flash("El código no tiene el formato esperado. Cópialo completo de la página de Claude.", "error")
        return redirect(url_for("panel.claude_page"))
    try:
        if tmux_pane("login") is None:
            flash("La conexión ha caducado. Pulsa de nuevo «Conectar cuenta».", "error")
            return redirect(url_for("panel.claude_page"))
        cexec(["tmux", "send-keys", "-t", "login", "-l", code])
        cexec(["tmux", "send-keys", "-t", "login", "Enter"])
        logged = False
        for _ in range(12):
            time.sleep(2)
            logged = claude_info()["logged"]
            if logged:
                break
        cexec(["tmux", "kill-session", "-t", "login"])
        audit(session["user"], "Conectar cuenta de Claude", client_ip(), "correcto" if logged else "error")
        if logged:
            flash("Cuenta de Claude conectada. Remote Control arranca en menos de un minuto.", "ok")
        else:
            flash("Claude no ha aceptado el código. Pulsa «Conectar cuenta» y repite el proceso.", "error")
    except Exception as e:
        flash(f"No se pudo completar la conexión: {e}", "error")
    return redirect(url_for("panel.claude_page"))


@bp.route("/claude/salir", methods=["POST"])
def claude_logout():
    try:
        cexec(["claude", "auth", "logout"])
        docker("POST", f"/containers/{CLAUDE_CONTAINER}/restart", timeout=60)
        audit(session["user"], "Desconectar cuenta de Claude", client_ip(), "correcto")
        flash("Cuenta de Claude desconectada del entorno.", "ok")
    except Exception as e:
        flash(f"No se pudo desconectar: {e}", "error")
    return redirect(url_for("panel.claude_page"))


@bp.route("/chatgpt")
def codex_page():
    return render_template("chatgpt.html", c=codex_info(), pair=session.pop("pair", None))


@bp.route("/chatgpt/entrar", methods=["POST"])
def codex_login():
    try:
        cexec(["tmux", "kill-session", "-t", "cxlogin"])
        cexec(["tmux", "new-session", "-d", "-s", "cxlogin", "-x", "200", "-y", "50",
               "codex login --device-auth; sleep 30"])
        for _ in range(10):
            time.sleep(1.5)
            if codex_info()["login_code"]:
                break
        audit(session["user"], "Iniciar conexión de la cuenta de ChatGPT", client_ip())
    except Exception as e:
        flash(f"No se pudo iniciar la conexión: {e}", "error")
    return redirect(url_for("panel.codex_page"))


@bp.route("/chatgpt/emparejar", methods=["POST"])
def codex_pair():
    try:
        _code, out = cexec(["codex", "remote-control", "pair", "--json"])
        m = re.search(r'"manualPairingCode"\s*:\s*"([A-Z0-9-]+)"', out)
        if not m:
            raise RuntimeError("Codex no ha devuelto un código. El control remoto puede no estar arrancado.")
        session["pair"] = m.group(1)
        audit(session["user"], "Generar código de emparejamiento de ChatGPT", client_ip(), "correcto")
    except Exception as e:
        flash(f"No se pudo generar el código: {e}", "error")
    return redirect(url_for("panel.codex_page"))


@bp.route("/chatgpt/salir", methods=["POST"])
def codex_logout():
    try:
        cexec(["codex", "remote-control", "stop"])
        cexec(["codex", "logout"])
        audit(session["user"], "Desconectar cuenta de ChatGPT", client_ip(), "correcto")
        flash("Cuenta de ChatGPT desconectada del entorno.", "ok")
    except Exception as e:
        flash(f"No se pudo desconectar: {e}", "error")
    return redirect(url_for("panel.codex_page"))


@bp.route("/actividad")
def activity():
    return render_template("actividad.html", records=read_audit(), jobs=list(reversed(JOBS.values())))


@bp.route("/tareas/<jid>")
def job_view(jid):
    job = JOBS.get(jid) or abort(404)
    return render_template("tarea.html", job=job)


@bp.route("/tareas/<jid>.json")
def job_json(jid):
    job = JOBS.get(jid) or abort(404)
    return jsonify(status=job.status, ended=job.ended, note=job.note, log="\n".join(job.lines))


app.register_blueprint(bp)
