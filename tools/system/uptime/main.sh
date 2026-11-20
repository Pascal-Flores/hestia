#!/bin/sh
# JSON input (parameters/context) arrives on stdin; no inputs required here.
# stdout is reserved for a single JSON object; logs belong on stderr.
IFS=. read -r seconds rest < /proc/uptime || exit 1
case "$seconds" in ''|*[!0-9]*) exit 1;; esac
printf '{"seconds":%s}\n' "$seconds"
