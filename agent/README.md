# TuxCMDB Client Agents

This folder contains starter agents for Linux and Windows.

## Linux

Agent script: `agent/linux/tuxcmdb_agent.py`

- Stores local config in `/etc/tuxcmdb-agent/config.json`
- Registers anonymously with `POST /v1/agent/register`
- Fetches approved task list via `POST /v1/agent/bootstrap`
- Reports values via `POST /v1/agent/report`
- Pass `--insecure` on first run to accept self-signed TLS certificates; the choice is persisted as `verify_ssl` in the config file

The package creates an unprivileged `tuxcmdb-agent` system user and owns `/etc/tuxcmdb-agent` with it; the systemd unit runs the agent as this user. Running `tuxcmdb-agent` manually as root still works but prints a warning, and the config file/directory are (re)owned by `tuxcmdb-agent` automatically.

Some attribute fetch commands are marked "needs privilege" in the webui. The unprivileged agent user runs these via `sudo -n`, which requires a matching NOPASSWD rule. Use the `sudo` subcommand (must be run as root) to manage these rules interactively:

```
tuxcmdb-agent sudo
```

This lists attribute commands that require privilege for the current host, shows which already have a rule installed under `/etc/sudoers.d/tuxcmdb-agent-<attribute>`, and lets you add (`a <number>`) or remove (`r <name>`) rules. Each rule is validated with `visudo -cf` before being installed. Running `sudo` as a non-root user prints an error and exits, since it cannot edit `/etc/sudoers.d`.

Package formats are published for Linux as RPMs for RHEL 8/9/10 and a generic DEB for Debian/Ubuntu.

Systemd units:

- `agent/linux/systemd/tuxcmdb-agent.service`
- `agent/linux/systemd/tuxcmdb-agent.timer`

## Windows

Agent script: `agent/windows/tuxcmdb-agent.ps1`

Suggested deployment for MVP:

1. Copy script to `C:\Program Files\TuxCMDBAgent\tuxcmdb-agent.ps1`
2. Run once interactively to register and write config to `C:\ProgramData\TuxCMDBAgent\config.json` (pass `-Insecure` to accept self-signed TLS certificates; stored as `verify_ssl` in the config file)
3. Create a Scheduled Task (every 15 min):

```powershell
$script = 'C:\Program Files\TuxCMDBAgent\tuxcmdb-agent.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName 'TuxCMDB Agent' -Action $action -Trigger $trigger -RunLevel Highest -Force
```

For a hardened production rollout, move credentials to DPAPI-protected storage or the Windows Credential Manager instead of plain JSON.

## Downloading agents from the WebUI

The `tuxcmdb-webui-agents` package bundles the Debian/Ubuntu, RHEL 8/9/10 agent packages, and the Windows PowerShell script into `/opt/tuxcmdb-webui-agents`. When installed alongside `tuxcmdb-webui`, these files become downloadable from the **Agents** page (`/agents/`) in the web interface. If the package is not installed, the page reports that no agent downloads are available.

