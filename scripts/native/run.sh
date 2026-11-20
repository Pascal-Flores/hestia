#!/usr/bin/env bash
# Starts native LLM, STT, TTS and the current Hestia Python gateway without Docker.
# Run in the foreground (or via the generated systemd --user unit).
set -euo pipefail
source "$(dirname "$0")/common.sh"

mode="${1:-start}"
case "$mode" in start|--check|--dry-run) ;; *) echo "Usage: $0 [--dry-run|--check]" >&2; exit 2;; esac
mem_mib=$(available_memory_mib)
choose_profile "$mem_mib"

LLAMA_BIN="${NATIVE_LLAMA_BIN:-$NATIVE_HOME/bin/llama-server}"
WHISPER_BIN="${NATIVE_WHISPER_BIN:-$NATIVE_HOME/bin/whisper-server}"
POCKET_BIN="${NATIVE_POCKET_BIN:-$NATIVE_HOME/bin/pocket-tts}"
WHISPER_MODEL="${NATIVE_WHISPER_MODEL:-$NATIVE_HOME/models/ggml-${STT_MODEL}.bin}"
UVICORN_BIN="${NATIVE_UVICORN_BIN:-$NATIVE_HOME/venv/bin/uvicorn}"

if [[ "$mode" == --dry-run ]]; then
  echo "RAM available to profile selector: $mem_mib MiB; profile=$profile"
  echo "LLM: $LLM_HF_REPO context=$LLM_CONTEXT, STT=$WHISPER_MODEL, TTS=$TTS_LANGUAGE quantized=$TTS_QUANTIZE"
  echo "Processes: llama-server, whisper-server, pocket-tts, uvicorn (no Docker)"
  echo 'Important: 2g is a tuning target, not a guaranteed RAM cap.'
  exit 0
fi

missing=0
check_binary llama-server "$LLAMA_BIN" || missing=1
check_binary whisper-server "$WHISPER_BIN" || missing=1
check_binary pocket-tts "$POCKET_BIN" || missing=1
check_binary uvicorn "$UVICORN_BIN" || missing=1
if [[ ! -f "$WHISPER_MODEL" ]]; then
  echo "Missing STT model: $WHISPER_MODEL (run ./scripts/native/install.sh)" >&2
  missing=1
fi
if ! command -v ffmpeg >/dev/null; then
  echo 'Missing ffmpeg (needed for 16-kHz WAV conversion)' >&2
  missing=1
fi
if (( missing )); then exit 1; fi
if [[ "$mode" == --check ]]; then echo "Native dependencies OK ($profile)"; exit 0; fi

mkdir -p "$NATIVE_HOME/logs" "$NATIVE_HOME/run" "$NATIVE_HOME/cache/hf" "$NATIVE_HOME/cache/embeddings"
export HF_HOME="${HF_HOME:-$NATIVE_HOME/cache/hf}"
export HESTIA_TOOLS_DIR="${HESTIA_TOOLS_DIR:-$HESTIA_ROOT/tools}"
export HESTIA_EMBEDDING_BACKEND="${HESTIA_EMBEDDING_BACKEND:-fastembed}"
export HESTIA_EMBEDDING_MODEL="${HESTIA_EMBEDDING_MODEL:-intfloat/multilingual-e5-small}"
export HESTIA_EMBEDDING_CACHE="${HESTIA_EMBEDDING_CACHE:-$NATIVE_HOME/cache/embeddings}"
export HESTIA_LLM_URL="${HESTIA_LLM_URL:-http://127.0.0.1:8080/v1/chat/completions}"
export HESTIA_LLM_MODEL="${HESTIA_LLM_MODEL:-hestia-local}"
export HESTIA_FALLBACK_LLM_URL="${HESTIA_FALLBACK_LLM_URL:-$HESTIA_LLM_URL}"
export HESTIA_FALLBACK_LLM_MODEL="${HESTIA_FALLBACK_LLM_MODEL:-$HESTIA_LLM_MODEL}"
export HESTIA_STT_URL="${HESTIA_STT_URL:-http://127.0.0.1:8001/inference}"
export HESTIA_STT_BACKEND=whisper_cpp
export HESTIA_TTS_URL="${HESTIA_TTS_URL:-http://127.0.0.1:8000/tts}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export RAYON_NUM_THREADS="$TTS_THREADS"
export PYTHONUNBUFFERED=1

pids=()
cleanup() {
  trap - EXIT TERM INT
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  rm -f "$NATIVE_HOME/run/"{llm,stt,tts,hestia}.pid
}
trap cleanup EXIT TERM INT

start_service() {
  local name="$1"; shift
  "$@" >"$NATIVE_HOME/logs/$name.log" 2>&1 &
  local pid=$!
  pids+=("$pid")
  printf '%s\n' "$pid" >"$NATIVE_HOME/run/$name.pid"
  echo "Started $name (PID $pid, log: $NATIVE_HOME/logs/$name.log)"
}

# Native llama.cpp downloads model on the first launch if --hf-repo is used.
start_service llm "$LLAMA_BIN" --hf-repo "$LLM_HF_REPO" --host 127.0.0.1 --port 8080 \
  --alias hestia-local --reasoning off --ctx-size "$LLM_CONTEXT" \
  --threads "$LLM_THREADS" --parallel 1 --n-predict "$LLM_MAX_TOKENS"
start_service stt "$WHISPER_BIN" --model "$WHISPER_MODEL" --language fr \
  --host 127.0.0.1 --port 8001 --threads "$STT_THREADS" --no-gpu
pocket_args=(serve --host 127.0.0.1 --port 8000 --language "$TTS_LANGUAGE" --default-voice "$TTS_VOICE")
if [[ "$TTS_QUANTIZE" == 1 ]]; then pocket_args+=(--quantize); fi
start_service tts "$POCKET_BIN" "${pocket_args[@]}"

cd "$HESTIA_ROOT"
start_service hestia "$UVICORN_BIN" hestia.server:app --host "$HESTIA_BIND_IP" --port "$HESTIA_PORT" --workers 1

echo "Hestia native ($profile) running at http://$HESTIA_BIND_IP:$HESTIA_PORT"
echo "Stop with Ctrl+C; inspect memory with ./scripts/native/memory.sh"
# All services are essential; stop the entire stack if one exits.
if wait -n "${pids[@]}"; then
  echo 'A native service stopped; shutting down the stack' >&2
else
  echo 'A native service failed; see .native/logs/*.log' >&2
  exit 1
fi
