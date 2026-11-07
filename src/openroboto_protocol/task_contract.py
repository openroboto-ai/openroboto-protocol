"""Pure author/worker contracts for versioned task definitions.

This module checks shared data shapes and reference budgets. It performs no I/O
and does not decide physical solvability, task value or reward eligibility.
The CLI uses the same findings as the admission worker before an author pays.
Verify with ``pytest tests/test_task_contract.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ContractIssue",
    "TaskContractError",
    "checker_types_for_profile",
    "execution_profile_ref",
    "parse_public_summary",
    "reference_contract_issues",
    "task_definition_issues",
    "task_profile_catalog",
    "validate_task_document",
]

_PROFILE = {
    "id": "panda-tabletop-v1",
    "version": "1.0.0",
    "robot": "franka-panda",
    "title": "Panda tabletop: place objects in target zones",
    "description": (
        "A fixed-base Panda arm moves one or two rigid cubes into marked target "
        "zones, releases them and leaves them still. "
        "This is not container containment. "
        "Allowed coordinates do not guarantee solvability; "
        "reference replay is required."
    ),
    "source": "axis",
    "scene_manifest_sha256": (
        "2e71806da0effe9069dfb1a615c81c11e3edcac5e4c26fdd921529816d638b26"
    ),
    "assets": [
        {
            "id": "cube-60mm-v1",
            "title": "60 mm rigid cube",
            "size_m": [0.06, 0.06, 0.06],
            "mass_kg": 0.08,
        }
    ],
    "templates": ["place_in_zones"],
    "limits": {
        "max_objects": 2,
        "min_instances": 1,
        "max_instances": 20,
        "max_seed": 2147483647,
        "position_m": {"x": [0.44, 0.70], "y": [-0.10, 0.10], "z": [0.782, 0.782]},
        "yaw_rad": [0.0, 0.0],
        "minimum_center_separation_m": 0.08,
        "max_control_steps_per_trial": 500,
    },
    "physics": {
        "mujoco_version": "3.17.0",
        "gravity_m_s2": [0.0, 0.0, -9.81],
        "control_period_s": 0.2,
    },
    "success": {
        "xy_radius_m": 0.07,
        "z_tolerance_m": 0.01,
        "finger_open_min_m": 0.035,
        "max_linear_speed_m_s": 0.05,
        "max_angular_speed_rad_s": 0.2,
        "entire_object_inside_zone": True,
        "hold_control_steps": 5,
    },
}
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_ORIGINS = {"original", "derived", "imported"}


@dataclass(frozen=True)
class ContractIssue:
    """A stable rejection code, data path and self-service explanation."""

    code: str
    path: str
    message: str


class TaskContractError(ValueError):
    """Invalid shared task data; callers can translate the stable findings."""

    def __init__(self, issues: Sequence[ContractIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in self.issues))


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def execution_profile_ref() -> dict[str, str]:
    """Return a fresh immutable-version reference to the first supported profile."""
    return {
        "id": str(_PROFILE["id"]),
        "version": str(_PROFILE["version"]),
        "digest": _digest(_PROFILE),
    }


def checker_types_for_profile(profile: object) -> tuple[str, ...]:
    """Expose the wire checker vocabulary only for an exact supported reference."""
    if not isinstance(profile, Mapping) or dict(profile) != execution_profile_ref():
        return ()
    return ("TabletopZoneChecker",)


def task_profile_catalog() -> tuple[dict[str, Any], ...]:
    """Public allowed options, including the digest of their exact normative data.

    No scene bytes, reference trajectory, credential or private task is exposed.
    Changes to these options require a new profile version and reference.
    """
    profile = _copy(_PROFILE)
    profile["execution_profile"] = execution_profile_ref()
    profile["authoring_commands"] = {
        "create": "openroboto task create task-definition.json",
        "build": (
            "openroboto task build --definition task-definition.json --output ./my-task"
        ),
        "record_reference": "openroboto task record-reference ./my-task --example",
        "self_test": "openroboto task self-test ./my-task",
    }
    return (profile,)


def _issue(code: str, path: str, message: str) -> ContractIssue:
    return ContractIssue(code, path, message)


def _profile_issues(
    value: object, path: str = "execution_profile"
) -> list[ContractIssue]:
    if not isinstance(value, Mapping) or dict(value) != execution_profile_ref():
        return [
            _issue(
                "execution_profile_invalid",
                path,
                "Select the exact published profile id, version and digest.",
            )
        ]
    return []


def _position(value: object) -> bool:
    if not isinstance(value, list) or len(value) != 3:
        return False
    if not all(
        type(v) is int or (type(v) is float and math.isfinite(v)) for v in value
    ):
        return False
    x, y, z = value
    return bool(
        0.44 <= x <= 0.70 and -0.10 <= y <= 0.10 and 0.781999999 <= z <= 0.782000001
    )


def _separated(positions: Sequence[list[float]]) -> bool:
    return all(
        math.dist(left[:2], right[:2]) >= 0.08
        for i, left in enumerate(positions)
        for right in positions[i + 1 :]
    )


def task_definition_issues(value: object) -> tuple[ContractIssue, ...]:
    """Check restricted authoring data, without claiming physical success."""
    if not isinstance(value, Mapping):
        return (_issue("definition_invalid", "task_definition", "Expected an object."),)
    issues: list[ContractIssue] = []
    allowed = {"template", "objects", "goals", "instance_count", "seed"}
    if set(value) != allowed:
        issues.append(
            _issue(
                "definition_fields",
                "task_definition",
                "Use exactly template, objects, goals, instance_count and seed.",
            )
        )
    if value.get("template") != "place_in_zones":
        issues.append(
            _issue(
                "template_unsupported",
                "task_definition.template",
                "Select place_in_zones.",
            )
        )
    count, seed = value.get("instance_count"), value.get("seed")
    if type(count) is not int or not 1 <= count <= 20:
        issues.append(
            _issue(
                "instance_count_invalid",
                "task_definition.instance_count",
                "Expected an integer from 1 to 20.",
            )
        )
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        issues.append(
            _issue(
                "seed_invalid",
                "task_definition.seed",
                "Expected an integer from 0 to 2147483647.",
            )
        )
    objects = value.get("objects")
    if not isinstance(objects, list) or not 1 <= len(objects) <= 2:
        issues.append(
            _issue(
                "objects_invalid",
                "task_definition.objects",
                "Select one or two supported cubes.",
            )
        )
        objects = []
    ids: list[str] = []
    positions: list[list[float]] = []
    for i, obj in enumerate(objects):
        path = f"task_definition.objects.{i}"
        if not isinstance(obj, Mapping) or set(obj) != {"id", "asset_id", "position"}:
            issues.append(
                _issue("object_fields", path, "Use exactly id, asset_id and position.")
            )
            continue
        identifier = obj.get("id")
        if (
            not isinstance(identifier, str)
            or not _IDENTIFIER.fullmatch(identifier)
            or identifier in ids
        ):
            issues.append(
                _issue(
                    "object_id_invalid",
                    path + ".id",
                    "Use a unique lowercase identifier of at most 32 characters.",
                )
            )
        else:
            ids.append(identifier)
        if obj.get("asset_id") != "cube-60mm-v1":
            issues.append(
                _issue(
                    "asset_unsupported",
                    path + ".asset_id",
                    "Select cube-60mm-v1; custom meshes and physics are not supported.",
                )
            )
        if not _position(obj.get("position")):
            issues.append(
                _issue(
                    "position_invalid",
                    path + ".position",
                    "Use x=0.44..0.70, y=-0.10..0.10 and z=0.782 metres.",
                )
            )
        else:
            positions.append(obj["position"])
    if not _separated(positions):
        issues.append(
            _issue(
                "objects_overlap",
                "task_definition.objects",
                "Keep cube centres at least 0.08 m apart.",
            )
        )
    goals = value.get("goals")
    if not isinstance(goals, list):
        issues.append(
            _issue(
                "goals_invalid",
                "task_definition.goals",
                "Supply one goal for each object.",
            )
        )
        goals = []
    goal_ids: list[str] = []
    targets: list[list[float]] = []
    for i, goal in enumerate(goals):
        path = f"task_definition.goals.{i}"
        if not isinstance(goal, Mapping) or set(goal) != {
            "object_id",
            "target_position",
        }:
            issues.append(
                _issue(
                    "goal_fields", path, "Use exactly object_id and target_position."
                )
            )
            continue
        object_id = goal.get("object_id")
        if (
            not isinstance(object_id, str)
            or object_id not in ids
            or object_id in goal_ids
        ):
            issues.append(
                _issue(
                    "goal_object_invalid",
                    path + ".object_id",
                    "Name a declared object exactly once.",
                )
            )
        else:
            goal_ids.append(object_id)
        if not _position(goal.get("target_position")):
            issues.append(
                _issue(
                    "position_invalid",
                    path + ".target_position",
                    "Use the published tabletop position range.",
                )
            )
        else:
            targets.append(goal["target_position"])
    if set(goal_ids) != set(ids) or len(goals) != len(objects):
        issues.append(
            _issue(
                "goal_coverage",
                "task_definition.goals",
                "Every object requires exactly one goal.",
            )
        )
    if not _separated(targets):
        issues.append(
            _issue(
                "goals_overlap",
                "task_definition.goals",
                "Keep target centres at least 0.08 m apart.",
            )
        )
    return tuple(issues)


def parse_public_summary(
    value: object,
    *,
    expected_profile: object = None,
) -> dict[str, Any] | None:
    """Validate an explicitly authored public synopsis, without inferring private text.

    Missing summaries remain missing for old packages. Any supplied summary must
    describe the same supported robot/profile; its text is not copied from a task.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TaskContractError(
            [_issue("public_summary_invalid", "public_summary", "Expected an object.")]
        )
    issues: list[ContractIssue] = []
    required = {"title", "instruction", "robot", "execution_profile", "origin"}
    if not required <= set(value) or set(value) - required - {"capabilities"}:
        issues.append(
            _issue(
                "public_summary_fields",
                "public_summary",
                "Supply title, instruction, robot, execution_profile and origin; "
                "capabilities is optional.",
            )
        )
    for name, maximum in (("title", 160), ("instruction", 1000)):
        text = value.get(name)
        if not isinstance(text, str) or not text.strip() or len(text) > maximum:
            issues.append(
                _issue(
                    "public_summary_text",
                    f"public_summary.{name}",
                    f"Supply non-empty text of at most {maximum} characters.",
                )
            )
    if value.get("robot") != "franka-panda":
        issues.append(
            _issue(
                "public_summary_robot", "public_summary.robot", "Expected franka-panda."
            )
        )
    issues.extend(
        _profile_issues(
            value.get("execution_profile"), "public_summary.execution_profile"
        )
    )
    if (
        expected_profile is not None
        and value.get("execution_profile") != expected_profile
    ):
        issues.append(
            _issue(
                "public_summary_profile_mismatch",
                "public_summary.execution_profile",
                "The public synopsis must reference the task's profile.",
            )
        )
    if not isinstance(value.get("origin"), str) or value.get("origin") not in _ORIGINS:
        issues.append(
            _issue(
                "public_summary_origin",
                "public_summary.origin",
                "Select original, derived or imported.",
            )
        )
    capabilities = value.get("capabilities", [])
    if (
        not isinstance(capabilities, list)
        or len(capabilities) > 8
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 64
            for item in capabilities
        )
    ):
        issues.append(
            _issue(
                "public_summary_capabilities",
                "public_summary.capabilities",
                "Supply up to eight non-empty capability labels, "
                "at most 64 characters each.",
            )
        )
    if issues:
        raise TaskContractError(issues)
    return dict(_copy(value))


def validate_task_document(
    task: Mapping[str, Any],
    *,
    expected_execution_profile: object = None,
    require_profile: bool = False,
) -> tuple[ContractIssue, ...]:
    """Check the opt-in contract, or require it under an operator's frozen policy.

    Old documents remain readable only when no policy requires a profile. A
    partially supplied new contract is always invalid, not a legacy fallback.
    """
    active = (
        require_profile
        or expected_execution_profile is not None
        or any(
            key in task
            for key in ("execution_profile", "task_definition", "public_summary")
        )
    )
    if not active:
        return ()
    issues = _profile_issues(task.get("execution_profile"))
    if "source" in task and task["source"] != "axis":
        issues.append(
            _issue(
                "execution_profile_source",
                "source",
                "This profile requires source axis.",
            )
        )
    if (
        expected_execution_profile is not None
        and task.get("execution_profile") != expected_execution_profile
    ):
        issues.append(
            _issue(
                "execution_profile_mismatch",
                "execution_profile",
                "The task must use the operator's frozen profile.",
            )
        )
    issues.extend(task_definition_issues(task.get("task_definition")))
    if "checker" in task:
        definition = task.get("task_definition")
        goals = definition.get("goals") if isinstance(definition, Mapping) else None
        expected_checker = {"checker": {"type": "TabletopZoneChecker", "goals": goals}}
        if task["checker"] != expected_checker:
            issues.append(
                _issue(
                    "execution_profile_checker",
                    "checker",
                    "Use the profile checker with exactly the declared goals.",
                )
            )
    try:
        parse_public_summary(
            task.get("public_summary"), expected_profile=task.get("execution_profile")
        )
    except TaskContractError as exc:
        issues.extend(exc.issues)
    return tuple(issues)


def reference_contract_issues(
    reference: object,
    *,
    task_id: str,
    instance_ids: Sequence[int],
    step_limit: object,
) -> tuple[ContractIssue, ...]:
    """Validate a declared reference and its explicit inclusive action cap.

    Missing headers and extra/missing instances are not repaired. The caller
    supplies the frozen benchmark limit; booleans, strings and floats are not
    integers. Actions are only checked for presence/count, not physical success.
    """
    if not isinstance(reference, Mapping):
        return (ContractIssue("reference_invalid", "reference", "Expected an object."),)
    issues: list[ContractIssue] = []
    limit = step_limit if type(step_limit) is int and step_limit > 0 else None
    if limit is None:
        issues.append(
            ContractIssue(
                "reference_step_budget",
                "benchmark.protocol.max_control_steps_per_trial",
                "Declare an explicit positive integer control-step limit.",
            )
        )
    schema = reference.get("schema_version")
    if type(schema) is not int or schema != 1:
        issues.append(
            ContractIssue(
                "reference_schema", "reference.schema_version", "Expected integer 1."
            )
        )
    if str(reference.get("task_id")) != str(task_id):
        issues.append(
            ContractIssue(
                "reference_task_id",
                "reference.task_id",
                f"Expected task_id {task_id!r}.",
            )
        )
    instances = reference.get("instances")
    if not isinstance(instances, Mapping):
        issues.append(
            ContractIssue(
                "reference_instances", "reference.instances", "Expected an object."
            )
        )
        return tuple(issues)
    expected = {str(number) for number in instance_ids}
    extra = sorted(str(key) for key in instances if key not in expected)
    if extra:
        issues.append(
            ContractIssue(
                "reference_instance_mismatch",
                "reference.instances",
                f"Undeclared instances: {extra}.",
            )
        )
    for number in instance_ids:
        entry = instances.get(str(number))
        actions = entry.get("actions") if isinstance(entry, Mapping) else None
        path = f"reference.instances.{number}.actions"
        if not isinstance(actions, list) or not actions:
            issues.append(
                ContractIssue(
                    "reference_actions_missing", path, "Supply non-empty actions."
                )
            )
        elif limit is not None and len(actions) > limit:
            issues.append(
                ContractIssue(
                    "reference_step_budget",
                    path,
                    f"{len(actions)} actions exceed the frozen limit {limit}.",
                )
            )
    return tuple(issues)
