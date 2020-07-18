#!/usr/bin/env bash

docker buildx build --platform linux/amd64 --platform linux/arm/v7 -t sfudeus/homematic_exporter:$(date +%F) --push .

