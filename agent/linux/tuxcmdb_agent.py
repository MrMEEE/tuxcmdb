#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

DEFAULT_CONFIG = Path("/etc/tuxcmdb-agent/config.json")
DEFAULT_TIMEOUT = 30


def ask(prompt: str) -> str:
    return input(prompt).strip()


def normalize_server_url(url: str) -> str:
    return url.strip().rstrip("/")


def _read_os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return values

    for line in os_release.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _dedupe_keep_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def detect_operating_system() -> dict[str, Any]:
    os_release = _read_os_release()

    distro_id = (os_release.get("ID") or platform.system() or "linux").strip().lower()
    version_id = (os_release.get("VERSION_ID") or "").strip().lower()
    version_codename = (os_release.get("VERSION_CODENAME") or "").strip().lower()
    id_like = (os_release.get("ID_LIKE") or "").strip().lower().split()

    version_major = version_id.split(".", 1)[0] if version_id else ""

    candidates: list[str] = []
    if distro_id and version_id:
        candidates.append(f"{distro_id}-{version_id}")
    if distro_id and version_major and version_major != version_id:
        candidates.append(f"{distro_id}-{version_major}")
    if distro_id and version_codename:
        candidates.append(f"{distro_id}-{version_codename}")
    if distro_id:
        candidates.append(distro_id)
    for parent in id_like:
        if version_major:
            candidates.append(f"{parent}-{version_major}")
        candidates.append(parent)

    report_value = candidates[0] if candidates else (distro_id or "linux")

    return {
        "report_value": report_value,
        "match_candidates": _dedupe_keep_order(candidates) or [distro_id or "linux"],
    }


def run_command(command: str) -> str | None:
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return None

    output = (result.stdout or "").strip()
    if not output:
        output = (result.stderr or "").strip()
    return output or None


def split_output_lines(output: str) -> list[str]:
    lines: list[str] = []
    for line in output.splitlines():
        value = line.strip()
        if value:
            lines.append(value)
    return lines


def ensure_config(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if "server_url" in data and "asset_id" in data and "systempass" in data:
            return data

    server_url = normalize_server_url(args.server_url or "")
    if not server_url:
        server_url = normalize_server_url(ask("CMDB API URL (e.g. http://127.0.0.1:8080): "))
    if not server_url:
        raise SystemExit("Missing CMDB API URL")

    payload: dict[str, Any] = {}
    if args.assetid:
        payload["asset_id"] = int(args.assetid)
    else:
        payload["assetname"] = (args.assetname or socket.gethostname() or f"asset-{secrets.token_hex(4)}").lower()

    response = requests.post(
        f"{server_url}/v1/agent/register",
        json=payload,
        timeout=DEFAULT_TIMEOUT,
    )
    if response.status_code >= 400:
        raise SystemExit(f"Agent registration failed: {response.status_code} {response.text}")
    data = response.json()

    config = {
        "server_url": server_url,
        "asset_id": data["id"],
        "systempass": data["systempass"],
    }

    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=True)
    os.chmod(config_path, 0o600)
    print(f"Registered asset {data['assetname']} (id={data['id']}); waiting for approval")
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="tuxcmdb Linux agent")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--server-url")
    parser.add_argument("--assetid")
    parser.add_argument("--assetname")
    parser.add_argument("--once", action="store_true", default=True)
    args = parser.parse_args()

    config = ensure_config(args)
    os_info = detect_operating_system()

    bootstrap: dict[str, Any] | None = None
    selected_match = ""
    for candidate in os_info["match_candidates"]:
        bootstrap_response = requests.post(
            f"{config['server_url']}/v1/agent/bootstrap",
            json={
                "asset_id": config["asset_id"],
                "systempass": config["systempass"],
                "operating_system": candidate,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if bootstrap_response.status_code >= 400:
            print(f"Bootstrap failed: {bootstrap_response.status_code} {bootstrap_response.text}")
            return 1

        candidate_bootstrap = bootstrap_response.json()
        if bootstrap is None:
            bootstrap = candidate_bootstrap
            selected_match = candidate

        if int(candidate_bootstrap.get("approved", 0)) != 2:
            bootstrap = candidate_bootstrap
            selected_match = candidate
            break

        if candidate_bootstrap.get("tasks"):
            bootstrap = candidate_bootstrap
            selected_match = candidate
            break

    if bootstrap is None:
        print("Bootstrap failed: no usable response")
        return 1

    if int(bootstrap.get("approved", 0)) != 2:
        print(f"Asset not approved for reporting (state={bootstrap.get('approved')}). Exiting.")
        return 0

    print(f"Using OS match key: {selected_match}; reporting OS value: {os_info['report_value']}")

    report_values: list[dict[str, Any]] = [
        {"attribute_name": "os", "value": os_info["report_value"]}
    ]
    for task in bootstrap.get("tasks", []):
        attribute_name = task.get("attribute_name")
        for command in task.get("commands", []):
            value = run_command(command)
            if value is None:
                continue
            values = split_output_lines(value)
            if not values:
                continue
            for line_value in values:
                report_values.append({"attribute_name": attribute_name, "value": line_value})

    if not report_values:
        print("No values to report")
        return 0

    report_response = requests.post(
        f"{config['server_url']}/v1/agent/report",
        json={
            "asset_id": config["asset_id"],
            "systempass": config["systempass"],
            "values": report_values,
        },
        timeout=DEFAULT_TIMEOUT,
    )
    if report_response.status_code >= 400:
        print(f"Report failed: {report_response.status_code} {report_response.text}")
        return 1

    print(f"Reported {len(report_values)} value(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
