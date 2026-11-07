"""Shared author/worker decisions for reference documents and frozen limits."""

from copy import deepcopy

import pytest

from openroboto_protocol.task_contract import (
    ContractIssue,
    TaskContractError,
    execution_profile_ref,
    parse_public_summary,
    reference_contract_issues,
    task_definition_issues,
    task_profile_catalog,
    validate_task_document,
)


def reference() -> dict:
    return {
        "schema_version": 1,
        "task_id": "task-one",
        "instances": {"0": {"actions": [[0.1] * 9, [0.2] * 9]}},
    }


def codes(value: object, limit: object = 2) -> list[str]:
    return [
        issue.code
        for issue in reference_contract_issues(
            value, task_id="task-one", instance_ids=[0], step_limit=limit
        )
    ]


def test_reference_limit_is_inclusive_and_inputs_are_not_changed() -> None:
    value = reference()
    before = deepcopy(value)
    assert codes(value) == []
    assert value == before
    assert codes(value, 1) == ["reference_step_budget"]


@pytest.mark.parametrize("limit", [None, True, 0, -1, 2.0, "2"])
def test_limit_is_an_explicit_positive_integer(limit: object) -> None:
    assert codes(reference(), limit) == ["reference_step_budget"]


def test_reference_requires_a_document() -> None:
    assert codes([]) == ["reference_invalid"]


@pytest.mark.parametrize("version", [None, True, 2, "1"])
def test_reference_schema_is_not_coerced(version: object) -> None:
    value = reference()
    value["schema_version"] = version
    assert codes(value) == ["reference_schema"]


def test_reference_task_is_bound() -> None:
    value = reference()
    value["task_id"] = "other-task"
    assert codes(value) == ["reference_task_id"]


def test_reference_instances_require_an_object() -> None:
    value = reference()
    value["instances"] = []
    assert codes(value) == ["reference_instances"]


@pytest.mark.parametrize("entry", [None, {}, {"actions": []}, {"actions": "fake"}])
def test_each_requested_instance_requires_actions(entry: object) -> None:
    value = reference()
    value["instances"]["0"] = entry
    assert codes(value) == ["reference_actions_missing"]


def test_unknown_instances_are_not_silently_accepted() -> None:
    value = reference()
    value["instances"]["extra"] = {"actions": [[0.1] * 9]}
    assert codes(value) == ["reference_instance_mismatch"]


def test_empty_expected_instances_can_only_accept_empty_mapping() -> None:
    value = reference()
    value["instances"] = {}
    assert (
        reference_contract_issues(
            value, task_id="task-one", instance_ids=[], step_limit=2
        )
        == ()
    )


def test_issue_has_explicit_field_and_message() -> None:
    issue = ContractIssue("reference_invalid", "reference", "Expected an object.")
    assert (issue.code, issue.path, issue.message) == (
        "reference_invalid",
        "reference",
        "Expected an object.",
    )


def definition() -> dict:
    return {
        "template": "place_in_zones",
        "instance_count": 2,
        "seed": 0,
        "objects": [
            {
                "id": "cube_a",
                "asset_id": "cube-60mm-v1",
                "position": [0.48, -0.08, 0.782],
            }
        ],
        "goals": [{"object_id": "cube_a", "target_position": [0.65, 0.08, 0.782]}],
    }


def synopsis() -> dict:
    return {
        "title": "Move a cube",
        "instruction": "Move the cube into its marked zone.",
        "robot": "franka-panda",
        "execution_profile": execution_profile_ref(),
        "origin": "original",
    }


def test_public_catalog_is_fresh_and_bound_to_a_complete_version() -> None:
    first = task_profile_catalog()[0]
    assert first["execution_profile"] == execution_profile_ref()
    assert len(first["execution_profile"]["digest"]) == 64
    first["assets"][0]["mass_kg"] = 999
    assert task_profile_catalog()[0]["assets"][0]["mass_kg"] == 0.08
    assert (
        execution_profile_ref()["digest"]
        == "795445828f327f9b09355c3dd344fd266ecf1e0c798e0e9c74f216e4a68d6cd6"
    )


def test_supported_definition_and_empty_legacy_document() -> None:
    assert task_definition_issues(definition()) == ()
    assert validate_task_document({"instruction": "Private legacy instruction"}) == ()
    assert parse_public_summary(None) is None
    assert (
        validate_task_document(
            {
                "execution_profile": execution_profile_ref(),
                "task_definition": definition(),
            }
        )
        == ()
    )


def test_operator_requirement_cannot_be_omitted() -> None:
    missing = validate_task_document({}, require_profile=True)
    assert {i.code for i in missing} == {
        "execution_profile_invalid",
        "definition_invalid",
    }
    mismatch = validate_task_document(
        {"execution_profile": execution_profile_ref(), "task_definition": definition()},
        expected_execution_profile={"id": "other"},
    )
    assert [i.code for i in mismatch] == ["execution_profile_mismatch"]
    assert validate_task_document({"public_summary": None})


@pytest.mark.parametrize(
    "ref", [None, [], {}, {"id": "panda-tabletop-v1", "version": 1, "digest": "0" * 64}]
)
def test_partial_or_unknown_profile_is_not_a_legacy_fallback(ref: object) -> None:
    issues = validate_task_document(
        {"execution_profile": ref, "task_definition": definition()}
    )
    assert [i.code for i in issues] == ["execution_profile_invalid"]


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("template", "contain_in_bowl", "template_unsupported"),
        ("instance_count", True, "instance_count_invalid"),
        ("instance_count", 0, "instance_count_invalid"),
        ("instance_count", 21, "instance_count_invalid"),
        ("seed", -1, "seed_invalid"),
        ("seed", True, "seed_invalid"),
        ("seed", 2147483648, "seed_invalid"),
        ("objects", [], "objects_invalid"),
        ("objects", "ultraman", "objects_invalid"),
        ("goals", "done", "goals_invalid"),
    ],
)
def test_definition_rejects_unsupported_options(
    field: str, value: object, code: str
) -> None:
    d = definition()
    d[field] = value
    assert code in [i.code for i in task_definition_issues(d)]


def test_definition_rejects_an_unknown_field_and_wrong_document() -> None:
    assert [i.code for i in task_definition_issues([])] == ["definition_invalid"]
    d = definition()
    d["gravity"] = [0, 0, 0]
    assert [i.code for i in task_definition_issues(d)] == ["definition_fields"]


@pytest.mark.parametrize(
    "position",
    [
        None,
        [0.5],
        [True, 0, 0.782],
        [float("nan"), 0, 0.782],
        [10**999, 0, 0.782],
        [0.5, 0, 10**999],
        [0.43, 0, 0.782],
        [0.5, 0.11, 0.782],
        [0.5, 0, 0.9],
    ],
)
def test_definition_positions_are_finite_and_in_the_fixed_workspace(
    position: object,
) -> None:
    d = definition()
    d["objects"][0]["position"] = position
    assert "position_invalid" in [i.code for i in task_definition_issues(d)]


@pytest.mark.parametrize(
    "obj,code",
    [
        (None, "object_fields"),
        (
            {
                "id": "a",
                "asset_id": "cube-60mm-v1",
                "position": [0.48, 0, 0.782],
                "mass": 0,
            },
            "object_fields",
        ),
        (
            {"id": "../bad", "asset_id": "cube-60mm-v1", "position": [0.48, 0, 0.782]},
            "object_id_invalid",
        ),
        (
            {"id": 1, "asset_id": "cube-60mm-v1", "position": [0.48, 0, 0.782]},
            "object_id_invalid",
        ),
        (
            {"id": "cube_a", "asset_id": "ultraman", "position": [0.48, 0, 0.782]},
            "asset_unsupported",
        ),
    ],
)
def test_objects_have_closed_fields_and_supported_assets(
    obj: object, code: str
) -> None:
    d = definition()
    d["objects"] = [obj]
    assert code in [i.code for i in task_definition_issues(d)]


def test_two_object_swap_and_overlap_rejections() -> None:
    d = definition()
    d["objects"].append(
        {"id": "cube_b", "asset_id": "cube-60mm-v1", "position": [0.65, 0.08, 0.782]}
    )
    d["goals"].append({"object_id": "cube_b", "target_position": [0.48, -0.08, 0.782]})
    assert task_definition_issues(d) == ()
    d["objects"][1]["position"] = d["objects"][0]["position"]
    assert [i.code for i in task_definition_issues(d)] == ["objects_overlap"]
    d["goals"][1]["target_position"] = d["goals"][0]["target_position"]
    assert "goals_overlap" in [i.code for i in task_definition_issues(d)]
    d["objects"][1]["id"] = "cube_a"
    assert "object_id_invalid" in [i.code for i in task_definition_issues(d)]


@pytest.mark.parametrize(
    "goal,code",
    [
        (None, "goal_fields"),
        (
            {"object_id": "other", "target_position": [0.65, 0.08, 0.782]},
            "goal_object_invalid",
        ),
        (
            {"object_id": [], "target_position": [0.65, 0.08, 0.782]},
            "goal_object_invalid",
        ),
        ({"object_id": "cube_a", "target_position": [9, 9, 9]}, "position_invalid"),
    ],
)
def test_goals_have_closed_fields_and_valid_objects(goal: object, code: str) -> None:
    d = definition()
    d["goals"] = [goal]
    assert code in [i.code for i in task_definition_issues(d)]


def test_duplicate_goal_is_rejected() -> None:
    d = definition()
    d["goals"].append(deepcopy(d["goals"][0]))
    assert "goal_object_invalid" in [i.code for i in task_definition_issues(d)]


def test_explicit_public_summary_does_not_mutate_or_publish_legacy_text() -> None:
    value = synopsis()
    parsed = parse_public_summary(value, expected_profile=execution_profile_ref())
    assert parsed == value
    parsed["title"] = "changed"
    assert value["title"] == "Move a cube"
    value["capabilities"] = ["planning"]
    assert parse_public_summary(value)["capabilities"] == ["planning"]
    assert (
        validate_task_document(
            {
                "execution_profile": execution_profile_ref(),
                "task_definition": definition(),
                "public_summary": value,
            }
        )
        == ()
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("title", "", "public_summary_text"),
        ("instruction", 5, "public_summary_text"),
        ("instruction", "x" * 1001, "public_summary_text"),
        ("robot", "humanoid", "public_summary_robot"),
        ("origin", "unknown", "public_summary_origin"),
        ("origin", [], "public_summary_origin"),
        ("execution_profile", {}, "execution_profile_invalid"),
        ("capabilities", "planning", "public_summary_capabilities"),
        ("capabilities", [""] * 9, "public_summary_capabilities"),
        ("capabilities", ["x" * 65], "public_summary_capabilities"),
        ("capabilities", [1], "public_summary_capabilities"),
    ],
)
def test_public_summary_is_bounded_and_bound(
    field: str, value: object, code: str
) -> None:
    data = synopsis()
    data[field] = value
    with pytest.raises(TaskContractError) as caught:
        parse_public_summary(data)
    assert code in [i.code for i in caught.value.issues]


def test_invalid_summary_fields_and_profile_mismatch_are_explained() -> None:
    with pytest.raises(TaskContractError) as caught:
        parse_public_summary("fake")
    assert caught.value.issues[0].code == "public_summary_invalid"
    value = synopsis()
    value["private_scene"] = "never publish this"
    with pytest.raises(TaskContractError) as caught:
        parse_public_summary(value, expected_profile={"id": "other"})
    assert {i.code for i in caught.value.issues} == {
        "public_summary_fields",
        "public_summary_profile_mismatch",
    }
    issues = validate_task_document(
        {
            "execution_profile": execution_profile_ref(),
            "task_definition": definition(),
            "public_summary": {"title": "incomplete"},
        }
    )
    assert "public_summary_fields" in [i.code for i in issues]


def test_public_surface_is_pinned() -> None:
    from openroboto_protocol import task_contract

    assert set(task_contract.__all__) == {
        "ContractIssue",
        "TaskContractError",
        "execution_profile_ref",
        "parse_public_summary",
        "reference_contract_issues",
        "task_definition_issues",
        "task_profile_catalog",
        "validate_task_document",
    }
