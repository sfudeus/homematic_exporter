#!/bin/bash

# First argument is the maximum age in seconds (default: 60)
maxage=${1:-60}

# Get the age of the last successful metrics refresh in seconds
age=$(curl -s http://localhost:9040/metrics | grep 'homematic_refresh_age{' | cut -d ' ' -f2 | cut -d '.' -f1)

if [[ $age -lt $maxage ]]; then
    exit 0
else
    # Maximum age exceeded → unhealthy
    exit 1
fi
