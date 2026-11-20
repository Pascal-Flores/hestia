#!/usr/bin/env bash
# Report RSS and proportional set size (PSS) for the native services.
set -euo pipefail
source "$(dirname "$0")/common.sh"
if [[ ! -d "$NATIVE_HOME/run" ]]; then
  echo 'No native services recorded (.native/run missing)' >&2; exit 1
fi
printf '%-12s %8s %12s %12s\n' SERVICE PID RSS_MiB PSS_MiB
total_rss=0; total_pss=0
for service in llm stt tts hestia; do
  file="$NATIVE_HOME/run/$service.pid"
  if [[ ! -s "$file" ]]; then continue; fi
  pid=$(cat "$file")
  [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/status" ]] || continue
  rss=$(awk '/^VmRSS:/ {print $2;exit}' "/proc/$pid/status")
  pss=$(awk '/^Pss:/ {print $2;exit}' "/proc/$pid/smaps_rollup" 2>/dev/null || true)
  rss=${rss:-0}; pss=${pss:-0}
  total_rss=$((total_rss + rss)); total_pss=$((total_pss + pss))
  awk -v n="$service" -v pid="$pid" -v rss="$rss" -v pss="$pss" \
    'BEGIN {printf "%-12s %8s %12.1f %12.1f\n", n, pid, rss/1024, pss/1024}'
done
awk -v rss="$total_rss" -v pss="$total_pss" \
  'BEGIN {printf "%-12s %8s %12.1f %12.1f\n", "TOTAL", "-", rss/1024, pss/1024}'
echo 'PSS shares mapped memory fairly between processes; host OS and other services are not included.'
