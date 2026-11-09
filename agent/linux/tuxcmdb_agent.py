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
import tempfile
from pathlib import Path
from typing import Any

import requests
import urllib3

DEFAULT_CONFIG = Path("/etc/tuxcmdb-agent/config.json")
DEFAULT_TIMEOUT = 30
AGENT_USER = "tuxcmdb-agent"
SUDOERS_DIR = Path("/etc/sudoers.d")
SUDOERS_PREFIX = "tuxcmdb-agent-"
# Stamped with the release version at RPM build time; keep as-is for source checkouts.
AGENT_VERSION = "0.0.0-dev"


def ask(prompt: str) -> str:
    return input(prompt).strip()


def normalize_server_url(url: str) -> str:
    return url.strip().rstrip("/")


def _apply_verify_ssl(verify_ssl: bool) -> None:
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
    _apply_verify_ssl(verify_ssl)

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
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.sudo_action and args.action != "sudo":
        parser.error("add/remove are only valid with sudo")
    if args.sudo_action and not (args.all or args.ids):
        parser.error("sudo add/remove requires --all or --ids")
    if (args.all or args.ids) and not args.sudo_action:
        parser.error("--all/--ids requires sudo add/remove")
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
    _apply_verify_ssl(verify_ssl)

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


def find_privileged_commands(bootstrap: dict[str, Any]) -> list[dict[str, Any]]:
    privileged: list[dict[str, Any]] = []
    for task in bootstrap.get("tasks", []):
        commands = [
            entry["command"] for entry in task.get("commands", []) if entry.get("needs_privilege")
        ]
        if commands:
            attribute_id = task.get("attribute_id")
            if type(attribute_id) is not int or attribute_id <= 0:
                raise BootstrapError("Bootstrap lacks valid attribute IDs; upgrade the API server first.")
            privileged.append({
                "attribute_id": attribute_id,
                "attribute_name": task["attribute_name"],
                "commands": list(dict.fromkeys(commands)),
            })
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


def _sudo_rule_names(item: dict[str, Any]) -> list[str]:
    rule_id = str(item["attribute_id"])
    legacy_path = _sudoers_rule_path(item["attribute_name"])
    legacy_name = legacy_path.name[len(SUDOERS_PREFIX):]
    names = [rule_id]
    # A numeric attribute name can collide with another attribute's ID-based file.
    if legacy_name != rule_id and not (
        legacy_path.is_file()
        and legacy_path.read_text(encoding="utf-8").startswith("# tuxcmdb-agent attribute-id:")
    ):
        names.append(legacy_name)
    return names


# Characters sudoers allows to be backslash-escaped inside an unquoted command
# word; every other character (quotes, pipes, wildcards, etc.) must stay literal
# or visudo rejects the rule with "illegal escape sequence".
_SUDOERS_ESCAPE_CHARS = set(" \t\\,:=")


def _escape_for_sudoers(command: str) -> str:
    return "".join(("\\" + ch) if ch in _SUDOERS_ESCAPE_CHARS else ch for ch in command)


def _validate_sudoers(rule_path: Path) -> bool:
    try:
        result = subprocess.run(["visudo", "-cf", str(rule_path)], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("visudo is required to validate sudo rules; no rule installed.", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr)
    return result.returncode == 0


def _add_sudo_rule(item: dict[str, Any]) -> bool:
    rule_path = _sudoers_rule_path(str(item["attribute_id"]))
    lines = f"# tuxcmdb-agent attribute-id: {item['attribute_id']}\n" + "".join(
        f'{AGENT_USER} ALL=(root) NOPASSWD: {_sh_path()} -c {_escape_for_sudoers(command)}\n'
        for command in item["commands"]
    )
    # A dotted temporary name is ignored by sudo's includedir while being validated.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=SUDOERS_DIR,
                                     prefix=".tuxcmdb-agent-", delete=False) as handle:
        staged_path = Path(handle.name)
        handle.write(lines)
    try:
        os.chmod(staged_path, 0o440)
        if not _validate_sudoers(staged_path):
            print(f"Generated sudoers rule for '{item['attribute_name']}' failed validation; not installed",
                  file=sys.stderr)
            return False
        staged_path.replace(rule_path)
    finally:
        if staged_path.exists():
            staged_path.unlink()
    for legacy_name in _sudo_rule_names(item)[1:]:
        legacy_path = _sudoers_rule_path(legacy_name)
        if legacy_path.exists():
            legacy_path.unlink()
    print(f"Added sudo rule: {rule_path}")
    return True


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

    if args.sudo_action == "remove" and args.all:
        for name in _existing_sudo_rules():
            _remove_sudo_rule(name)
        return 0

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"No agent config found at {config_path}; register the agent first.", file=sys.stderr)
        return 1
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    verify_ssl = config.get("verify_ssl", True)
    _apply_verify_ssl(verify_ssl)

    try:
        bootstrap, _selected_match, _os_info = fetch_bootstrap(config, verify_ssl)
        privileged_commands = find_privileged_commands(bootstrap)
    except (BootstrapError, requests.RequestException) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    existing_rules = _existing_sudo_rules()

    if args.sudo_action:
        by_id = {item["attribute_id"]: item for item in privileged_commands}
        selected_ids = list(by_id) if args.all else args.ids
        unknown_ids = [
            value for value in selected_ids
            if value not in by_id and not (
                args.sudo_action == "remove" and str(value) in existing_rules
            )
        ]
        if unknown_ids:
            print("No privileged rule for attribute ID(s): " + ", ".join(map(str, unknown_ids)),
                  file=sys.stderr)
            return 1
        success = True
        for value in selected_ids:
            if args.sudo_action == "add":
                if not _add_sudo_rule(by_id[value]):
                    success = False
            else:
                _remove_sudo_rule(str(value))
                if value in by_id:
                    for legacy_name in _sudo_rule_names(by_id[value])[1:]:
                        if legacy_name in existing_rules:
                            _remove_sudo_rule(legacy_name)
        if not selected_ids:
            print("No attribute commands require privilege escalation for this host.")
        return 0 if success else 1

    if not privileged_commands and not existing_rules:
        print("No attribute commands require privilege escalation for this host.")
        return 0

    print("Privileged attribute commands:")
    print("-" * 60)
    known_names: set[str] = set()
    for item in privileged_commands:
        rule_id = str(item["attribute_id"])
        names = _sudo_rule_names(item)
        known_names.update(names)
        status = "[configured]" if any(name in existing_rules for name in names) else "[not configured]"
        print(f"  {rule_id:>2}. {item['attribute_name']:<24} {status}")
        for command in item["commands"]:
            print(f"      $ {command}")

    unmatched_rules = [name for name in existing_rules if name not in known_names]
    if unmatched_rules:
        print()
        print("Sudo rules with no matching privileged command:")
        for name in unmatched_rules:
            print(f"  - {name}")

    print("-" * 60)
    print("Commands:")
    print("  a <id>       add all privileged commands for an attribute ID")
    print("  r <id/name>  remove an existing sudo rule (legacy names accepted)")
    print("  q            quit")
    while True:
        choice = ask("\ntuxcmdb-agent sudo> ")
        if not choice or choice.lower() in {"q", "quit", "exit"}:
            return 0
        parts = choice.split(maxsplit=1)
        action = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if action == "a" and arg.isdigit():
            item = next((item for item in privileged_commands if item["attribute_id"] == int(arg)), None)
            if item is not None:
                _add_sudo_rule(item)
                existing_rules = _existing_sudo_rules()
            else:
                print("Invalid selection")
        elif action == "r" and arg:
            _remove_sudo_rule(arg)
            item = next((item for item in privileged_commands if str(item["attribute_id"]) == arg), None)
            if item is not None:
                for legacy_name in _sudo_rule_names(item)[1:]:
                    if legacy_name in existing_rules:
                        _remove_sudo_rule(legacy_name)
            existing_rules = _existing_sudo_rules()
        else:
            print("Unrecognized command")


def _parse_sudo_ids(value: str) -> list[int]:
    parts = value.split(",")
    if not all(re.fullmatch(r"[0-9]+", part.strip()) and int(part) > 0 for part in parts):
        raise argparse.ArgumentTypeError("IDs must be comma-separated positive attribute IDs")
    return list(dict.fromkeys(int(part) for part in parts))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="tuxcmdb Linux agent")
    parser.add_argument("--version", action="version", version=AGENT_VERSION)
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
    parser.add_argument("sudo_action", nargs="?", choices=["add", "remove"],
                        help="Non-interactive sudo rule management")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="Select all agent sudo rules")
    selection.add_argument("--ids", "--id", type=_parse_sudo_ids,
                           help="Comma-separated attribute IDs (--id is an alias)")
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
