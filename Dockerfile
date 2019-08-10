FROM python:3-slim-buster

RUN pip3 install prometheus_client

ADD exporter.py /usr/local/bin/homematic_exporter

ENTRYPOINT [ "/usr/local/bin/homematic_exporter" ]