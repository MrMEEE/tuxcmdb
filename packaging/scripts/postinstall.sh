#!/usr/bin/env bash
set -euo pipefail

if ! getent passwd tuxcmdb-agent >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin \
    --comment "TuxCMDB Agent" tuxcmdb-agent
fi

mkdir -p /etc/tuxcmdb-agent
chown -R tuxcmdb-agent:tuxcmdb-agent /etc/tuxcmdb-agent
chmod 750 /etc/tuxcmdb-agent

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi
