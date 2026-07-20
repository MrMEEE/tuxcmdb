# TuxCMDB Client Agents

This folder contains starter agents for Linux and Windows.

## Linux

Agent script: `agent/linux/tuxcmdb_agent.py`

- Stores local config in `/etc/tuxcmdb-agent/config.json`
- Registers anonymously with `POST /v1/agent/register`
- Fetches approved task list via `POST /v1/agent/bootstrap`
- Reports values via `POST /v1/agent/report`

Package formats are published for Linux as RPMs for RHEL 8/9/10 and a generic DEB for Debian/Ubuntu.

Systemd units:

- `agent/linux/systemd/tuxcmdb-agent.service`
- `agent/linux/systemd/tuxcmdb-agent.timer`

## Windows

Agent script: `agent/windows/tuxcmdb-agent.ps1`

Suggested deployment for MVP:

1. Copy script to `C:\Program Files\TuxCMDBAgent\tuxcmdb-agent.ps1`
2. Run once interactively to register and write config to `C:\ProgramData\TuxCMDBAgent\config.json`
3. Create a Scheduled Task (every 15 min):

```powershell
$script = 'C:\Program Files\TuxCMDBAgent\tuxcmdb-agent.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName 'TuxCMDB Agent' -Action $action -Trigger $trigger -RunLevel Highest -Force
```

For a hardened production rollout, move credentials to DPAPI-protected storage or the Windows Credential Manager instead of plain JSON.
