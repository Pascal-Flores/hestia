#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

# Auto-detect the available physical/cgroup memory, capped by container limits.
mem_mb=$(awk '/MemTotal:/ {printf "%d", $2 / 1024}' /proc/meminfo)
if [ -r /sys/fs/cgroup/memory.max ]; then
    limit=$(cat /sys/fs/cgroup/memory.max)
    if [ "$limit" != "max" ]; then
        cgroup_mb=$((limit / 1024 / 1024))
        [ "$cgroup_mb" -gt 0 ] && [ "$cgroup_mb" -lt "$mem_mb" ] && mem_mb=$cgroup_mb
    fi
fi
case "${HESTIA_MEMORY_PROFILE:-auto}" in
    auto)
        if [ "$mem_mb" -lt 3072 ]; then profile=2g
        elif [ "$mem_mb" -lt 7168 ]; then profile=4g
        else profile=8g
        fi ;;
    2g|4g|8g) profile=$HESTIA_MEMORY_PROFILE ;;
    *) echo 'HESTIA_MEMORY_PROFILE must be auto, 2g, 4g or 8g' >&2; exit 2 ;;
esac

# Do not override explicit shell or .env choices. Profile defaults are only suggestions.
set_if_undefined() {
    name=$1; value=$2
    eval "existing=\${$name-}"
    if [ -z "$existing" ] && ! grep -Eq "^${name}=" .env 2>/dev/null; then
        export "$name=$value"
    fi
}
case "$profile" in
    2g)
        set_if_undefined LLM_HF_REPO 'bartowski/Qwen_Qwen3-0.6B-GGUF:Q4_K_M'
        set_if_undefined LLM_CONTEXT 1024
        set_if_undefined STT_MODEL tiny
        ;;
    4g)
        set_if_undefined LLM_HF_REPO 'bartowski/Qwen_Qwen3-0.6B-GGUF:Q4_K_M'
        set_if_undefined LLM_CONTEXT 2048
        set_if_undefined STT_MODEL tiny
        ;;
    8g)
        set_if_undefined LLM_HF_REPO 'bartowski/Qwen_Qwen3-1.7B-GGUF:Q4_K_M'
        set_if_undefined LLM_CONTEXT 4096
        set_if_undefined STT_MODEL base
        ;;
esac

echo "Hestia: RAM detected ${mem_mb} MiB; profile ${profile} (experimental resource limits)"
if [ "${1:-}" = '--dry-run' ]; then
    echo "docker compose -f compose.yaml -f profiles/${profile}.yaml up -d --build"
    exit 0
fi
docker compose -f compose.yaml -f "profiles/${profile}.yaml" up -d --build "$@"
