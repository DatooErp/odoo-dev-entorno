# Imagen propia del entorno de desarrollo: Odoo 16 oficial más las
# dependencias Python de los módulos de Datoo.
FROM odoo:16.0
USER root
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
USER odoo
