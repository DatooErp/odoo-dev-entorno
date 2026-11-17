#!/usr/bin/env python3
"""Puente de terminal para el panel.

El panel abre aquí una conexión por cada ventana de terminal y este proceso
lanza Claude Code o Codex con el usuario Unix del puesto indicado, dentro de
una sesión de tmux que sobrevive a los cortes de conexión.

Solo atiende a quien presenta el secreto compartido con el panel, que se lee
de un fichero que únicamente root puede leer: ni los asistentes ni el Odoo de
desarrollo pueden abrir una terminal en nombre de otro usuario.
"""
import asyncio
import fcntl
import hmac
import json
import os
import pty
import pwd
import re
import signal
import struct
import termios

import websockets

SECRET = open("/run/terminal.secret").read().strip()
TOOLS = {"claude": "claude", "codex": "codex"}
WORKDIR = "/workspace/datoov16"


def winsize(fd, cols, rows):
    cols, rows = max(20, min(int(cols), 400)), max(5, min(int(rows), 200))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def spawn(user, tool, cols, rows):
    pw = pwd.getpwnam(user)
    pid, fd = pty.fork()
    if pid == 0:  # proceso hijo: pasa a ser el usuario del puesto, con entorno limpio
        try:
            os.setgid(pw.pw_gid)
            os.initgroups(user, pw.pw_gid)
            os.setuid(pw.pw_uid)
            os.umask(0o002)
            os.chdir(WORKDIR)
            env = {"HOME": pw.pw_dir, "USER": user, "LOGNAME": user, "TERM": "xterm-256color",
                   "LANG": "C.UTF-8", "PATH": "/usr/local/bin:/usr/bin:/bin",
                   "ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
            os.execvpe("tmux", ["tmux", "-u", "new-session", "-A", "-s", "web-" + tool, TOOLS[tool]], env)
        finally:
            os._exit(1)
    winsize(fd, cols, rows)
    return pid, fd


async def handler(ws):
    try:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
        ok = (hmac.compare_digest(str(hello.get("secret", "")), SECRET)
              and re.fullmatch(r"u[1-8]", str(hello.get("user", "")))
              and hello.get("tool") in TOOLS)
    except Exception:
        ok = False
    if not ok:
        await ws.close(4003, "no autorizado")
        return
    pid, fd = spawn(hello["user"], hello["tool"], hello.get("cols", 120), hello.get("rows", 32))
    loop, queue = asyncio.get_running_loop(), asyncio.Queue()

    def on_read():
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        if not data:
            loop.remove_reader(fd)
        queue.put_nowait(data)

    async def pump():
        while True:
            data = await queue.get()
            if not data:
                break
            await ws.send(data)
        await ws.close()

    loop.add_reader(fd, on_read)
    task = asyncio.create_task(pump())
    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                while msg:
                    msg = msg[os.write(fd, msg):]
            else:
                ctl = json.loads(msg)
                if "resize" in ctl:
                    winsize(fd, *ctl["resize"][:2])
    except Exception:
        pass
    finally:
        try:
            loop.remove_reader(fd)
        except Exception:
            pass
        task.cancel()
        try:  # cierra la ventana; la sesión de tmux sigue viva para reconectar
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        os.close(fd)


async def main():
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    async with websockets.serve(handler, "0.0.0.0", 7681, max_size=1 << 20, ping_interval=30):
        await asyncio.Future()


asyncio.run(main())
