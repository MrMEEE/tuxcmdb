"""Canonical definition and reconciliation of built-in default attributes.

This is the single source of truth for TuxCMDB's built-in attributes, used by
both the ``tuxcmdb migrate``/``setup`` commands and the API server startup.
Default attributes are automatically added when missing, updated when their
definition changes here, and removed if no longer listed (as long as they
have no assignments, to avoid destroying asset data).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# name, data_type, description, allow_multiple, immutable
DEFAULT_ATTRIBUTES: tuple[tuple[str, str, str, bool, bool], ...] = (
    ("ip_address", "string", "Primary IP address for the asset", True, False),
    ("vmware_uuid", "string", "VMware UUID for virtual machine identification", False, False),
    ("environment", "string", "Environment tag such as production, test, or development", False, False),
    ("cpus", "integer", "Number of CPU cores assigned to the asset", False, False),
    ("memory_gb", "numeric", "Amount of memory assigned to the asset in gigabytes", False, False),
    ("os", "string", "Detected operating system", False, True),
    ("tuxcmdb-agent-version", "string", "Version of the tuxcmdb agent installed on the asset", False, True),
)

# attribute name -> default fetch method commands (command, needs_privilege)
DEFAULT_FETCHMETHODS: dict[str, tuple[tuple[str, bool], ...]] = {
    "tuxcmdb-agent-version": (("tuxcmdb-agent --version", False),),
}

DEFAULT_ATTRIBUTE_NAMES = frozenset(name for name, *_ in DEFAULT_ATTRIBUTES)


def sync_default_attributes(conn: Connection) -> None:
    for name, data_type, description, allow_multiple, immutable in DEFAULT_ATTRIBUTES:
        existing = conn.execute(
            text(
                "SELECT id, data_type, description, allow_multiple, immutable, is_default "
                "FROM attributes WHERE name = :name"
            ),
            {"name": name},
        ).one_or_none()

        if existing is None:
            conn.execute(
                text(
                    "INSERT INTO attributes (name, data_type, description, allow_multiple, immutable, is_default) "
                    "VALUES (:name, :data_type, :description, :allow_multiple, :immutable, :is_default)"
                ),
                {
                    "name": name,
                    "data_type": data_type,
                    "description": description,
                    "allow_multiple": allow_multiple,
                    "immutable": immutable,
                    "is_default": True,
                },
            )
            attribute_id = conn.execute(
                text("SELECT id FROM attributes WHERE name = :name"), {"name": name}
            ).scalar_one()
        else:
            attribute_id = existing.id
            if (
                existing.data_type != data_type
                or (existing.description or "") != description
                or bool(existing.allow_multiple) != allow_multiple
                or bool(existing.immutable) != immutable
                or not bool(existing.is_default)
            ):
                conn.execute(
                    text(
                        "UPDATE attributes SET data_type = :data_type, description = :description, "
                        "allow_multiple = :allow_multiple, immutable = :immutable, is_default = :is_default "
                        "WHERE id = :id"
                    ),
                    {
                        "data_type": data_type,
                        "description": description,
                        "allow_multiple": allow_multiple,
                        "immutable": immutable,
                        "is_default": True,
                        "id": attribute_id,
                    },
                )

        for command, needs_privilege in DEFAULT_FETCHMETHODS.get(name, ()):
            method_exists = conn.execute(
                text(
                    "SELECT id FROM attribute_fetchmethods WHERE attribute_id = :attribute_id AND command = :command"
                ),
                {"attribute_id": attribute_id, "command": command},
            ).one_or_none()
            if method_exists is None:
                conn.execute(
                    text(
                        "INSERT INTO attribute_fetchmethods (attribute_id, command, is_default, needs_privilege) "
                        "VALUES (:attribute_id, :command, :is_default, :needs_privilege)"
                    ),
                    {
                        "attribute_id": attribute_id,
                        "command": command,
                        "is_default": True,
                        "needs_privilege": needs_privilege,
                    },
                )

    stale_rows = conn.execute(
        text("SELECT id, name FROM attributes WHERE is_default = :is_default"),
        {"is_default": True},
    ).all()
    for row in stale_rows:
        if row.name in DEFAULT_ATTRIBUTE_NAMES:
            continue
        in_use = conn.execute(
            text("SELECT 1 FROM assignments WHERE attribute_id = :attribute_id LIMIT 1"),
            {"attribute_id": row.id},
        ).one_or_none()
        if in_use is not None:
            continue
        conn.execute(text("DELETE FROM attribute_fetchmethods WHERE attribute_id = :attribute_id"), {"attribute_id": row.id})
        conn.execute(text("DELETE FROM attributes WHERE id = :id"), {"id": row.id})
