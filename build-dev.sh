#!/usr/bin/env bash

REPO=sfudeus/homematic_exporter
docker buildx build --platform linux/amd64 --platform linux/arm/v7 --platform linux/arm64 -t $REPO:"$(git describe --always --dirty)" --push .
