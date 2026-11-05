#!/usr/bin/env bash
set -euo pipefail

target="${1:?Usage: build-agent-package.sh el8|el9|el10|debian [version]}"
version="${2:-$(sed -n 's/^VERSION = "\(.*\)"/\1/p' version.py)}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([._+-][a-zA-Z0-9]+)*$ ]]; then
    echo "Invalid agent package version: $version" >&2
    exit 1
fi

case "$target" in
    el8|el9|el10)
        format=rpm
        architecture=noarch
        iteration="1.$target"
        unit_dir=usr/lib/systemd/system
        dependencies=(--depends python3)
        ;;
    debian)
        format=deb
        architecture=all
        iteration=1.debian
        unit_dir=lib/systemd/system
        dependencies=(--depends 'python3 >= 3.9')
        ;;
    *) echo "Unsupported package target: $target" >&2; exit 1 ;;
esac

source_file=agent/linux/tuxcmdb_agent.py
if [[ "$target" == el8 ]]; then
    source_file=agent/linux/el8/tuxcmdb_agent.py
    dependencies+=(--depends python3-requests)
fi

buildroot="$(mktemp -d /tmp/tuxcmdb-agent-package.XXXXXX)"
trap 'rm -rf "$buildroot"' EXIT
output_dir="$PWD/dist/$target"
mkdir -p "$output_dir" "$buildroot/opt/tuxcmdb-agent/lib" \
    "$buildroot/usr/bin" "$buildroot/$unit_dir"
if [[ "$target" != el8 ]]; then
    # Keep noarch/all packages free of Python ABI-specific extension modules.
    python3 -m pip install --only-binary=:all: --platform any \
        -r agent/linux/requirements.txt -t "$buildroot/opt/tuxcmdb-agent/lib"
fi
cp "$source_file" "$buildroot/opt/tuxcmdb-agent/tuxcmdb_agent.py"
sed -i "s/^AGENT_VERSION = .*/AGENT_VERSION = \"$version\"/" \
    "$buildroot/opt/tuxcmdb-agent/tuxcmdb_agent.py"
chmod 755 "$buildroot/opt/tuxcmdb-agent/tuxcmdb_agent.py"
PYTHONPATH="$buildroot/opt/tuxcmdb-agent/lib" \
    python3 "$buildroot/opt/tuxcmdb-agent/tuxcmdb_agent.py" --help >/dev/null

cat > "$buildroot/usr/bin/tuxcmdb-agent" <<'EOF'
#!/usr/bin/env bash
export PYTHONPATH="/opt/tuxcmdb-agent/lib${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 /opt/tuxcmdb-agent/tuxcmdb_agent.py "$@"
EOF
chmod 755 "$buildroot/usr/bin/tuxcmdb-agent"
cp agent/linux/systemd/tuxcmdb-agent.service agent/linux/systemd/tuxcmdb-agent.timer \
    "$buildroot/$unit_dir/"

fpm -s dir -t "$format" \
    -n tuxcmdb-agent -v "$version" --iteration "$iteration" \
    --architecture "$architecture" "${dependencies[@]}" \
    --after-install packaging/scripts/postinstall.sh \
    --before-remove packaging/scripts/preuninstall.sh \
    --package "$output_dir/" -C "$buildroot" .
