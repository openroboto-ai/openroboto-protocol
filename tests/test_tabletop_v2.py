"""Risk cases for the opt-in composed task contract; no simulator required."""

from copy import deepcopy

import pytest

from openroboto_protocol.tabletop_v2 import (
    asset_catalog,
    example_definition,
    profile_ref,
    validate_definition,
)
from openroboto_protocol.task_contract import execution_profile_ref


def codes(value):
    return {issue.code for issue in validate_definition(value)}


def test_old_contract_stays_frozen_and_new_catalog_is_real_geometry():
    assert (
        execution_profile_ref()["digest"]
        == "f72932959102b72b74812a7ef68339c0ea0116b0211499f1263de025f6821066"
    )
    assets = asset_catalog()
    assert len(assets) == 10
    assert len({a["geometry_digest"] for a in assets}) == 10
    assert all(a["license"] == "Apache-2.0" and len(a["digest"]) == 64 for a in assets)
    assert {a["geometry"]["type"] for a in assets} == {
        "box",
        "sphere",
        "cylinder",
        "capsule",
        "mesh",
    }
    assets[0]["mass_kg"] = 999
    assert asset_catalog()[0]["mass_kg"] == 0.08
    assert profile_ref() != execution_profile_ref()


def test_valid_composed_definition_and_no_input_mutation():
    value = example_definition()
    before = deepcopy(value)
    assert not validate_definition(value)
    assert value == before


@pytest.mark.parametrize(
    "change,expected",
    [
        (lambda d: d.update(gravity=[0, 0, 0]), "definition_fields"),
        (lambda d: d["objects"][0].update(mass_kg=0), "object_fields"),
        (lambda d: d["objects"][0].update(asset_id="fake-sphere"), "asset_unsupported"),
        (
            lambda d: d["objects"][0].update(position=[float("nan"), 0, 0.77]),
            "position_invalid",
        ),
        (
            lambda d: d["objects"][0].update(quaternion=[0, 0, 0, 0]),
            "quaternion_invalid",
        ),
        (lambda d: d["objects"][0].update(id="franka"), "object_id_invalid"),
        (lambda d: d.update(instance_count=True), "instance_count_invalid"),
        (lambda d: d.update(seed=-1), "seed_invalid"),
        (lambda d: d.update(observation="omniscient"), "observation_unsupported"),
        (lambda d: d.update(resource_profile="unlimited"), "resource_unsupported"),
    ],
)
def test_closed_contract_rejects_bypass_and_nonfinite_inputs(change, expected):
    value = example_definition()
    change(value)
    assert expected in codes(value)


def test_stages_reject_cycles_unknown_objects_and_impossible_hold_budget():
    value = example_definition()
    value["stages"][0]["requires"] = ["finish"]
    assert "stage_dependency" in codes(value)
    value = example_definition()
    value["stages"][0]["goal"] = {
        "type": "in_zone",
        "object_id": "unknown",
        "zone_id": "goal",
    }
    assert "predicate_object" in codes(value)
    value = example_definition()
    value["stages"][0]["hold_ticks"] = 500_000
    assert "hold_budget" in codes(value)


def test_pose_stack_protection_and_temporal_goals_are_expressible():
    value = example_definition()
    value["objects"].append(
        {
            "id": "base",
            "asset_id": "cube-60mm-v2",
            "position": [0.64, 0.07, 0.782],
            "quaternion": [0, 0, 0, 1],
        }
    )
    value["stages"][0]["goal"] = {
        "type": "all",
        "children": [
            {"type": "supported_by", "object_id": "block", "support_id": "base"},
            {"type": "pose", "object_id": "block", "quaternion": [0, 0, 0, 1]},
            {"type": "stable", "object_id": "block"},
            {"type": "released", "object_id": "block"},
        ],
    }
    value["invariants"] = [
        {"type": "no_contact", "object_id": "base", "other_id": "robot"}
    ]
    value["stages"].append(
        {
            "id": "second",
            "requires": ["finish"],
            "goal": {"type": "in_zone", "object_id": "block", "zone_id": "goal"},
            "hold_ticks": 500,
            "invariants": [],
            "reject_early": False,
        }
    )
    assert not validate_definition(value)


def test_unknown_predicates_and_deep_trees_fail_closed():
    value = example_definition()
    value["invariants"] = [{"type": "python", "code": "return True"}]
    assert "predicate_unsupported" in codes(value)
    predicate = {"type": "stable", "object_id": "block"}
    for _ in range(20):
        predicate = {"type": "all", "children": [predicate]}
    value["invariants"] = [predicate]
    assert "predicate_complexity" in codes(value)
