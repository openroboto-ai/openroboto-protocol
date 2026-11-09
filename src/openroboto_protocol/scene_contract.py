"""Opt-in imported-scene routing and detached submission identity, without I/O.

CLI, admission and backend share these rules. Schema parsing is lazy and uses
the existing [schemas] extra; v1/v2 discovery and parsing keep their entrypoints.
This module neither imports a scene nor certifies physical support or payment.
Verify with tests/test_scene_contract.py and tests/test_scene_schema.py.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from . import task_profiles
from .composed_execution import parse_execution_contract as parse_composed_execution
from .tabletop_assets import canonical_digest
from .tabletop_v2 import profile_catalog as composed_catalog
from .tabletop_v2 import profile_ref as composed_ref
from .task_contract import ContractIssue, TaskContractError

__all__ = [
    "DISTRIBUTION_POLICY",
    "CapabilityResponse",
    "CapabilityStatus",
    "SubmissionIdentity",
    "checker_types_for_profile",
    "parse_execution_contract",
    "parse_public_summary",
    "profile_catalog",
    "profile_document",
    "profile_ref",
    "task_profile_catalog",
    "validate_task_document",
]

# A capability response never substitutes for physical admission or rewards.
CapabilityStatus = Literal[
    "supported", "development", "unsupported", "resource_unavailable", "invalid_package"
]

# Caller authorization must also bind task ownership/lease. Models receive only
# registered tools/observations; even execution-view files are worker-only.
DISTRIBUTION_POLICY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("author", ("execution", "private", "detached_receipt")),
    ("admission_worker", ("execution", "private", "detached_receipt")),
    ("reviewer", ("execution", "private", "detached_receipt")),
    ("evaluation_worker", ("execution", "detached_receipt")),
    ("model", ()),
    ("public", ()),
)


def _profile() -> dict[str, Any]:
    base = composed_catalog()
    return {
        "id": "panda-open-scene-v1",
        "version": "2.0.0",
        "status": "development_candidate",
        "task_schema_version": 3,
        "execution_contract_version": 3,
        "robot": "franka-panda",
        "simulator": "mujoco/3.17.0",
        "program_profile": composed_ref(),
        "program_semantics_version": base["program_semantics_version"],
        "semantics": base["semantics"],
        "success": base["success"],
        "physics": base["physics"],
        "observations": base["observations"],
        "resources": base["resources"],
        "coordinates": "right_handed_z_up_metres_radians_xyzw",
        "dependency_resolution": "materialized_package_relative_only",
        "identity_version": 1,
        "binding_version": 2,
        "action_dimensions": 9,
        "scene_actuators": "forbidden",
        "geom_ownership": "explicit_disjoint_no_descendant_expansion",
        "contact_targets": "explicit_owner_subsets_overlap_permitted",
        "poses": "body_origin_world_not_geometry_or_com",
        "initial_reset": "free_pose_then_scalar_joints_then_assert_all_body_poses",
        "initial_velocities": "zero",
        "zone_frame": "world_xy",
        "zone_capacity": "matching_assignment_only",
        "camera_sizes": base["camera"]["image_size"],
    }


def profile_ref() -> dict[str, str]:
    """Freeze imported-scene semantics separately from controlled v2 assets."""
    profile = _profile()
    return {
        "id": profile["id"],
        "version": profile["version"],
        "digest": canonical_digest(profile),
    }


def profile_catalog() -> dict[str, Any]:
    """Describe a development contract; publication/support require other gates."""
    return {**_profile(), "execution_profile": profile_ref()}


def profile_document(profile: object) -> dict[str, Any]:
    """Resolve an exact scene reference or reuse the existing v1/v2 resolver."""
    if isinstance(profile, Mapping) and dict(profile) == profile_ref():
        return profile_catalog()
    return task_profiles.profile_document(profile)


def checker_types_for_profile(profile: object) -> tuple[str, ...]:
    """Reuse the program checker only for the complete exact scene reference."""
    if isinstance(profile, Mapping) and dict(profile) == profile_ref():
        return ("TabletopProgramChecker",)
    return task_profiles.checker_types_for_profile(profile)


def parse_public_summary(
    value: object, *, expected_profile: object = None
) -> dict[str, Any] | None:
    """Reuse existing public text/privacy checks with explicit scene binding."""
    if isinstance(value, Mapping) and value.get("execution_profile") == profile_ref():
        if expected_profile is not None and expected_profile != profile_ref():
            raise TaskContractError(
                (
                    ContractIssue(
                        "public_summary_profile_mismatch",
                        "public_summary.execution_profile",
                        "The synopsis must reference the task profile.",
                    ),
                )
            )
        translated = copy.deepcopy(dict(value))
        translated["execution_profile"] = composed_ref()
        parsed = task_profiles.parse_public_summary(translated)
        assert parsed is not None
        parsed["execution_profile"] = profile_ref()
        return parsed
    return task_profiles.parse_public_summary(value, expected_profile=expected_profile)


def task_profile_catalog(
    *, include_development: bool = False
) -> tuple[dict[str, Any], ...]:
    """Keep the released default; explicitly opt into both development profiles."""
    existing = task_profiles.task_profile_catalog(
        include_development=include_development
    )
    return (*existing, profile_catalog()) if include_development else existing


def parse_execution_contract(raw: Any) -> dict[str, Any]:
    """Reuse v2 execution checks, selecting the new scene profile only at v3.

    No controlled asset catalog is substituted into a scene. Only common
    harness, sensor, timing and resource rules are delegated to the v2 parser.
    """
    if (
        not isinstance(raw, Mapping)
        or type(raw.get("version")) is not int
        or raw["version"] != 3
    ):
        raise ValueError(
            "Imported scenes require explicit execution_contract.version=3"
        )
    if raw.get("execution_profile") != profile_ref():
        raise ValueError("Imported scenes require the exact scene execution profile")
    translated = copy.deepcopy(dict(raw))
    translated.update(version=2, execution_profile=composed_ref())
    parsed = parse_composed_execution(translated)
    parsed.update(version=3, execution_profile=profile_ref())
    return parsed


def validate_task_document(
    task: Mapping[str, Any],
    *,
    expected_execution_profile: object = None,
    require_profile: bool = False,
) -> tuple[ContractIssue, ...]:
    """New callers dispatch explicitly; legacy entrypoints remain untouched."""
    ref = task.get("execution_profile")
    if task.get("schema_version") not in (2, 3) and not (
        isinstance(ref, Mapping) and ref.get("id") == profile_ref()["id"]
    ):
        return task_profiles.validate_task_document(
            task,
            expected_execution_profile=expected_execution_profile,
            require_profile=require_profile,
        )
    from pydantic import ValidationError

    from .scene_schema import SceneTask

    try:
        SceneTask.model_validate(task)
    except ValidationError as exc:
        return tuple(
            ContractIssue("scene_invalid", ".".join(map(str, e["loc"])), e["msg"])
            for e in exc.errors(include_input=False, include_context=False)
        )
    if expected_execution_profile is not None and ref != expected_execution_profile:
        return (
            ContractIssue(
                "execution_profile_mismatch",
                "execution_profile",
                "The operator froze another profile.",
            ),
        )
    return ()


@dataclass(frozen=True)
class SubmissionIdentity:
    """Bind both directory digests; keep this record and signatures outside them.

    Each digest uses the unchanged package_digest rule over every regular file
    under its own root. Changing private references changes submission identity
    even when the evaluation execution view is byte-for-byte unchanged.
    """

    execution_package_digest: str
    private_package_digest: str

    def __post_init__(self) -> None:
        for value in (self.execution_package_digest, self.private_package_digest):
            if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
                raise ValueError("Package digests must be 64 lowercase hex characters")

    @property
    def digest(self) -> str:
        """Domain-separated canonical JSON SHA-256, not a replacement package hash."""
        return canonical_digest(
            {
                "domain": "openroboto-scene-submission",
                "version": 1,
                "execution_package_digest": self.execution_package_digest,
                "private_package_digest": self.private_package_digest,
            }
        )


@dataclass(frozen=True)
class CapabilityResponse:
    """A capability gate, independent of physical admission and reward status.

    'supported' requires an exact operator registry and worker attestation;
    parsing a profile or loading a mesh is insufficient. Findings are sanitized
    public codes/paths, never private checker details, credentials or reference text.
    """

    status: CapabilityStatus
    issues: tuple[ContractIssue, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {
            "supported",
            "development",
            "unsupported",
            "resource_unavailable",
            "invalid_package",
        }:
            raise ValueError("Unknown capability status")
        if self.status == "supported" and self.issues:
            raise ValueError("Supported capability cannot carry blocking findings")
        if self.status != "supported" and not self.issues:
            raise ValueError("A blocked capability requires a finding")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the shared response without inferring admission or payment."""
        return {
            "status": self.status,
            "issues": [
                {"code": i.code, "path": i.path, "message": i.message}
                for i in self.issues
            ],
        }
