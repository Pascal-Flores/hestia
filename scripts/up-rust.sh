#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
# Bascule uniquement l'API sur Rust; LLM, STT, TTS et listener sont inchangés.
docker compose -f compose.yaml -f compose.rust.yaml --profile listener up -d --build "$@"
