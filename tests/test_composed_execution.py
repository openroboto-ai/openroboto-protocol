"""Exact reference/model execution conditions and rejected privilege drift."""

from copy import deepcopy

import pytest

from openroboto_protocol.composed_execution import parse_execution_contract
from openroboto_protocol.tabletop_v2 import profile_catalog, profile_ref


def contract():
    return {
        "version": 2,
        "harness": {"name": "headless"},
        "agent": {
            "harness_commit": "a" * 40,
            "system_prompt_digest": "b" * 64,
            "tools_digest": "c" * 64,
        },
        "permissions": {
            "mode": "privileged",
            "allowed_sensors": ["image", "proprioception", "success"],
        },
        "budget": {"max_api_calls": 12, "max_steps": 500, "wall_seconds": 900},
        "timing": {"control_step_seconds": 0.2, "clock_mode": "sim", "s": 0},
        "execution_profile": profile_ref(),
        "observation_contract": "vision",
        "resource_profile": "tabletop-standard-v2",
    }


def test_both_explicit_observations_and_resources_normalize_without_mutating():
    for observation in ["state", "vision"]:
        for tier, steps in [("standard", 500), ("extended", 2000)]:
            raw = contract()
            raw["observation_contract"] = observation
            raw["permissions"]["allowed_sensors"] = list(
                reversed(profile_catalog()["observations"][observation])
            )
            raw["resource_profile"] = f"tabletop-{tier}-v2"
            raw["budget"]["max_steps"] = steps
            original = deepcopy(raw)
            result = parse_execution_contract(raw)
            assert raw == original
            assert result["permissions"]["allowed_sensors"] == sorted(
                raw["permissions"]["allowed_sensors"]
            )
            assert result["timing"]["s"] == 0.0
            assert type(result["budget"]["wall_seconds"]) is float


@pytest.mark.parametrize(
    "path,value",
    [
        ("", None),
        ("", []),
        ("version", 1),
        ("version", 2.0),
        ("version", True),
        ("execution_profile", {}),
        ("harness", {}),
        ("harness.name", "sandbox"),
        ("agent.tools_digest", None),
        ("agent.harness_commit", "a" * 39),
        ("observation_contract", []),
        ("observation_contract", "other"),
        ("permissions.mode", "standard"),
        ("permissions.allowed_sensors", None),
        ("permissions.allowed_sensors", ["image"]),
        ("permissions.allowed_sensors", ["image", "proprioception", 1]),
        ("permissions.allowed_sensors", ["image", "image", "success"]),
        ("resource_profile", {}),
        ("resource_profile", "other"),
        ("budget.max_api_calls", True),
        ("budget.max_steps", 0),
        ("budget.max_steps", 501),
        ("budget.wall_seconds", True),
        ("budget.wall_seconds", 10**400),
        ("budget.wall_seconds", float("nan")),
        ("budget.wall_seconds", float("inf")),
        ("budget.wall_seconds", -1),
        ("timing.clock_mode", "wall"),
        ("timing.s", True),
        ("timing.s", 1),
        ("timing.control_step_seconds", 0),
        ("timing.control_step_seconds", 0.02),
    ],
)
def test_invalid_conditions_fail_closed(path, value):
    raw = contract()
    if not path:
        raw = value
    else:
        keys = path.split(".")
        target = raw
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    with pytest.raises(ValueError):
        parse_execution_contract(raw)
