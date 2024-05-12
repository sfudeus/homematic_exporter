FROM python:3.12-slim-bookworm
COPY requirements.txt /tmp
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

COPY exporter.py /usr/local/bin/homematic_exporter
COPY healthcheck.sh /usr/local/bin/healthcheck.sh

ENTRYPOINT [ "/usr/local/bin/homematic_exporter" ]

EXPOSE 8010
HEALTHCHECK --interval=20s --timeout=3s \
    CMD bash /usr/local/bin/healthcheck.sh
