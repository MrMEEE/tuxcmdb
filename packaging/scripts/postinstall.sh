#!/usr/bin/env bash
set -euo pipefail

if ! getent passwd tuxcmdb-agent >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin \
    --comment "TuxCMDB Agent" tuxcmdb-agent
fi

mkdir -p /etc/tuxcmdb-agent
chown -R tuxcmdb-agent:tuxcmdb-agent /etc/tuxcmdb-agent
chmod 750 /etc/tuxcmdb-agent

if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  os_major="${VERSION_ID:-}"
  os_major="${os_major%%.*}"
  if [[ "$os_major" == "8" && " ${ID:-} ${ID_LIKE:-} " =~ (rhel|centos|rocky|almalinux|ol) ]]; then
    echo "WARNING: EL8 Python compatibility mode is deprecated and will be removed in a future release. Upgrade to Python 3.9 or newer when possible." >&2
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi
