#!/usr/bin/env bash
set -euo pipefail

# rpm passes $1=0 on final erase (1+ means an upgrade is in progress);
# dpkg passes "remove"/"purge" on removal and "upgrade" on upgrade.
action="${1:-}"
if [[ "$action" == "0" || "$action" == "remove" || "$action" == "purge" ]]; then
  if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now tuxcmdb-api.service || true
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi
