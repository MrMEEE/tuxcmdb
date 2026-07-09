# TuxCMDB

A simple Content Management Database for servers, switches, and other infrastructure components.

## Current Scope

This first iteration contains:

- A relational schema implemented with SQLAlchemy 2.0 models.
- Alembic migration setup and an initial migration.
- Support for SQLite, PostgreSQL, and MySQL (also used for MariaDB).
- A manage script (`tuxcmdb.py`) for setup and migrations.

## Naming Choice

Instead of `parameters`, this project uses `attributes`.

Reason: these are reusable definitions of possible fields that an asset can have (for example `ip_address`, `vmware_uuid`, `environment`, and `memory_gb`).

## Data Model

- `assets`: unique infrastructure assets (`hostname` is unique and enforced lowercase).
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

Default attribute rows are seeded automatically (if missing), with descriptions: `ip_address`, `vmware_uuid`, `environment`, `cpus`, `memory_gb`.

## Example Connection URLs

PostgreSQL:

```text
postgresql+psycopg://user:password@localhost:5432/tuxcmdb
```

MariaDB:

```text
mysql+pymysql://user:password@localhost:3306/tuxcmdb
```
