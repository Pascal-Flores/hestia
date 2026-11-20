#!/usr/bin/env bash
# Shared helpers for the native (Docker-free) launcher.
set -euo pipefail
HESTIA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_HOME="${HESTIA_NATIVE_HOME:-$HESTIA_ROOT/.native}"

# This file is trusted shell configuration, not untrusted plugin input.
if [[ -f "$HESTIA_ROOT/native.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$HESTIA_ROOT/native.env"
  set +a
fi

available_memory_mib() {
  local mem_mib limit
  mem_mib=$(awk '/^MemTotal:/ {printf "%d", $2 / 1024}' /proc/meminfo)
  if [[ -r /sys/fs/cgroup/memory.max ]]; then
    limit=$(cat /sys/fs/cgroup/memory.max)
    if [[ "$limit" != max ]] && [[ "$limit" =~ ^[0-9]+$ ]]; then
      limit=$((limit / 1024 / 1024))
      if (( limit > 0 && limit < mem_mib )); then mem_mib=$limit; fi
    fi
  fi
  printf '%s\n' "$mem_mib"
}

choose_profile() {
  local mem_mib="$1"
  HESTIA_MEMORY_PROFILE="${HESTIA_MEMORY_PROFILE:-auto}"
  case "$HESTIA_MEMORY_PROFILE" in
    auto)
      if (( mem_mib < 3072 )); then profile=2g
      elif (( mem_mib < 7168 )); then profile=4g
      else profile=8g; fi ;;
    2g|4g|8g) profile="$HESTIA_MEMORY_PROFILE" ;;
    *) echo 'HESTIA_MEMORY_PROFILE must be auto, 2g, 4g or 8g' >&2; return 2 ;;
  esac
  case "$profile" in
    2g)
      default_repo='bartowski/Qwen_Qwen3-0.6B-GGUF:Q4_K_M'
      default_context=1024
      default_whisper=tiny ;;
    4g)
      default_repo='bartowski/Qwen_Qwen3-0.6B-GGUF:Q4_K_M'
      default_context=2048
      default_whisper=tiny ;;
    8g)
      default_repo='bartowski/Qwen_Qwen3-1.7B-GGUF:Q4_K_M'
      default_context=4096
      default_whisper=base ;;
  esac
  LLM_HF_REPO="${LLM_HF_REPO:-$default_repo}"
  LLM_CONTEXT="${LLM_CONTEXT:-$default_context}"
  STT_MODEL="${STT_MODEL:-$default_whisper}"
  LLM_THREADS="${LLM_THREADS:-2}"
  STT_THREADS="${STT_THREADS:-2}"
  TTS_THREADS="${TTS_THREADS:-2}"
  LLM_MAX_TOKENS="${LLM_MAX_TOKENS:-256}"
  HESTIA_PORT="${HESTIA_PORT:-8765}"
  HESTIA_BIND_IP="${HESTIA_BIND_IP:-127.0.0.1}"
  TTS_LANGUAGE="${TTS_LANGUAGE:-french}"
  TTS_VOICE="${TTS_VOICE:-estelle}"
  TTS_QUANTIZE="${TTS_QUANTIZE:-1}"
}

check_binary() {
  local name="$1" path="$2"
  if [[ ! -x "$path" ]]; then
    echo "Missing $name: $path (run ./scripts/native/install.sh)" >&2
    return 1
  fi
}
