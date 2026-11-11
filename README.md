# TuxCMDB

**Current version:** 0.3.7

A simple Content Management Database for servers, switches, and other infrastructure components.

## Current Scope

This first iteration contains:

- A relational schema implemented with SQLAlchemy 2.0 models.
- Alembic migration setup and an initial migration.
- Support for SQLite, PostgreSQL, and MySQL (also used for MariaDB).
- A manage script (`tuxcmdb.py`) for setup and migrations.
- A launcher script (`tuxcmdb-webui.py`) for starting, stopping, and restarting the Django web UI.

## Naming Choice

Instead of `parameters`, this project uses `attributes`.

Reason: these are reusable definitions of possible fields that an asset can have (for example `ip_address`, `vmware_uuid`, `environment`, and `memory_gb`).

## Data Model

- `assets`: unique infrastructure assets (`assetname` is unique and enforced lowercase).
- `attributes`: catalog of possible attributes.
- `assignments`: append-only history of value assignments for one attribute on one asset.

`assignments.assigned` acts as a soft-remove flag. Setting it to `false` keeps historical values without deleting rows.

`assignments` stores the assigned data in a single `value` field. Value formatting and type handling are done in the API layer.

## Quick Start

1. Create a virtual environment and install dependencies.
2. Install dependencies:

```bash
pip install -r tuxcmdb/requirements.txt
```

The `tuxcmdb.py` script will also auto-create `.venv` and install missing dependencies from `tuxcmdb/requirements.txt` if needed.

3. Run setup (this saves database config in `conf/database.yaml` and migrates):

SQLite:

```bash
python tuxcmdb.py setup --backend sqlite --database tuxcmdb.db
```

PostgreSQL:

```bash
python tuxcmdb.py setup --backend postgresql --host localhost --username tuxcmdb --database tuxcmdb
```

MySQL/MariaDB:

```bash
python tuxcmdb.py setup --backend mysql --host localhost --username tuxcmdb --database tuxcmdb
```

If an admin/root-style user is used (`root`, `admin`, `postgres`), `--database` is optional. The setup command will try to create `tuxcmdb`.

4. Migrate to latest version:

```bash
python tuxcmdb.py migrate
```

`migrate` reads the database URL from `conf/database.yaml` by default.

5. Manage the Django web UI from the repository root:

Start the Django web UI in the background:

```bash
python tuxcmdb-webui.py start
```

Stop it again:

```bash
python tuxcmdb-webui.py stop
```

Restart it on the configured host and port:

```bash
python tuxcmdb-webui.py restart --host 127.0.0.1 --port 8000
```

Default attribute rows are seeded automatically (if missing), with descriptions: `ip_address`, `vmware_uuid`, `environment`, `cpus`, `memory_gb`.

### Virtual machine asset matching

When editing a hypervisor cluster, leave the password blank to retain its existing
password, or enter a new password to replace it. A password is still required
when creating a cluster. The API's `PATCH /v1/hypervisors/{id}` likewise retains
the stored password when `password` is omitted, null, or empty.

Select the asset UUID attribute on the hypervisor cluster to link discovered VMs
to inventory assets. Both the cluster details and Virtual Machines pages accept
standard UUIDs and the SMBIOS mixed-endian representation (the first three UUID
fields byte-swapped). Exact UUID matches take priority, with byte-swapped matches
used only as a fallback. UUID case and formatting are normalized for comparison;
stored inventory values are unchanged. Ambiguous matches are left unlinked and
logged rather than selecting an arbitrary asset.

The Asset Inventory table loads without waiting for virtualization servers.
Its **VM Mapped** badges arrive in a separate background request. Successful
matches are cached in WebUI memory for one hour, and confirmed no-match results
for 60 seconds, scoped to the API,
user credentials and access mode; inventory itself remains uncached. API access
is revalidated on cache hits. Each WebUI worker maintains its own cache, which
is cleared on restart. Failed or unreachable hypervisor lookups are not cached;
the page displays an error with **Retry VM mappings** rather than treating a
failed lookup as an unmapped asset. Background badge loading also works after
filtering, sorting and live-page refreshes.

## OKD / OpenShift

See the [OpenShift deployment guide](deploy/openshift/README.md) for UBI 10
Source-to-Image builds of the server components, TLS routes, and a choice of
persistent MariaDB or PostgreSQL databases.

## Reverse Proxy Examples

The `tuxcmdb-webui` RPM installs disabled example reverse-proxy configs at:

- `/etc/nginx/conf.d/tuxcmdb.conf.example`
- `/etc/httpd/conf.d/tuxcmdb.conf.example`

Copy the one you want into your active web server config, update the TLS certificate paths, and reload the service.

On Apache/httpd systems, disable or replace stock default vhosts such as `/etc/httpd/conf.d/welcome.conf` and `/etc/httpd/conf.d/ssl.conf` if they are still taking precedence over the TuxCMDB proxy vhost.

## Releasing

Use the release script from the repository root:

```bash
./release.sh
```

Or call the Python tool directly:

```bash
python tools/release.py
```

## Example Connection URLs

PostgreSQL:

```text
postgresql+psycopg://user:password@localhost:5432/tuxcmdb
```

MariaDB:

```text
mysql+pymysql://user:password@localhost:3306/tuxcmdb
```
