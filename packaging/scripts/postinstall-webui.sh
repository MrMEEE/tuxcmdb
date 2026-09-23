#!/usr/bin/env bash
set -euo pipefail

if [[ ! -e /etc/sysconfig/tuxcmdb-webui ]]; then
  mkdir -p /etc/sysconfig
  cat > /etc/sysconfig/tuxcmdb-webui <<'EOF'
# TuxCMDB WebUI environment settings
#
# Comma-separated browser origins allowed to submit POST/PUT/PATCH/DELETE
# requests through a reverse proxy. Include the scheme and any external port.
# Example:
# TUXCMDB_CSRF_TRUSTED_ORIGINS=https://tuxcmdb.example.com,https://localhost:4443
TUXCMDB_CSRF_TRUSTED_ORIGINS=
#
# Optional location of package download files displayed at /agents/.
# TUXCMDB_AGENTS_DIR=/opt/tuxcmdb-webui-agents
EOF
  chmod 0644 /etc/sysconfig/tuxcmdb-webui
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
  systemctl try-restart tuxcmdb-webui.service || true
fi
