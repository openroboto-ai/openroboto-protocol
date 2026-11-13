"""Role-free reference-panel definitions and signed manifest v2.

Acceptance of an old envelope is a consumer policy. The canonical byte
projection remains usable to verify an already accepted historical original.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Final

__all__ = [
    "PANEL_MANIFEST_FIELDS",
    "PANEL_MANIFEST_VERSION",
    "manifest_hash",
    "manifest_payload",
    "validate_panel_definition",
]

PANEL_MANIFEST_VERSION: Final = 2
PANEL_MANIFEST_FIELDS: Final = frozenset(
    {
        "version",
        "task_id",
        "run_id",
        "package_digest",
        "config_version",
        "config_digest",
        "panel",
        "settlement",
        "node_id",
        "key_id",
        "algorithm",
        "members",
        "trials_digest",
        "signature",
    }
)


def validate_panel_definition(raw: Mapping[str, Any]) -> None:
    """Validate the shared panel shape; each consumer validates member bindings."""
    if set(raw) != {"members"}:
        raise ValueError(
            "panel must contain only 'members'; use a new role-free config"
        )
    if not isinstance(raw["members"], list) or not raw["members"]:
        raise ValueError("panel.members must be a non-empty list")
    for member in raw["members"]:
        if (
            isinstance(member, Mapping)
            and {"role", "roles", "roles_as_of"} & member.keys()
        ):
            raise ValueError("panel members cannot carry role assignments")


def manifest_payload(manifest: Mapping[str, Any]) -> bytes:
    """Canonical signing bytes, excluding only the signature itself."""
    return json.dumps(
        {key: value for key, value in manifest.items() if key != "signature"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def manifest_hash(manifest: Mapping[str, Any]) -> str:
    """SHA-256 of the exact canonical signing bytes."""
    return hashlib.sha256(manifest_payload(manifest)).hexdigest()
