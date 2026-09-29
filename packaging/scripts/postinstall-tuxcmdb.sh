#!/usr/bin/env bash
set -euo pipefail

if [[ -f /opt/tuxcmdb/conf/database.yaml ]]; then
  if ! /usr/bin/tuxcmdb migrate; then
    echo "WARNING: tuxcmdb migrate failed; run '/usr/bin/tuxcmdb migrate' manually." >&2
  fi
else
  echo "tuxcmdb: no database config found at /opt/tuxcmdb/conf/database.yaml; skipping migrate. Run 'tuxcmdb setup' first." >&2
fi
