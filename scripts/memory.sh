#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
echo 'Instantaneous Docker container memory usage (not total host RAM):'
docker compose ps -q | while IFS= read -r container; do
    [ -n "$container" ] || continue
    docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}' "$container"
done
