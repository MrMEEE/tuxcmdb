import os
from urllib.parse import unquote

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///tuxcmdb.db")


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_db_engine(database_url: str, echo: bool = False, **kwargs) -> Engine:  # type: ignore[no-untyped-def]
    parsed_url = make_url(database_url)
    if parsed_url.drivername.startswith("mysql") and parsed_url.query.get("unix_socket"):
        # Older SQLAlchemy versions may leave the unix_socket value
        # percent-encoded (e.g. %2Fvar%2F...) after make_url().  Decode it
        # manually, then pass it exclusively via connect_args so the PyMySQL
        # dialect never sees an encoded socket path in the URL query.
        raw_socket = parsed_url.query["unix_socket"]
        unix_socket = unquote(raw_socket)  # no-op when already decoded
        connect_args = dict(kwargs.get("connect_args") or {})
        connect_args["unix_socket"] = unix_socket
        kwargs["connect_args"] = connect_args
        # Strip unix_socket from the URL query; connect_args is authoritative.
        new_query = {k: v for k, v in parsed_url.query.items() if k != "unix_socket"}
        parsed_url = parsed_url.set(query=new_query)

    engine = create_engine(parsed_url, echo=echo, future=True, **kwargs)
    _enable_sqlite_foreign_keys(engine)
    return engine


def get_engine(echo: bool = False):
    return create_db_engine(get_database_url(), echo=echo)
