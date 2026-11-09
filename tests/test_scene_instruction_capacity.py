"""Scene-only Unicode capacity and explicit profile transitions; no renderer."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from openroboto_protocol import scene_contract as scene
from openroboto_protocol import scene_schema
from openroboto_protocol.scene_schema import SceneTask
from openroboto_protocol.tabletop_assets import canonical_digest
from openroboto_protocol.task_contract import TaskContractError

EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples/open_scene/valid/execution/task.json"
)
OLD_PROFILE = {
    "id": "panda-open-scene-v1",
    "version": "2.0.0",
    "digest": "052ede59310a10eecdaefb43584f06340bc3ad461dbca8579cdb23b8ec4ad3d5",
}


def task():
    return json.loads(EXAMPLE.read_text())


@pytest.mark.parametrize("character", ["a", "界", "🤖"])
@pytest.mark.parametrize("length", [1000, 1001, 16384, 16385])
@pytest.mark.parametrize("entry", ["python", "json"])
def test_instruction_boundaries_count_code_points(character, length, entry):
    value = task()
    value["instruction"] = character * length

    def parse():
        if entry == "json":
            return SceneTask.model_validate_json(json.dumps(value))
        return SceneTask.model_validate(value)

    if length > 16384:
        with pytest.raises(ValidationError) as exc:
            parse()
        assert any(
            e["type"] == "string_too_long" and e["loc"] == ("instruction",)
            for e in exc.value.errors()
        )
    else:
        assert parse().instruction == value["instruction"]


def test_combining_characters_are_preserved_without_normalization():
    value = task()
    value["instruction"] = "e\u0301" * 8192
    parsed = SceneTask.model_validate_json(json.dumps(value))
    assert len(parsed.instruction) == 16384
    assert len(parsed.instruction.encode("utf-8")) == 24576
    assert parsed.instruction == value["instruction"]
    value["instruction"] += "\u0301"
    with pytest.raises(ValidationError):
        SceneTask.model_validate(value)


@pytest.mark.parametrize("value", ["", " \n\t"])
def test_instruction_nonempty_rule_is_preserved(value):
    data = task()
    data["instruction"] = value
    with pytest.raises(ValidationError):
        SceneTask.model_validate(data)


def test_only_instruction_gets_the_larger_text_type():
    text = TypeAdapter(scene_schema._Text)
    assert text.validate_python("x" * 1000) == "x" * 1000
    with pytest.raises(ValidationError):
        text.validate_python("x" * 1001)
    schema = SceneTask.model_json_schema()
    assert schema["properties"]["instruction"]["maxLength"] == 16384
    for model, field in (
        ("_Definition", "resource_profile"),
        ("_Object", "body"),
        ("_ExactRef", "id"),
        ("_ExactRef", "version"),
    ):
        assert schema["$defs"][model]["properties"][field]["maxLength"] == 1000


@pytest.mark.parametrize("scope", ["environment", "object_body"])
def test_other_text_fields_still_reject_1001_characters(scope):
    data = task()
    if scope == "environment":
        node, key = data["environment"], "version"
    else:
        node, key = data["task_definition"]["objects"][0], "body"
    node[key] = "x" * 1000
    data["checker"]["checker"]["definition"] = deepcopy(data["task_definition"])
    SceneTask.model_validate(data)
    node[key] += "x"
    data["checker"]["checker"]["definition"] = deepcopy(data["task_definition"])
    with pytest.raises(ValidationError) as exc:
        SceneTask.model_validate(data)
    assert any(e["type"] == "string_too_long" for e in exc.value.errors())


def test_profile_digest_binds_the_instruction_capacity():
    assert scene.MAX_SCENE_INSTRUCTION_CHARACTERS == 16384
    profile = scene.profile_catalog()
    assert profile["version"] == "2.1.0"
    assert profile["instruction"] == {
        "max_characters": 16384,
        "length_unit": "unicode_code_points",
    }
    reference = profile.pop("execution_profile")
    assert reference["digest"] == canonical_digest(profile)
    profile["instruction"]["max_characters"] = 1000
    assert canonical_digest(profile) != reference["digest"]
    assert scene.profile_ref() != OLD_PROFILE


@pytest.mark.parametrize("location", ["task", "execution_contract", "public_summary"])
def test_new_reader_rejects_each_old_profile_reference(location):
    value = task()
    node = value if location == "task" else value[location]
    node["execution_profile"] = deepcopy(OLD_PROFILE)
    with pytest.raises(ValidationError):
        SceneTask.model_validate(value)
    assert scene.validate_task_document(value)


def test_old_profile_has_no_silent_route_or_version_alias():
    with pytest.raises(TaskContractError):
        scene.profile_document(OLD_PROFILE)
    assert scene.checker_types_for_profile(OLD_PROFILE) == ()
    conditions = task()["execution_contract"]
    conditions["execution_profile"] = OLD_PROFILE
    with pytest.raises(ValueError, match="exact scene execution profile"):
        scene.parse_execution_contract(conditions)
    value = task()
    value["execution_profile"]["version"] = OLD_PROFILE["version"]
    with pytest.raises(ValidationError):
        SceneTask.model_validate(value)
