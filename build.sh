#!/usr/bin/env bash

REPO=sfudeus/homematic_exporter
docker buildx build --platform linux/amd64 --platform linux/arm/v7 -t $REPO:"$(date +%F)" -t $REPO:latest --push .
