FROM python:3-slim-buster

RUN pip3 install --no-cache-dir prometheus_client

COPY exporter.py /usr/local/bin/homematic_exporter

ENTRYPOINT [ "/usr/local/bin/homematic_exporter" ]

EXPOSE 8010
