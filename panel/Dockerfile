FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client postgresql-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY templates templates
# Terminal web (xterm.js), servido por el propio panel.
ADD --chmod=644 https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js static/xterm.js
ADD --chmod=644 https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css static/xterm.css
ADD --chmod=644 https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js static/xterm-addon-fit.js
# Los volúmenes con nombre heredan el propietario de estos directorios.
RUN useradd -u 10001 -m panel && mkdir -p /backups /data /revision && chown panel:panel /backups /data /revision
USER panel
EXPOSE 8000
CMD ["gunicorn", "-w", "1", "--threads", "48", "--timeout", "0", "-b", "0.0.0.0:8000", "app:app"]
