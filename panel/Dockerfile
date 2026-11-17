FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY templates templates
# Los volúmenes con nombre heredan el propietario de estos directorios.
RUN useradd -u 10001 -m panel && mkdir -p /backups /data && chown panel:panel /backups /data
USER panel
EXPOSE 8000
CMD ["gunicorn", "-w", "1", "--threads", "8", "--timeout", "0", "-b", "0.0.0.0:8000", "app:app"]
