#!/usr/bin/env bash
# Register Hestia in the current user's systemd session (no sudo/root).
set -euo pipefail
source "$(dirname "$0")/common.sh"
command -v systemctl >/dev/null || { echo 'systemd user service unavailable' >&2; exit 1; }
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
cat > "$unit_dir/hestia-native.service" <<UNIT
[Unit]
Description=Hestia native local-first voice assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HESTIA_ROOT
ExecStart=$HESTIA_ROOT/scripts/native/run.sh
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now hestia-native.service
printf 'Service installed: systemctl --user status hestia-native.service\n'
