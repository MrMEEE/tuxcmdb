#!/usr/bin/env python3
"""tuxcmdb release manager."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "version.py"
README_FILE = PROJECT_ROOT / "README.md"


class ReleaseError(RuntimeError):
    pass


class ReleaseManager:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._changes: list[str] = []

    def _log(self, msg: str, level: str = "INFO") -> None:
        prefix = "[DRY-RUN] " if self.dry_run else ""
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{ts}  {prefix}{level}: {msg}")

    def info(self, msg: str) -> None:
        self._log(msg, "INFO")

    def warn(self, msg: str) -> None:
        self._log(msg, "WARN")

    def ok(self, msg: str) -> None:
        self._log(msg, "OK  ")

    def _run(self, cmd: list[str], *, read_only: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        self.info(f"$ {' '.join(cmd)}")
        if self.dry_run and not read_only:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, check=check)

    @staticmethod
    def parse_version(value: str) -> tuple[int, int, int]:
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value.strip())
        if not match:
            raise ReleaseError(f"Invalid version format: {value!r} (expected X.Y.Z)")
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    @staticmethod
    def fmt(parts: tuple[int, int, int]) -> str:
        return f"{parts[0]}.{parts[1]}.{parts[2]}"

    def current_version(self) -> str:
        text = VERSION_FILE.read_text(encoding="utf-8")
        match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if not match:
            raise ReleaseError("Could not find VERSION in version.py")
        return match.group(1)

    def bump(self, current: str, mode: str) -> str:
        major, minor, patch = self.parse_version(current)
        if mode == "major":
            return self.fmt((major + 1, 0, 0))
        if mode == "minor":
            return self.fmt((major, minor + 1, 0))
        return self.fmt((major, minor, patch + 1))

    def check_git_state(self) -> None:
        branch = self._run(["git", "branch", "--show-current"], read_only=True).stdout.strip()
        if branch not in ("main", "master"):
            raise ReleaseError(f"Releases must be made from main/master (current: {branch})")

        managed = {
            str(VERSION_FILE.relative_to(PROJECT_ROOT)),
            str(README_FILE.relative_to(PROJECT_ROOT)),
        }
        status = self._run(["git", "status", "--porcelain"], read_only=True).stdout.splitlines()
        unmanaged = [line for line in status if line and line[3:] not in managed]
        if unmanaged:
            raise ReleaseError("Working tree has uncommitted changes:\n" + "\n".join(unmanaged))

    def check_tag_doesnt_exist(self, version: str) -> None:
        tag = f"v{version}"
        existing = self._run(["git", "tag", "-l", tag], read_only=True).stdout.strip()
        if existing:
            raise ReleaseError(f"Tag {tag} already exists")

    def update_version_file(self, new_version: str) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        text = VERSION_FILE.read_text(encoding="utf-8")
        text = re.sub(r'^(VERSION\s*=\s*)["\'][^"\']+["\']', rf'\g<1>"{new_version}"', text, flags=re.MULTILINE)
        text = re.sub(r'^(BUILD_DATE\s*=\s*)["\'][^"\']+["\']', rf'\g<1>"{today}"', text, flags=re.MULTILINE)
        if not self.dry_run:
            VERSION_FILE.write_text(text, encoding="utf-8")
        self._changes.append(str(VERSION_FILE.relative_to(PROJECT_ROOT)))

    def update_readme(self, new_version: str) -> None:
        text = README_FILE.read_text(encoding="utf-8")
        if "**Current version:**" not in text:
            text = text.replace("# TuxCMDB\n", f"# TuxCMDB\n\n**Current version:** {new_version}\n")
        else:
            text = re.sub(r'(?m)^(\*\*Current version:\*\*\s*)\S+', rf'\g<1>{new_version}', text)
        if not self.dry_run:
            README_FILE.write_text(text, encoding="utf-8")
        self._changes.append(str(README_FILE.relative_to(PROJECT_ROOT)))

    def git_commit_tag_push(self, new_version: str) -> None:
        tag = f"v{new_version}"
        self._run(["git", "add"] + self._changes)
        self._run(["git", "commit", "-m", f"chore: release {new_version}"])
        self._run(["git", "tag", "-a", tag, "-m", f"Release {new_version}"])
        self._run(["git", "push", "origin", "HEAD"])
        self._run(["git", "push", "origin", tag])
        self.ok(f"Tag {tag} pushed")

    def run(self, mode: str, explicit_version: str | None) -> None:
        current = self.current_version()
        if explicit_version:
            self.parse_version(explicit_version)
            new_version = explicit_version
        else:
            new_version = self.bump(current, mode)

        self.info(f"Current version : {current}")
        self.info(f"New version     : {new_version}")

        self.check_git_state()
        self.check_tag_doesnt_exist(new_version)
        self.update_version_file(new_version)
        self.update_readme(new_version)
        self.git_commit_tag_push(new_version)


def main() -> None:
    parser = argparse.ArgumentParser(description="tuxcmdb release manager")
    bump_group = parser.add_mutually_exclusive_group()
    bump_group.add_argument("--major", action="store_true")
    bump_group.add_argument("--minor", action="store_true")
    bump_group.add_argument("--patch", action="store_true")
    bump_group.add_argument("--version", metavar="X.Y.Z")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.major:
        mode = "major"
    elif args.minor:
        mode = "minor"
    else:
        mode = "patch"

    try:
        ReleaseManager(dry_run=args.dry_run).run(mode, args.version)
    except ReleaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
