#!/usr/bin/env bash

REPO=s0riak/homematic_exporter
docker buildx build --platform linux/amd64 --platform linux/arm/v7 --platform linux/arm64 -t $REPO:"$(date +%F)" -t $REPO:latest --push .
