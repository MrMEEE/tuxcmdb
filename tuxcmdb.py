#!/usr/bin/env python3
"""Management script for TuxCMDB setup and migrations."""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
from pathlib import Path
import subprocess
import sys
import venv


BASE_DIR = Path(__file__).resolve().parent
REQUIREMENTS_FILE = BASE_DIR / "tuxcmdb" / "requirements.txt"
VENV_DIR = BASE_DIR / ".venv"
RPM_VENV_DIRS = (Path("/opt/tuxcmdb/venv"),)
PACKAGED_INSTALL_ROOTS = (Path("/opt/tuxcmdb"),)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def venv_python_path() -> Path:
    def python_from(venv_dir: Path) -> Path:
        if os.name == "nt":
            return venv_dir / "Scripts" / "python.exe"
        return venv_dir / "bin" / "python"

    for rpm_venv_dir in RPM_VENV_DIRS:
        candidate = python_from(rpm_venv_dir)
        if candidate.exists():
            return candidate

    return python_from(VENV_DIR)


def ensure_venv_and_dependencies(requirements_file: Path, modules: tuple[str, ...]) -> None:
    if BASE_DIR in PACKAGED_INSTALL_ROOTS:
        return

    venv_python = venv_python_path()

    if not venv_python.exists():
        print(f"Creating virtual environment in {VENV_DIR}")
        venv.EnvBuilder(with_pip=True).create(str(VENV_DIR))
        venv_python = venv_python_path()

    import_check = "\n".join(
        [
            "import importlib",
            *[f"importlib.import_module('{module}')" for module in modules],
        ]
    )
    check_result = subprocess.run(
        [str(venv_python), "-c", import_check],
        capture_output=True,
        text=True,
    )

    if check_result.returncode != 0:
        if not requirements_file.exists():
            raise FileNotFoundError(f"Requirements file not found: {requirements_file}")
        print(f"Installing dependencies from {requirements_file}")
        subprocess.check_call(
            [str(venv_python), "-m", "pip", "install", "-r", str(requirements_file)]
        )

    current_python = Path(sys.executable).resolve()
    if current_python != venv_python.resolve():
        os.execv(
            str(venv_python),
            [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        )


ensure_venv_and_dependencies(
    REQUIREMENTS_FILE,
    ("alembic.command", "sqlalchemy", "yaml"),
)

# Avoid importing the local ./alembic directory as the Alembic package.
script_path_entry = str(BASE_DIR.resolve())
if "" in sys.path:
    sys.path.remove("")
if script_path_entry in sys.path:
    sys.path.remove(script_path_entry)

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL
import yaml

sys.path.insert(0, script_path_entry)

from tuxcmdb.db import create_db_engine


DEFAULT_CONFIG_FILE = BASE_DIR / "conf" / "database.yaml"
DEFAULT_ATTRIBUTES: tuple[tuple[str, str, str], ...] = (
    ("ip_address", "string", "Primary IP address for the asset"),
    ("vmware_uuid", "string", "VMware UUID for virtual machine identification"),
    ("environment", "string", "Environment tag such as production, test, or development"),
    ("cpus", "integer", "Number of CPU cores assigned to the asset"),
    ("memory_gb", "numeric", "Amount of memory assigned to the asset in gigabytes"),
)


def normalize_backend(value: str) -> str:
    backend = value.strip().lower()
    aliases = {
        "postgres": "postgresql",
        "postgresql": "postgresql",
        "pgsql": "postgresql",
        "mysql": "mysql",
        "sqlite": "sqlite",
        "mariadb": "mysql",
        "maria": "mysql",
    }
    if backend not in aliases:
        raise ValueError(f"Unsupported backend '{value}'. Use sqlite, mysql, or postgres.")
    return aliases[backend]


def default_port(backend: str) -> int | None:
    if backend == "postgresql":
        return 5432
    if backend == "mysql":
        return 3306
    return None


def admin_database_name(backend: str) -> str:
    if backend == "postgresql":
        return "postgres"
    if backend == "mysql":
        return "mysql"
    return ""


def is_admin_like_user(username: str) -> bool:
    user = username.strip().lower()
    if user in {"root", "admin", "postgres"}:
        return True
    if user.endswith("admin"):
        return True
    return False


def sqlite_url(database: str) -> str:
    if database == ":memory:":
        return "sqlite+pysqlite:///:memory:"

    db_path = Path(database).expanduser()
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{db_path}"


def mysql_drivername() -> str:
    try:
        import MySQLdb  # noqa: F401

        return "mysql+mysqldb"
    except Exception:
        return "mysql+pymysql"


def server_url(backend: str, host: str, port: int | None, username: str, password: str, database: str) -> str:
    drivername = "postgresql+psycopg" if backend == "postgresql" else mysql_drivername()
    return URL.create(
        drivername=drivername,
        username=username,
        password=password,
        host=host,
        port=port or default_port(backend),
        database=database,
    ).render_as_string(hide_password=False)


def mysql_socket_url(username: str, password: str, database: str, unix_socket: str) -> str:
    return URL.create(
        drivername=mysql_drivername(),
        username=username,
        password=password,
        host=None,
        database=database,
        query={"unix_socket": unix_socket},
    ).render_as_string(hide_password=False)


def mysql_connection_candidates(
    host: str,
    port: int | None,
    username: str,
    password: str,
    database: str,
    unix_socket: str | None,
) -> list[str]:
    candidates = [server_url("mysql", host, port, username, password, database)]
    if host.strip().lower() not in LOCAL_HOSTS:
        return candidates

    socket_paths: list[Path] = []
    if unix_socket:
        socket_paths.append(Path(unix_socket).expanduser())

    for probe_command in (("mysql_config", "--socket"), ("mariadb_config", "--socket")):
        probe = shutil.which(probe_command[0])
        if not probe:
            continue
        try:
            probe_result = subprocess.run(
                [probe, probe_command[1]],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:  # pragma: no cover
            continue
        probe_socket = probe_result.stdout.strip() or probe_result.stderr.strip()
        if probe_socket:
            socket_paths.append(Path(probe_socket).expanduser())

    for env_var in ("MARIADB_UNIX_SOCKET", "MYSQL_UNIX_SOCKET"):
        env_socket = os.getenv(env_var)
        if env_socket:
            socket_paths.append(Path(env_socket).expanduser())

    socket_paths.extend(
        [
            Path("/run/mariadb/mariadb.sock"),
            Path("/var/run/mariadb/mariadb.sock"),
            Path("/run/mysqld/mysqld.sock"),
            Path("/var/run/mysqld/mysqld.sock"),
            Path("/var/lib/mysql/mysql.sock"),
            Path("/tmp/mysql.sock"),
        ]
    )

    seen_paths: set[str] = set()
    for socket_path in socket_paths:
        resolved_path = str(socket_path)
        if resolved_path in seen_paths or not socket_path.exists():
            continue
        seen_paths.add(resolved_path)
        candidates.insert(0, mysql_socket_url(username, password, database, resolved_path))

    return candidates


def write_database_config(config_file: Path, payload: dict[str, object]) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    doc = {"database": payload}
    with config_file.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)


def load_database_url(config_file: Path) -> str | None:
    env_value = os.getenv("DATABASE_URL")
    if env_value:
        return env_value

    if not config_file.exists():
        return None

    with config_file.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    if not isinstance(data, dict):
        return None

    db_config = data.get("database")
    if not isinstance(db_config, dict):
        return None

    url = db_config.get("url")
    return str(url) if isinstance(url, str) and url else None


def run_migrations(database_url: str) -> None:
    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BASE_DIR / "alembic"))
    # Pass the URL via the DATABASE_URL env var rather than through
    # set_main_option, because configparser interpolation chokes on
    # percent-encoded characters (e.g. %2F in a unix_socket path).
    # env.py's get_url() checks DATABASE_URL first.
    os.environ["DATABASE_URL"] = database_url
    command.upgrade(config, "head")


def ensure_connection(database_urls: list[str]) -> str:
    failures: list[tuple[str, Exception]] = []
    for database_url in database_urls:
        try:
            engine = create_db_engine(database_url)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return database_url
        except Exception as exc:  # pragma: no cover
            failures.append((database_url, exc))

    if failures:
        if len(failures) == 1:
            raise failures[0][1]
        details = "\n".join(f"- {database_url}: {exc}" for database_url, exc in failures)
        raise RuntimeError(f"Unable to connect to any database URL candidate:\n{details}") from failures[-1][1]
    raise RuntimeError("No database URLs were provided")


def seed_default_attributes(database_url: str) -> None:
    engine = create_db_engine(database_url)
    with engine.begin() as conn:
        for name, data_type, description in DEFAULT_ATTRIBUTES:
            existing_row = conn.execute(
                text("SELECT id, description FROM attributes WHERE name = :name"),
                {"name": name},
            ).one_or_none()
            if existing_row is None:
                conn.execute(
                    text(
                        "INSERT INTO attributes (name, data_type, description) "
                        "VALUES (:name, :data_type, :description)"
                    ),
                    {"name": name, "data_type": data_type, "description": description},
                )
                continue
            if existing_row.description is None or existing_row.description == "":
                conn.execute(
                    text(
                        "UPDATE attributes "
                        "SET description = :description "
                        "WHERE name = :name"
                    ),
                    {"name": name, "description": description},
                )


def test_database_connection(database_url: str) -> str:
    engine = create_db_engine(database_url)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT USER(), CURRENT_USER()"))
        current_user, current_grant = row.one()
    return f"Connected as {current_user} (effective grant {current_grant})"


def ensure_database_exists_mysql(admin_url: str, database_name: str) -> None:
    engine = create_db_engine(admin_url, isolation_level="AUTOCOMMIT")
    safe_name = database_name.replace("`", "``")
    create_sql = f"CREATE DATABASE IF NOT EXISTS `{safe_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    with engine.connect() as conn:
        conn.execute(text(create_sql))


def ensure_database_exists_postgresql(admin_url: str, database_name: str) -> None:
    engine = create_db_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
            {"database_name": database_name},
        ).scalar_one_or_none()
        if exists is not None:
            return

        preparer = conn.dialect.identifier_preparer
        quoted_name = preparer.quote(database_name)
        conn.execute(text(f"CREATE DATABASE {quoted_name}"))


def command_setup(args: argparse.Namespace) -> int:
    backend = normalize_backend(args.backend)
    config_file = Path(args.config_file).expanduser()

    if backend == "sqlite":
        database_name = args.database or "tuxcmdb.db"
        database_url = sqlite_url(database_name)
        ensure_connection([database_url])

        if not args.skip_migrate:
            run_migrations(database_url)
            seed_default_attributes(database_url)
            print("Migration complete")

        write_database_config(
            config_file,
            {
                "backend": "sqlite",
                "database": database_name,
                "url": database_url,
            },
        )
        print(f"Saved database config to {config_file}")
        return 0

    if not args.host:
        print("Error: --host is required for postgres and mysql", file=sys.stderr)
        return 2
    if not args.username:
        print("Error: --username is required for postgres and mysql", file=sys.stderr)
        return 2

    password = args.password
    if password is None:
        password = getpass.getpass("Database password: ")

    target_database = args.database
    if not target_database:
        if is_admin_like_user(args.username):
            target_database = "tuxcmdb"
            print("No database name supplied. Using 'tuxcmdb' and attempting database creation with admin user.")
        else:
            print(
                "Error: --database is required unless an admin/root-style username is used.",
                file=sys.stderr,
            )
            return 2

    if is_admin_like_user(args.username):
        admin_db = args.admin_database or admin_database_name(backend)
        if backend == "postgresql":
            admin_url = server_url(backend, args.host, args.port, args.username, password, admin_db)
            ensure_database_exists_postgresql(admin_url, target_database)
        else:
            admin_connection_candidates = mysql_connection_candidates(
                args.host,
                args.port,
                args.username,
                password,
                admin_db,
                getattr(args, "unix_socket", None),
            )
            admin_url = ensure_connection(admin_connection_candidates)
            ensure_database_exists_mysql(admin_url, target_database)

    effective_port = args.port or default_port(backend)
    database_url = server_url(backend, args.host, args.port, args.username, password, target_database)
    connection_candidates = [database_url]
    if backend == "mysql":
        connection_candidates = mysql_connection_candidates(
            args.host,
            args.port,
            args.username,
            password,
            target_database,
            getattr(args, "unix_socket", None),
        )
    database_url = ensure_connection(connection_candidates)

    if not args.skip_migrate:
        run_migrations(database_url)
        seed_default_attributes(database_url)
        print("Migration complete")

    write_database_config(
        config_file,
        {
            "backend": backend,
            "host": args.host,
            "port": effective_port,
            "username": args.username,
            "database": target_database,
            "url": database_url,
        },
    )
    print(f"Saved database config to {config_file}")

    return 0


def command_migrate(args: argparse.Namespace) -> int:
    config_file = Path(args.config_file).expanduser()
    database_url = args.url or load_database_url(config_file)
    if not database_url:
        print(
            f"Error: no database URL found. Use --url or run setup first so {config_file} is created.",
            file=sys.stderr,
        )
        return 2

    run_migrations(database_url)
    seed_default_attributes(database_url)
    print("Migration complete")
    return 0


def command_test(args: argparse.Namespace) -> int:
    backend = normalize_backend(args.backend)

    if backend == "sqlite":
        database_name = args.database or "tuxcmdb.db"
        database_url = sqlite_url(database_name)
        ensure_connection([database_url])
        print(test_database_connection(database_url))
        return 0

    if not args.host:
        print("Error: --host is required for postgres and mysql", file=sys.stderr)
        return 2
    if not args.username:
        print("Error: --username is required for postgres and mysql", file=sys.stderr)
        return 2

    password = args.password
    if password is None:
        password = getpass.getpass("Database password: ")

    database_name = args.database or admin_database_name(backend)
    if backend == "mysql":
        connection_candidates = mysql_connection_candidates(
            args.host,
            args.port,
            args.username,
            password,
            database_name,
            getattr(args, "unix_socket", None),
        )
    else:
        connection_candidates = [server_url(backend, args.host, args.port, args.username, password, database_name)]

    database_url = ensure_connection(connection_candidates)
    print(f"Authenticated using {database_url}")
    print(test_database_connection(database_url))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TuxCMDB manage script")
    subparsers = parser.add_subparsers(dest="command")

    setup = subparsers.add_parser("setup", help="Configure database connection and initialize schema")
    setup.add_argument(
        "--backend",
        required=True,
        choices=["sqlite", "mysql", "postgres"],
        help="Database backend",
    )
    setup.add_argument("--host", help="Database host (required for postgres and mysql)")
    setup.add_argument("--port", type=int, help="Database port")
    setup.add_argument("--username", help="Database username (required for postgres and mysql)")
    setup.add_argument("--password", help="Database password. If omitted, prompt securely")
    setup.add_argument(
        "--unix-socket",
        help="MySQL/MariaDB Unix socket path to try before TCP when connecting to localhost",
    )
    setup.add_argument(
        "--database",
        help="Target database name. For sqlite, this is the db file path. Optional for admin/root users.",
    )
    setup.add_argument(
        "--admin-database",
        help="Administrative database to connect to when creating a target db (defaults: postgres/mysql)",
    )
    setup.add_argument(
        "--config-file",
        default=str(DEFAULT_CONFIG_FILE),
        help="Path to YAML config file where database settings are stored",
    )
    setup.add_argument(
        "--skip-migrate",
        action="store_true",
        help="Only configure and test database access, do not run migrations",
    )
    setup.set_defaults(func=command_setup)

    migrate = subparsers.add_parser("migrate", help="Run Alembic migrations to latest version")
    migrate.add_argument("--url", help="Explicit database URL to migrate")
    migrate.add_argument(
        "--config-file",
        default=str(DEFAULT_CONFIG_FILE),
        help="Path to YAML config file used to read database URL",
    )
    migrate.set_defaults(func=command_migrate)

    test_cmd = subparsers.add_parser("test", help="Test database credentials and report the authenticated user")
    test_cmd.add_argument(
        "--backend",
        required=True,
        choices=["sqlite", "mysql", "postgres"],
        help="Database backend",
    )
    test_cmd.add_argument("--host", help="Database host (required for postgres and mysql)")
    test_cmd.add_argument("--port", type=int, help="Database port")
    test_cmd.add_argument("--username", help="Database username (required for postgres and mysql)")
    test_cmd.add_argument("--password", help="Database password. If omitted, prompt securely")
    test_cmd.add_argument(
        "--unix-socket",
        help="MySQL/MariaDB Unix socket path to try before TCP when connecting to localhost",
    )
    test_cmd.add_argument(
        "--database",
        help="Target database name. For sqlite, this is the db file path. Optional for admin/root users.",
    )
    test_cmd.set_defaults(func=command_test)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
