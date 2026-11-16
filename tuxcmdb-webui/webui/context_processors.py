from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from .services import get_tuxcmdb_version


def tuxcmdb_version(request: HttpRequest) -> dict[str, Any]:
    return {"tuxcmdb_version": get_tuxcmdb_version()}
