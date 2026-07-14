#!/usr/bin/env bash
set -euo pipefail

if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now tuxcmdb-webui.service || true
  systemctl daemon-reload || true
fi
