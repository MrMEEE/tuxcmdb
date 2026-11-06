# TuxCMDB Client Agents

This folder contains starter agents for Linux and Windows.

## Linux

Standard agent script: `agent/linux/tuxcmdb_agent.py` (Python 3.9+)

EL8 compatibility script: `agent/linux/el8/tuxcmdb_agent.py` (Python 3.6; deprecated)

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
The Linux agent normally requires Python 3.9 or newer. The EL8 RPM temporarily supports the system Python 3 runtime and depends on the distribution's `python3-requests` package. This EL8 compatibility path is deprecated and will be removed in a future release; use Python 3.9 or newer on EL8 where available.

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

### Report validation

The agent submits collected values in one request. The API validates each value
independently: invalid datatype values and unknown attributes are rejected without
discarding other valid updates. An invalid value does not remove the attribute's
previous assignment. Successful authenticated reports update the last-checkin
time even if all submitted values are rejected.

The HTTP 200 response retains `status` and `message` and includes `updated_count`,
`unchanged_count`, `rejected_count`, and `errors` (zero-based input `index`,
`attribute_name`, and `reason`). `status` is `partial` when any values are rejected,
otherwise `ok`. Rejections are also recorded in the API warning log and the
`agent_report` audit entry, without logging submitted values. Existing agents
can use this behavior without an update, but do not display the rejection details.
The WebUI marks known rejected attributes with an **Invalid result** badge,
preserving their last valid values. Attributes with no valid assignment show
**No valid value**. Status is persisted separately from assignment history and
cleared by the next valid result for that attribute, even if its value is
unchanged. Attributes omitted from later reports retain their status. Badges
appear on asset details and selected attribute columns in the inventory list.
Unknown attribute names are reported as rejections but cannot receive a badge
until an attribute definition exists.
Authentication, asset approval/decommissioning checks, malformed request bodies,
and unexpected server/database errors still fail the request as a whole.

### Remote Linux installation

Signed-in users with write access can use **Agents → Install agent** above the
downloads list. Supply up to 50 hostnames or IPv4/IPv6 addresses (comma/space/newline
separated), an SSH username/password, and the server's API hostname or URL.
The default is the current WebUI server; OpenShift uses its separate API Route
when `TUXCMDB_AGENT_INSTALL_SERVER_URL` is configured. A bare hostname uses
HTTPS. Targets must reach the API address and their package repositories.
Short hostnames and fully qualified DNS names are supported, and names are
resolved from the WebUI server/pod, not from your browser. Duplicate names are
compared case-insensitively; a trailing DNS dot is optional.

The WebUI server connects over SSH port 22, detects EL8/9/10 or Debian/Ubuntu,
uploads the corresponding currently published package over SFTP, verifies its
checksum, installs dependencies with dnf/apt, and registers the agent as the
non-privileged `tuxcmdb-agent` account created by the package. It then enables
and starts `tuxcmdb-agent.timer`. systemd is required. Unsupported distributions
fail explicitly; Windows installation over SSH is not supported.

Use root SSH login or check **Use sudo**. Sudo uses the SSH password and also
supports passwordless sudo; sudo policies requiring a TTY are not supported.
**Ignore SSL verification** disables HTTPS certificate verification for this
agent's registration and future requests. It does not disable SSH host-key
checks. First-seen SSH keys are trusted and recorded; changed keys are rejected.
Verify host identity independently on first use, and manage changed keys manually.

An on-page modal shows live per-host steps: SSH, OS detection, transfer, installation,
account verification, registration, timer activation and cleanup. Hosts are
processed sequentially; one host's failure does not stop the next. Only one
installation batch can run at a time. Progress is visible only to its initiating
user, and polling resumes on page refresh in the same browser tab.
Closing the modal does not cancel the installation; use **View installation
progress** to reopen it. Validation, request failures and download errors appear
in modals without leaving the Agents page. If a status request fails, use
**Retry status** before starting another batch.

Credentials are used in worker memory only, never persisted in progress records,
shell commands or logs. Browser-side passwords are cleared after successful
submission. Progress and SSH known-host keys persist under
`TUXCMDB_AGENT_INSTALL_DATA_DIR` (default `tuxcmdb-webui/.agent-installs`).
Keep this directory private/writable by the WebUI service account and never
serve it as static files. Install progress records can be removed manually after
a job has finished. The single WebUI process hosts the background worker; a
WebUI restart interrupts deployment and the status page reports interruption.
Inspect partially installed hosts before retrying.

Matching existing registrations are reused. An existing configuration pointing
at another API server or TLS policy is not overwritten; reconcile it manually
before retrying. Registration does not approve an asset automatically. Package
installation is not rolled back if a later step fails.

The `tuxcmdb-webui-agents` package bundles the Debian/Ubuntu, RHEL 8/9/10 agent packages, and the Windows PowerShell script into `/opt/tuxcmdb-webui-agents`. When installed alongside `tuxcmdb-webui`, these files become downloadable from the **Agents** page (`/agents/`) in the web interface. If the package is not installed, the page reports that no agent downloads are available.

On OpenShift, the optional [agent build template](../deploy/openshift/agents.yaml)
builds the same downloads at runtime in distribution-specific Jobs and publishes
them to a shared RWX volume through a separate updater pod. The WebUI mounts
that volume read-only, so new agents require no WebUI rebuild or restart.
Reusable toolchain/updater images contain no prebuilt agents. The updater checks
Git periodically, and writable WebUI users can request builds on the Agents
page. Agent build settings control retained versions (default three).
See the [deployment guide](../deploy/openshift/README.md)
for enabling or disabling this independently of the server installation.

Each package can be downloaded by its versioned filename or a stable `latest` URL on the Agents page. For example, `/agents/download/tuxcmdb-agent-latest.el10.noarch.rpm` always downloads the newest available EL10 RPM with that filename. The Debian package uses `tuxcmdb-agent_latest.debian_all.deb`; the Windows script uses `tuxcmdb-agent-latest.ps1`. Downloads are served as attachments.
