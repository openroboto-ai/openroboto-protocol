"""Pure v2 execution conditions shared by authoring, admission and evaluation."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from .tabletop_v2 import profile_catalog, profile_ref, resource_catalog

__all__ = ["parse_execution_contract"]


def _object(raw: Any, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != keys:
        raise ValueError(f"{name} must contain exactly {sorted(keys)}")
    return raw


def _positive(raw: Any, name: str) -> float:
    if type(raw) not in (int, float):
        raise ValueError(f"{name} must be positive and finite")
    try:
        value = float(raw)
    except OverflowError as exc:
        raise ValueError(f"{name} must be positive and finite") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


def parse_execution_contract(raw: Any) -> dict[str, Any]:
    """Normalize exact v2 conditions without granting any execution budget.

    Legacy parsers keep their version-1 branch unchanged and dispatch only
    explicit version 2 here. An exact observation/resource/profile binding is
    mandatory for reference proofs and compared agents alike.
    """
    raw = _object(
        raw,
        {
            "version",
            "harness",
            "agent",
            "permissions",
            "budget",
            "timing",
            "execution_profile",
            "observation_contract",
            "resource_profile",
        },
        "v2 execution contract",
    )
    if type(raw["version"]) is not int or raw["version"] != 2:
        raise ValueError("execution_contract.version must be 2")
    if raw["execution_profile"] != profile_ref():
        raise ValueError("v2 execution contract requires the exact composed profile")
    harness = _object(raw["harness"], {"name"}, "harness")
    if harness["name"] != "headless":
        raise ValueError("execution_contract requires the headless harness")
    agent = _object(
        raw["agent"],
        {"harness_commit", "system_prompt_digest", "tools_digest"},
        "agent",
    )
    for key, size in (
        ("harness_commit", 40),
        ("system_prompt_digest", 64),
        ("tools_digest", 64),
    ):
        if not isinstance(agent[key], str) or not re.fullmatch(
            r"[0-9a-f]{" + str(size) + "}", agent[key]
        ):
            raise ValueError(f"agent.{key} must be {size} lowercase hex characters")
    observation = raw["observation_contract"]
    options = profile_catalog()["observations"]
    if not isinstance(observation, str) or observation not in options:
        raise ValueError("v2 observation contract must be state or vision")
    sensors = options[observation]
    permissions = _object(
        raw["permissions"], {"mode", "allowed_sensors"}, "permissions"
    )
    supplied = permissions["allowed_sensors"]
    if (
        permissions["mode"] != "privileged"
        or not isinstance(supplied, list)
        or len(supplied) != len(sensors)
        or any(not isinstance(v, str) for v in supplied)
        or set(supplied) != set(sensors)
    ):
        raise ValueError(
            "v2 execution sensors must exactly match its observation contract"
        )
    resource = raw["resource_profile"]
    if not isinstance(resource, str) or resource not in resource_catalog():
        raise ValueError("unknown v2 resource profile")
    budget = _object(
        raw["budget"], {"max_api_calls", "max_steps", "wall_seconds"}, "budget"
    )
    for key in ("max_api_calls", "max_steps"):
        if type(budget[key]) is not int or budget[key] <= 0:
            raise ValueError(f"budget.{key} must be a positive integer")
    wall = _positive(budget["wall_seconds"], "budget.wall_seconds")
    timing = _object(
        raw["timing"], {"control_step_seconds", "clock_mode", "s"}, "timing"
    )
    if (
        timing["clock_mode"] != "sim"
        or type(timing["s"]) not in (int, float)
        or timing["s"] != 0
    ):
        raise ValueError("execution_contract requires clock_mode=sim and s=0")
    control = _positive(timing["control_step_seconds"], "timing.control_step_seconds")
    if (
        budget["max_steps"]
        != resource_catalog()[resource]["max_control_steps_per_trial"]
        or control != 0.2
    ):
        raise ValueError(
            "v2 resource step cap and physical control period must match exactly"
        )
    return {
        "version": 2,
        "harness": {"name": "headless"},
        "agent": dict(agent),
        "permissions": {"mode": "privileged", "allowed_sensors": sorted(sensors)},
        "budget": {
            "max_api_calls": budget["max_api_calls"],
            "max_steps": budget["max_steps"],
            "wall_seconds": wall,
        },
        "timing": {"control_step_seconds": control, "clock_mode": "sim", "s": 0.0},
        "execution_profile": profile_ref(),
        "observation_contract": observation,
        "resource_profile": resource,
    }
