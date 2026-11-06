"""Match reported SMBIOS UUIDs without rewriting inventory values."""

import logging
from typing import TypeVar
from uuid import UUID

T = TypeVar("T")
logger = logging.getLogger(__name__)


def match_vm_asset(vm_uuid: object, uuid_to_asset: dict[str, T]) -> T | None:
    if not isinstance(vm_uuid, str) or not vm_uuid.strip():
        return None
    value = vm_uuid.strip()
    try:
        uuid = UUID(value)
    except ValueError:
        return uuid_to_asset.get(value)

    exact = []
    swapped = []
    swapped_uuid = UUID(bytes_le=uuid.bytes)
    for reported, asset in uuid_to_asset.items():
        try:
            candidate = UUID(reported)
        except ValueError:
            continue
        if candidate == uuid:
            exact.append(asset)
        elif candidate == swapped_uuid:
            swapped.append(asset)
    # Exact identities take priority over SMBIOS mixed-endian equivalents.
    matches = exact or swapped
    if len(matches) == 1:
        return matches[0]
    if matches:
        logger.warning("Ambiguous VM UUID association for %s; leaving VM unlinked", value)
    return None
