#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

DEFAULT_CONFIG = Path("/etc/tuxcmdb-agent/config.json")
DEFAULT_TIMEOUT = 30
AGENT_USER = "tuxcmdb-agent"
SUDOERS_DIR = Path("/etc/sudoers.d")
SUDOERS_PREFIX = "tuxcmdb-agent-"


def ask(prompt: str) -> str:
    return input(prompt).strip()


def normalize_server_url(url: str) -> str:
    return url.strip().rstrip("/")


def _agent_uid_gid() -> tuple[int, int] | None:
    try:
        import pwd
    except ImportError:
        return None
    try:
        entry = pwd.getpwnam(AGENT_USER)
    except KeyError:
        return None
    return entry.pw_uid, entry.pw_gid


def _chown_to_agent_user(path: Path) -> None:
    ids = _agent_uid_gid()
    if ids is None:
        return
    try:
        os.chown(path, *ids)
    except OSError:
        pass


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


def _sh_path() -> str:
    return shutil.which("sh") or "/bin/sh"


def run_command(command: str, needs_privilege: bool = False) -> str | None:
    if needs_privilege and os.geteuid() != 0:
        argv: str | list[str] = ["sudo", "-n", _sh_path(), "-c", command]
        use_shell = False
    else:
        argv = command
        use_shell = True

    try:
        result = subprocess.run(
            argv,
            shell=use_shell,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return None

    # Only return output if command succeeded (exit code 0)
    if result.returncode != 0:
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
    _chown_to_agent_user(config_path.parent)

    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if "server_url" in data and "asset_id" in data and "systempass" in data:
            data.setdefault("verify_ssl", True)
            return data

    server_url = normalize_server_url(args.server_url or "")
    if not server_url:
        server_url = normalize_server_url(ask("CMDB API URL (e.g. http://127.0.0.1:8080): "))
    if not server_url:
        raise SystemExit("Missing CMDB API URL")

    verify_ssl = not args.insecure

    payload: dict[str, Any] = {}
    if args.assetid:
        payload["asset_id"] = int(args.assetid)
    else:
        payload["assetname"] = (args.assetname or socket.gethostname() or f"asset-{secrets.token_hex(4)}").lower()

    response = requests.post(
        f"{server_url}/v1/agent/register",
        json=payload,
        timeout=DEFAULT_TIMEOUT,
        verify=verify_ssl,
    )
    if response.status_code >= 400:
        raise SystemExit(f"Agent registration failed: {response.status_code} {response.text}")
    data = response.json()

    config = {
        "server_url": server_url,
        "asset_id": data["id"],
        "systempass": data["systempass"],
        "verify_ssl": verify_ssl,
    }

    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=True)
    os.chmod(config_path, 0o600)
    _chown_to_agent_user(config_path)
    print(f"Registered asset {data['assetname']} (id={data['id']}); waiting for approval")
    return config


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.action == "sudo":
        return cmd_sudo(args)
    return cmd_report(args)


class BootstrapError(RuntimeError):
    pass


def fetch_bootstrap(config: dict[str, Any], verify_ssl: bool) -> tuple[dict[str, Any], str, dict[str, Any]]:
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
            verify=verify_ssl,
        )
        if bootstrap_response.status_code >= 400:
            raise BootstrapError(f"Bootstrap failed: {bootstrap_response.status_code} {bootstrap_response.text}")

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
        raise BootstrapError("Bootstrap failed: no usable response")

    return bootstrap, selected_match, os_info


def cmd_report(args: argparse.Namespace) -> int:
    if os.geteuid() == 0:
        print(
            f"Warning: running as root is not recommended; use the unprivileged '{AGENT_USER}' user instead.",
            file=sys.stderr,
        )

    config = ensure_config(args)
    verify_ssl = config.get("verify_ssl", True)

    try:
        bootstrap, selected_match, os_info = fetch_bootstrap(config, verify_ssl)
    except BootstrapError as exc:
        print(str(exc))
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
        for command_entry in task.get("commands", []):
            value = run_command(command_entry["command"], needs_privilege=bool(command_entry.get("needs_privilege")))
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
        verify=verify_ssl,
    )
    if report_response.status_code >= 400:
        print(f"Report failed: {report_response.status_code} {report_response.text}")
        return 1

    print(f"Reported {len(report_values)} value(s)")
    return 0


def find_privileged_commands(bootstrap: dict[str, Any]) -> list[dict[str, str]]:
    privileged: list[dict[str, str]] = []
    for task in bootstrap.get("tasks", []):
        attribute_name = task.get("attribute_name")
        for command_entry in task.get("commands", []):
            if command_entry.get("needs_privilege"):
                privileged.append({"attribute_name": attribute_name, "command": command_entry["command"]})
    return privileged


def _sudoers_rule_path(attribute_name: str) -> Path:
    safe_name = re.sub(r"[^a-z0-9_-]+", "-", attribute_name.strip().lower()).strip("-") or "rule"
    return SUDOERS_DIR / f"{SUDOERS_PREFIX}{safe_name}"


def _existing_sudo_rules() -> dict[str, str]:
    rules: dict[str, str] = {}
    if not SUDOERS_DIR.is_dir():
        return rules
    for entry in sorted(SUDOERS_DIR.iterdir()):
        if entry.is_file() and entry.name.startswith(SUDOERS_PREFIX):
            rules[entry.name[len(SUDOERS_PREFIX):]] = entry.read_text(encoding="utf-8").strip()
    return rules


def _escape_for_sudoers(command: str) -> str:
    return command.replace("\\", "\\\\").replace('"', '\\"')


def _validate_sudoers(rule_path: Path) -> bool:
    try:
        result = subprocess.run(["visudo", "-cf", str(rule_path)], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return True
    return result.returncode == 0


def _add_sudo_rule(attribute_name: str, command: str) -> None:
    rule_path = _sudoers_rule_path(attribute_name)
    line = f'{AGENT_USER} ALL=(root) NOPASSWD: {_sh_path()} -c "{_escape_for_sudoers(command)}"\n'
    rule_path.write_text(line, encoding="utf-8")
    os.chmod(rule_path, 0o440)
    if not _validate_sudoers(rule_path):
        rule_path.unlink(missing_ok=True)
        print(f"Generated sudoers rule for '{attribute_name}' failed validation; not installed", file=sys.stderr)
        return
    print(f"Added sudo rule: {rule_path}")


def _remove_sudo_rule(attribute_name: str) -> None:
    rule_path = _sudoers_rule_path(attribute_name)
    if rule_path.exists():
        rule_path.unlink()
        print(f"Removed sudo rule: {rule_path}")
    else:
        print(f"No sudo rule found for '{attribute_name}'")


def cmd_sudo(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        print(f"The 'sudo' command must be run as root to manage {SUDOERS_DIR} rules.", file=sys.stderr)
        return 1

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"No agent config found at {config_path}; register the agent first.", file=sys.stderr)
        return 1
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    verify_ssl = config.get("verify_ssl", True)

    try:
        bootstrap, _selected_match, _os_info = fetch_bootstrap(config, verify_ssl)
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    privileged_commands = find_privileged_commands(bootstrap)
    existing_rules = _existing_sudo_rules()

    if not privileged_commands and not existing_rules:
        print("No attribute commands require privilege escalation for this host.")
        return 0

    print("Privileged attribute commands:")
    known_names: set[str] = set()
    for index, item in enumerate(privileged_commands, start=1):
        safe_name = _sudoers_rule_path(item["attribute_name"]).name[len(SUDOERS_PREFIX):]
        known_names.add(safe_name)
        configured = " (sudo rule configured)" if safe_name in existing_rules else ""
        print(f"  [{index}] {item['attribute_name']}: {item['command']}{configured}")

    unmatched_rules = [name for name in existing_rules if name not in known_names]
    if unmatched_rules:
        print("\nExisting sudo rules with no matching privileged command:")
        for name in unmatched_rules:
            print(f"  - {name}")

    print("\nCommands: 'a <number>' add rule, 'r <name>' remove rule, 'q' quit")
    while True:
        choice = ask("> ")
        if not choice or choice.lower() in {"q", "quit", "exit"}:
            return 0
        parts = choice.split(maxsplit=1)
        action = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if action == "a" and arg.isdigit():
            index = int(arg)
            if 1 <= index <= len(privileged_commands):
                item = privileged_commands[index - 1]
                _add_sudo_rule(item["attribute_name"], item["command"])
                existing_rules = _existing_sudo_rules()
            else:
                print("Invalid selection")
        elif action == "r" and arg:
            _remove_sudo_rule(arg)
            existing_rules = _existing_sudo_rules()
        else:
            print("Unrecognized command")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="tuxcmdb Linux agent")
    parser.add_argument(
        "action",
        nargs="?",
        default="report",
        choices=["report", "sudo"],
        help=(
            "'report' registers/bootstraps and reports attribute values (default); "
            "'sudo' manages NOPASSWD sudo rules for privileged attribute commands"
        ),
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--server-url")
    parser.add_argument("--assetid")
    parser.add_argument("--assetname")
    parser.add_argument("--once", action="store_true", default=True)
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Do not verify the server's TLS certificate (allows self-signed certificates)",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
