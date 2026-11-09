"""Opt-in composed Panda contract, separate from the released v1 contract.

Authors and workers share exact assets, stage semantics, permissions and resource
references. Validation is structural, not proof of solvability or reward value.
All registered v2 capabilities remain development candidates until real physical
coverage and release approval. Verify with tests/test_tabletop_v2.py. No I/O.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from typing import Any

from .tabletop_assets import asset_catalog, canonical_digest
from .task_contract import ContractIssue

__all__ = [
    "asset_catalog",
    "example_definition",
    "profile_catalog",
    "profile_ref",
    "resource_catalog",
    "validate_definition",
    "validate_document",
    "validate_program",
]

_ID = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_PREDICATE_FIELDS = {
    "in_zone": {"object_id", "zone_id"},
    "pose": {"object_id", "quaternion"},
    "stable": {"object_id"},
    "released": {"object_id"},
    "supported_by": {"object_id", "support_id"},
    "no_contact": {"object_id", "other_id"},
    "matching": {"object_ids", "zone_ids"},
    "all": {"children"},
    "any": {"children"},
    "not": {"child"},
}


def resource_catalog() -> dict[str, dict[str, int]]:
    """Development execution guards, not certified manipulation upper bounds.

    Resource selection never grants model calls or changes an operator's frozen
    harness budget. Author and operator must explicitly agree on the same tier.
    """
    return {
        "tabletop-standard-v2": {
            "max_objects": 6,
            "max_instances": 64,
            "max_control_steps_per_trial": 500,
            "max_stages": 16,
            "max_package_bytes": 5_000_000,
        },
        "tabletop-extended-v2": {
            "max_objects": 16,
            "max_instances": 64,
            "max_control_steps_per_trial": 2000,
            "max_stages": 32,
            "max_package_bytes": 20_000_000,
        },
    }


def _profile() -> dict[str, Any]:
    return {
        "id": "panda-tabletop-v2",
        "version": "2.0.0",
        "robot": "franka-panda",
        "title": "Panda composed manipulation tasks (development)",
        "description": (
            "Controlled shapes with pose, support, process and staged goals. "
            "Candidate capabilities require per-combination physical validation."
        ),
        "source": "axis",
        "status": "development_candidate",
        "compiler_version": "openroboto-composed-compiler/1.0.0",
        "program_semantics_version": "openroboto-substep-program/1.0.0",
        "assets": asset_catalog(),
        "templates": ["composed"],
        "resources": resource_catalog(),
        "limits": {
            "position_m": {"x": [0.30, 0.85], "y": [-0.25, 0.25], "z": [0.75, 1.4]},
            "max_predicate_depth": 8,
            "max_predicate_nodes": 128,
        },
        "physics": {
            "mujoco_version": "3.17.0",
            "timestep_s": 0.002,
            "control_period_s": 0.2,
            "gravity_m_s2": [0, 0, -9.81],
        },
        "success": {
            "max_linear_speed_m_s": 0.05,
            "max_angular_speed_rad_s": 0.2,
            "finger_open_min_m": 0.035,
            "pose_tolerance_rad": math.pi / 36,
            "support_min_weight_fraction": 0.5,
            "contact_force_min_n": 0.0001,
        },
        "observations": {
            "state": [
                "image",
                "object_positions",
                "proprioception",
                "state",
                "success",
            ],
            "vision": ["image", "proprioception", "success"],
        },
        "camera": {
            "name": "camera0",
            "position_m": [0.58, -0.35, 1.8],
            "xyaxes": [1, 0, 0, 0, 0.956, 0.293],
            "vertical_fov_deg": 47,
            "image_size": {"state": [320, 180], "vision": [1280, 720]},
        },
        "semantics": {
            "sampling": "each_physics_substep",
            "time": "integer_simulation_ticks",
            "stage_dependencies": "completed_on_an_earlier_tick",
            "invariant_failure": "latched_until_reset",
            "terminal_goals": "all_leaf_goals_hold_now",
            "retrospective_stages": False,
        },
        "scene_manifest_sha256": (
            "2e71806da0effe9069dfb1a615c81c11e3edcac5e4c26fdd921529816d638b26"
        ),
    }


def profile_ref() -> dict[str, str]:
    """Exact reference to the new candidate; v1 functions remain unchanged."""
    profile = _profile()
    return {
        "id": profile["id"],
        "version": profile["version"],
        "digest": canonical_digest(profile),
    }


def profile_catalog() -> dict[str, Any]:
    """Return a fresh full profile suitable for catalog display and compilation."""
    profile = _profile()
    profile["execution_profile"] = profile_ref()
    profile["authoring_commands"] = {
        "create": "openroboto task create definition.json --profile panda-tabletop-v2",
        "build": "openroboto task build --definition definition.json --output ./task",
        "record_reference": (
            "openroboto task record-reference ./task --script ./solve.py"
        ),
        "self_test": "openroboto task self-test ./task",
    }
    return profile


def _number(value: Any, lo: float, hi: float) -> bool:
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi
        )
    except OverflowError:
        return False


def _vector(value: Any, bounds: list[tuple[float, float]]) -> bool:
    return (
        isinstance(value, list)
        and len(value) == len(bounds)
        and all(_number(v, *b) for v, b in zip(value, bounds, strict=True))
    )


def _quaternion(value: Any) -> bool:
    return _vector(value, [(-1, 1)] * 4) and abs(sum(v * v for v in value) - 1) <= 1e-8


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(_ID.fullmatch(value))
        and value not in {"franka", "table", "robot"}
        and not value.startswith(("target_zone_", "zone_"))
    )


def validate_definition(value: object) -> tuple[ContractIssue, ...]:
    """Reject unknown parameters, invalid graph/geometry references and budgets.

    These are development envelope checks. Physical initial-state collision,
    reachability, visibility and reference replay remain worker obligations.
    """
    issues: list[ContractIssue] = []

    def issue(code: str, path: str, message: str) -> None:
        issues.append(ContractIssue(code, path, message))

    if not isinstance(value, Mapping):
        return (
            ContractIssue(
                "definition_invalid", "task_definition", "Expected an object."
            ),
        )
    fields = {
        "template",
        "objects",
        "zones",
        "stages",
        "invariants",
        "seed",
        "instance_count",
        "randomization",
        "observation",
        "resource_profile",
    }
    if set(value) != fields:
        issue("definition_fields", "task_definition", f"Use exactly {sorted(fields)}.")
    if value.get("template") != "composed":
        issue("template_unsupported", "template", "Select composed for the v2 profile.")
    resources = resource_catalog()
    resource = value.get("resource_profile")
    if not isinstance(resource, str) or resource not in resources:
        issue(
            "resource_unsupported",
            "resource_profile",
            "Select an exact registered resource tier.",
        )
        resource = "tabletop-standard-v2"
    budget = resources[resource]
    if (
        type(value.get("instance_count")) is not int
        or not 1 <= value["instance_count"] <= budget["max_instances"]
    ):
        issue(
            "instance_count_invalid",
            "instance_count",
            f"Use integer 1..{budget['max_instances']}.",
        )
    if type(value.get("seed")) is not int or not 0 <= value["seed"] <= 2**31 - 1:
        issue("seed_invalid", "seed", "Use an integer seed from 0 to 2147483647.")
    if value.get("observation") not in ("state", "vision"):
        issue(
            "observation_unsupported",
            "observation",
            "Select state or vision; permissions must match.",
        )
    randomization = value.get("randomization")
    if (
        not isinstance(randomization, Mapping)
        or set(randomization) != {"xy_m", "yaw_rad"}
        or not _number(randomization.get("xy_m"), 0, 0.05)
        or not _number(randomization.get("yaw_rad"), 0, math.pi)
    ):
        issue(
            "randomization_invalid",
            "randomization",
            "Use xy_m=0..0.05 and yaw_rad=0..pi; every generated state is retained.",
        )
    assets = {a["id"]: a for a in asset_catalog()}
    objects = value.get("objects")
    if not isinstance(objects, list) or not 1 <= len(objects) <= budget["max_objects"]:
        issue(
            "objects_invalid",
            "objects",
            f"Development guard: use 1..{budget['max_objects']} objects; "
            "this is not a manipulation support claim.",
        )
        objects = []
    object_assets: dict[str, Any] = {}
    for i, obj in enumerate(objects):
        path = f"objects.{i}"
        if not isinstance(obj, Mapping) or set(obj) != {
            "id",
            "asset_id",
            "position",
            "quaternion",
        }:
            issue(
                "object_fields",
                path,
                "Use exactly id, asset_id, position and quaternion.",
            )
            continue
        name, asset = obj["id"], obj["asset_id"]
        if not _identifier(name) or name in object_assets:
            issue(
                "object_id_invalid",
                path + ".id",
                "Use a unique non-reserved lowercase identifier.",
            )
        elif isinstance(asset, str) and asset in assets:
            object_assets[name] = assets[asset]
        if not isinstance(asset, str) or asset not in assets:
            issue(
                "asset_unsupported",
                path + ".asset_id",
                "Select an exact catalog asset_id; no custom physics.",
            )
        if not _vector(obj["position"], [(0.30, 0.85), (-0.25, 0.25), (0.75, 1.4)]):
            issue(
                "position_invalid",
                path + ".position",
                "Use a finite position in the published development workspace.",
            )
        if not _quaternion(obj["quaternion"]):
            issue(
                "quaternion_invalid",
                path + ".quaternion",
                "Supply a unit xyzw quaternion; it is not silently normalized.",
            )
    zones = value.get("zones")
    if not isinstance(zones, list) or not 1 <= len(zones) <= 32:
        issue("zones_invalid", "zones", "Use 1..32 explicit marked zones.")
        zones = []
    zone_ids: set[str] = set()
    if value.get("observation") == "vision" and len(zones) > 16:
        issue(
            "visual_zone_identity",
            "zones",
            "The candidate palette has 16 unique zone labels; additional zones "
            "need a new marker contract.",
        )
    for i, zone in enumerate(zones):
        path = f"zones.{i}"
        if not isinstance(zone, Mapping) or set(zone) != {
            "id",
            "position",
            "radius_m",
            "capacity",
            "accepts",
        }:
            issue(
                "zone_fields",
                path,
                "Use exactly id, position, radius_m, capacity and accepts.",
            )
            continue
        name = zone["id"]
        if not _identifier(name) or name in zone_ids or name in object_assets:
            issue(
                "zone_id_invalid",
                path + ".id",
                "Use a unique non-reserved identifier, distinct from objects.",
            )
        else:
            zone_ids.add(name)
        if not _vector(zone["position"], [(0.30, 0.85), (-0.25, 0.25)]) or not _number(
            zone["radius_m"], 0.025, 0.20
        ):
            issue(
                "zone_geometry",
                path,
                "Use a finite XY position and radius 0.025..0.20 metres.",
            )
        if (
            type(zone["capacity"]) is not int
            or not 1 <= zone["capacity"] <= budget["max_objects"]
        ):
            issue(
                "zone_capacity",
                path + ".capacity",
                "Use an integer capacity within this resource tier.",
            )
        accepts = zone["accepts"]
        if (
            not isinstance(accepts, list)
            or not accepts
            or any(not isinstance(a, str) or a not in assets for a in accepts)
            or len(set(a for a in accepts if isinstance(a, str))) != len(accepts)
        ):
            issue(
                "zone_assets",
                path + ".accepts",
                "List unique exact asset ids allowed in this zone.",
            )
    issues.extend(
        validate_program(
            value.get("stages"),
            value.get("invariants"),
            object_symmetries={k: v["symmetry"] for k, v in object_assets.items()},
            zone_ids=zone_ids,
            max_stages=budget["max_stages"],
            max_hold_ticks=budget["max_control_steps_per_trial"] * 100,
        )
    )
    return tuple(issues)


def validate_program(
    stages: Any,
    invariants: Any,
    *,
    object_symmetries: Mapping[str, str],
    zone_ids: set[str],
    max_stages: int,
    max_hold_ticks: int,
    contact_targets: set[str] | None = None,
) -> tuple[ContractIssue, ...]:
    """Share the existing AST and stage checks with imported scene bindings.

    Object geometry and physical evaluation belong to the selected adapter. A
    binding supplies actual symmetry, never a surrogate controlled asset id.
    contact_targets only adds explicit imported-scene geom groups; omitting it
    preserves the controlled-v2 findings, order and semantics. Findings traverse
    episode invariants first, then stages in list order, with depth-first child
    predicates. Return a tuple of ContractIssue(code, path, message); never mutate
    inputs. This validator grants neither runtime support nor reward eligibility.
    """
    issues: list[ContractIssue] = []

    def issue(code: str, path: str, message: str) -> None:
        issues.append(ContractIssue(code, path, message))

    nodes = 0

    def predicate(p: Any, path: str, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 8 or nodes > 128:
            issue(
                "predicate_complexity",
                path,
                "At most 128 predicate nodes and nesting depth 8.",
            )
            return
        if (
            not isinstance(p, Mapping)
            or not isinstance(p.get("type"), str)
            or p["type"] not in _PREDICATE_FIELDS
        ):
            issue(
                "predicate_unsupported",
                path,
                "Select a registered declarative predicate; code is not accepted.",
            )
            return
        kind = p["type"]
        if set(p) != _PREDICATE_FIELDS[kind] | {"type"}:
            issue(
                "predicate_fields",
                path,
                f"Use exactly {sorted(_PREDICATE_FIELDS[kind] | {'type'})}.",
            )
            return
        if kind in ("all", "any"):
            if not isinstance(p["children"], list) or not p["children"]:
                issue("predicate_children", path, "Supply non-empty children.")
            else:
                for i, child in enumerate(p["children"]):
                    predicate(child, f"{path}.children.{i}", depth + 1)
            return
        if kind == "not":
            predicate(p["child"], path + ".child", depth + 1)
            return
        if kind == "matching":
            for field, ids in (
                ("object_ids", object_symmetries),
                ("zone_ids", zone_ids),
            ):
                names = p[field]
                if (
                    not isinstance(names, list)
                    or not names
                    or any(not isinstance(n, str) or n not in ids for n in names)
                    or len(set(n for n in names if isinstance(n, str))) != len(names)
                ):
                    issue(
                        "predicate_matching",
                        path + "." + field,
                        "Use unique declared IDs.",
                    )
            return
        name = p["object_id"]
        if not isinstance(name, str) or name not in object_symmetries:
            issue("predicate_object", path + ".object_id", "Name a declared object.")
        if kind == "in_zone" and (
            not isinstance(p["zone_id"], str) or p["zone_id"] not in zone_ids
        ):
            issue("predicate_zone", path + ".zone_id", "Name a declared zone.")
        if kind == "pose" and not _quaternion(p["quaternion"]):
            issue(
                "quaternion_invalid",
                path + ".quaternion",
                "Supply a unit xyzw quaternion.",
            )
        if (
            kind == "pose"
            and isinstance(name, str)
            and object_symmetries.get(name) == "isotropic"
        ):
            issue(
                "pose_unobservable",
                path,
                "An unmarked sphere has no distinguishable target orientation.",
            )
        if kind in ("supported_by", "no_contact"):
            field = "support_id" if kind == "supported_by" else "other_id"
            other = p[field]
            allowed = (
                set(object_symmetries)
                | (contact_targets or set())
                | {"table"}
                | ({"robot"} if kind == "no_contact" else set())
            )
            if not isinstance(other, str) or other not in allowed or other == name:
                issue(
                    "predicate_contact",
                    path + "." + field,
                    "Name another object, table, or permitted robot group.",
                )

    def predicates(items: Any, path: str) -> None:
        if not isinstance(items, list):
            issue("invariants_invalid", path, "Supply an invariant list.")
        else:
            for i, p in enumerate(items):
                predicate(p, f"{path}.{i}")

    predicates(invariants, "invariants")
    if not isinstance(stages, list) or not 1 <= len(stages) <= max_stages:
        issue("stages_invalid", "stages", f"Supply 1..{max_stages} stages.")
        stages = []
    declared: set[str] = set()
    for i, stage in enumerate(stages):
        path = f"stages.{i}"
        if not isinstance(stage, Mapping) or set(stage) != {
            "id",
            "requires",
            "goal",
            "hold_ticks",
            "invariants",
            "reject_early",
        }:
            issue(
                "stage_fields",
                path,
                "Use id, requires, goal, hold_ticks, invariants and reject_early.",
            )
            continue
        name = stage["id"]
        if not _identifier(name) or name in declared:
            issue("stage_id_invalid", path + ".id", "Supply a unique stage identifier.")
        requires = stage["requires"]
        if (
            not isinstance(requires, list)
            or any(not isinstance(n, str) or n not in declared for n in requires)
            or len(set(n for n in requires if isinstance(n, str))) != len(requires)
        ):
            issue(
                "stage_dependency",
                path + ".requires",
                "Use unique earlier stage IDs; cycles and self-dependencies "
                "are forbidden.",
            )
        if isinstance(name, str):
            declared.add(name)
        ticks = stage["hold_ticks"]
        if type(ticks) is not int or not 1 <= ticks <= max_hold_ticks:
            issue(
                "hold_budget",
                path + ".hold_ticks",
                "Use positive integer physics ticks within the frozen trial horizon.",
            )
        if type(stage["reject_early"]) is not bool:
            issue(
                "stage_early_invalid",
                path + ".reject_early",
                "Use an explicit boolean.",
            )
        predicate(stage["goal"], path + ".goal")
        predicates(stage["invariants"], path + ".invariants")
    return tuple(issues)


def validate_document(
    task: Mapping[str, Any], *, expected_execution_profile: object = None
) -> tuple[ContractIssue, ...]:
    """Validate v2 identity, definition and exact checker compilation contract."""
    issues = list(validate_definition(task.get("task_definition")))
    if task.get("execution_profile") != profile_ref():
        issues.append(
            ContractIssue(
                "execution_profile_invalid",
                "execution_profile",
                "Use the exact v2 profile reference.",
            )
        )
    if (
        expected_execution_profile is not None
        and task.get("execution_profile") != expected_execution_profile
    ):
        issues.append(
            ContractIssue(
                "execution_profile_mismatch",
                "execution_profile",
                "The operator froze another profile.",
            )
        )
    if "source" in task and task["source"] != "axis":
        issues.append(
            ContractIssue("execution_profile_source", "source", "Use source axis.")
        )
    expected = {
        "checker": {
            "type": "TabletopProgramChecker",
            "definition": task.get("task_definition"),
        }
    }
    if "checker" in task and task["checker"] != expected:
        issues.append(
            ContractIssue(
                "execution_profile_checker",
                "checker",
                "Use the exact compiled program checker.",
            )
        )
    return tuple(issues)


def example_definition() -> dict[str, Any]:
    """A small editable candidate, not an admitted or reward-qualified task."""
    return copy.deepcopy(
        {
            "template": "composed",
            "instance_count": 2,
            "seed": 227,
            "resource_profile": "tabletop-standard-v2",
            "observation": "state",
            "randomization": {"xy_m": 0.002, "yaw_rad": 0.0},
            "objects": [
                {
                    "id": "block",
                    "asset_id": "cube-40mm-v2",
                    "position": [0.48, -0.04, 0.772],
                    "quaternion": [0, 0, 0, 1],
                }
            ],
            "zones": [
                {
                    "id": "goal",
                    "position": [0.65, -0.04],
                    "radius_m": 0.07,
                    "capacity": 1,
                    "accepts": ["cube-40mm-v2"],
                }
            ],
            "invariants": [],
            "stages": [
                {
                    "id": "finish",
                    "requires": [],
                    "hold_ticks": 500,
                    "invariants": [],
                    "reject_early": False,
                    "goal": {
                        "type": "all",
                        "children": [
                            {
                                "type": "in_zone",
                                "object_id": "block",
                                "zone_id": "goal",
                            },
                            {
                                "type": "supported_by",
                                "object_id": "block",
                                "support_id": "table",
                            },
                            {"type": "stable", "object_id": "block"},
                            {"type": "released", "object_id": "block"},
                        ],
                    },
                }
            ],
        }
    )
