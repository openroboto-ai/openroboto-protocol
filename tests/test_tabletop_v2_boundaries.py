"""Closed-schema boundaries, geometric support and explicit version dispatch."""

from copy import deepcopy

import pytest

from openroboto_protocol import tabletop_v2 as v2
from openroboto_protocol import task_contract as v1
from openroboto_protocol import task_profiles as registry
from openroboto_protocol.tabletop_assets import rotate_vector, support_local


@pytest.mark.parametrize(
    "kind,size,expected",
    [
        ("box", [0.02, 0.03, 0.04], 0.09),
        ("sphere", [0.02], 3**0.5 * 0.02),
        ("capsule", [0.02, 0.03], 3**0.5 * 0.02 + 0.03),
        ("cylinder", [0.02, 0.03], 2**0.5 * 0.02 + 0.03),
    ],
)
def test_exact_support_functions_include_all_geometric_dimensions(kind, size, expected):
    assert support_local({"type": kind, "size": size}, [1, 1, 1]) == pytest.approx(
        expected
    )
    assert rotate_vector([0, 0, 0, 1], [1, 2, 3]) == [1, 2, 3]


def test_convex_mesh_support_uses_actual_vertices():
    geometry = next(
        a["geometry"] for a in v2.asset_catalog() if a["category"] == "cone"
    )
    assert support_local(geometry, [0, 0, 1]) == 0.03
    assert support_local(geometry, [0, 0, -1]) == 0.03


def test_registry_never_silently_changes_default_v1_discovery():
    assert registry.task_profile_catalog() == v1.task_profile_catalog()
    assert len(registry.task_profile_catalog(include_development=True)) == 2
    assert (
        registry.profile_document(v1.execution_profile_ref())
        == v1.task_profile_catalog()[0]
    )
    assert registry.profile_document(v2.profile_ref()) == v2.profile_catalog()
    assert registry.checker_types_for_profile(v2.profile_ref()) == (
        "TabletopProgramChecker",
    )
    assert registry.checker_types_for_profile(v1.execution_profile_ref()) == (
        "TabletopZoneChecker",
    )
    assert not registry.is_v2(None)
    assert not registry.checker_types_for_profile({})
    with pytest.raises(v1.TaskContractError):
        registry.profile_document({})
    assert registry.validate_task_document({}) == ()
    assert registry.parse_public_summary(None) is None


def test_v2_summary_and_checker_cannot_be_bound_to_a_different_task_profile():
    summary = {
        "title": "Candidate",
        "instruction": "Do the declared task.",
        "robot": "franka-panda",
        "execution_profile": v2.profile_ref(),
        "origin": "original",
    }
    before = deepcopy(summary)
    assert (
        registry.parse_public_summary(summary, expected_profile=v2.profile_ref())
        == summary
    )
    assert summary == before
    with pytest.raises(v1.TaskContractError):
        registry.parse_public_summary(
            summary, expected_profile=v1.execution_profile_ref()
        )
    task = {
        "execution_profile": v2.profile_ref(),
        "task_definition": v2.example_definition(),
        "public_summary": summary,
        "source": "axis",
    }
    task["checker"] = {
        "checker": {
            "type": "TabletopProgramChecker",
            "definition": task["task_definition"],
        }
    }
    assert registry.validate_task_document(task, require_profile=True) == ()
    task["public_summary"] = {}
    assert "public_summary_fields" in {
        i.code for i in registry.validate_task_document(task)
    }
    task.update(
        execution_profile={
            "id": "panda-tabletop-v2",
            "version": "2.0.0",
            "digest": "0" * 64,
        },
        source="robodojo",
        checker={},
    )
    codes = {
        i.code
        for i in v2.validate_document(
            task, expected_execution_profile=v1.execution_profile_ref()
        )
    }
    assert codes == {
        "execution_profile_invalid",
        "execution_profile_mismatch",
        "execution_profile_source",
        "execution_profile_checker",
    }


@pytest.mark.parametrize(
    "field,bad",
    [
        ("template", "free_code"),
        ("objects", []),
        ("objects", [{}]),
        ("zones", []),
        ("zones", [{}]),
        ("invariants", None),
        ("stages", []),
        ("stages", [{}]),
        ("randomization", {"xy_m": 10, "yaw_rad": 0}),
        ("randomization", None),
    ],
)
def test_invalid_root_shapes_are_rejected_with_findings(field, bad):
    d = v2.example_definition()
    d[field] = bad
    assert v2.validate_definition(d)
    assert v2.validate_definition(None)


@pytest.mark.parametrize(
    "field,bad,code",
    [
        ("position", [10**999, 0, 0.78], "position_invalid"),
        ("id", "zone_reserved", "object_id_invalid"),
        ("id", "target_zone_a", "object_id_invalid"),
    ],
)
def test_object_numbers_and_names_are_not_coerced(field, bad, code):
    d = v2.example_definition()
    d["objects"][0][field] = bad
    assert code in {i.code for i in v2.validate_definition(d)}


@pytest.mark.parametrize(
    "field,bad,code",
    [
        ("id", "block", "zone_id_invalid"),
        ("position", [0, 0], "zone_geometry"),
        ("radius_m", 0, "zone_geometry"),
        ("capacity", True, "zone_capacity"),
        ("accepts", [], "zone_assets"),
        ("accepts", ["fake"], "zone_assets"),
        ("accepts", ["cube-40mm-v2", "cube-40mm-v2"], "zone_assets"),
    ],
)
def test_zone_capacity_geometry_and_assets_are_explicit(field, bad, code):
    d = v2.example_definition()
    d["zones"][0][field] = bad
    assert code in {i.code for i in v2.validate_definition(d)}


@pytest.mark.parametrize(
    "predicate,code",
    [
        (
            {"type": "stable", "object_id": "block", "threshold": 999},
            "predicate_fields",
        ),
        ({"type": "all", "children": []}, "predicate_children"),
        (
            {"type": "in_zone", "object_id": "block", "zone_id": "missing"},
            "predicate_zone",
        ),
        (
            {"type": "pose", "object_id": "block", "quaternion": [0, 0, 0, 0]},
            "quaternion_invalid",
        ),
        (
            {"type": "supported_by", "object_id": "block", "support_id": "block"},
            "predicate_contact",
        ),
        (
            {"type": "matching", "object_ids": [], "zone_ids": ["goal"]},
            "predicate_matching",
        ),
    ],
)
def test_predicates_cannot_smuggle_parameters_or_invalid_references(predicate, code):
    d = v2.example_definition()
    d["invariants"] = [predicate]
    assert code in {i.code for i in v2.validate_definition(d)}


def test_matching_not_any_and_unobservable_pose():
    d = v2.example_definition()
    d["invariants"] = [
        {
            "type": "not",
            "child": {
                "type": "any",
                "children": [
                    {"type": "matching", "object_ids": ["block"], "zone_ids": ["goal"]}
                ],
            },
        }
    ]
    assert v2.validate_definition(d) == ()
    d["objects"][0]["asset_id"] = "sphere-40mm-v2"
    d["invariants"] = [
        {"type": "pose", "object_id": "block", "quaternion": [0, 0, 0, 1]}
    ]
    assert "pose_unobservable" in {i.code for i in v2.validate_definition(d)}


def test_stage_ids_and_early_policy_are_strict():
    d = v2.example_definition()
    d["stages"][0].update(id="robot", reject_early=1)
    assert {"stage_id_invalid", "stage_early_invalid"} <= {
        i.code for i in v2.validate_definition(d)
    }
