"""Frame-tree correctness and deterministic linear-work bounds; no simulator."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from openroboto_protocol import scene_schema
from openroboto_protocol.scene_schema import SceneTask

EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples/open_scene/valid/execution/task.json"
)


def task_with_chain(length, *, reverse=False):
    task = json.loads(EXAMPLE.read_text())
    pose = task["scene"]["frames"][0]["pose_in_parent"]
    chain = [
        {
            "id": f"f_{i}",
            "parent": f"f_{i - 1}" if i else "scene",
            "pose_in_parent": deepcopy(pose),
        }
        for i in range(length)
    ]
    task["scene"]["frames"].extend(reversed(chain) if reverse else chain)
    return task


@pytest.mark.parametrize("reverse", [False, True])
def test_long_chain_has_linear_parent_reads(monkeypatch, reverse):
    # Count graph work instead of relying on wall-clock speed or a frame cap.
    reads = 0
    original = scene_schema._Frame.__getattribute__

    def counted(self, name):
        nonlocal reads
        if name == "parent":
            reads += 1
        return original(self, name)

    monkeypatch.setattr(scene_schema._Frame, "__getattribute__", counted)
    data = task_with_chain(2000, reverse=reverse)
    parsed = SceneTask.model_validate_json(json.dumps(data))
    count = len(data["scene"]["frames"])
    assert len(parsed.scene.frames) == count
    assert reads <= 2 * count + 1


@pytest.mark.parametrize("reverse", [False, True])
def test_twelve_thousand_frames_still_accept_without_a_new_limit(reverse):
    data = task_with_chain(12000, reverse=reverse)
    assert SceneTask.model_validate(data).model_dump(mode="json") == data


@pytest.mark.parametrize("fault", ["missing_parent", "self_cycle", "long_cycle"])
@pytest.mark.parametrize("reverse", [False, True])
def test_long_invalid_chain_keeps_the_same_error(fault, reverse):
    data = task_with_chain(2000, reverse=reverse)
    by_id = {f["id"]: f for f in data["scene"]["frames"]}
    if fault == "missing_parent":
        by_id["f_0"]["parent"] = "absent"
    elif fault == "self_cycle":
        by_id["f_0"]["parent"] = "f_0"
    else:
        by_id["f_0"]["parent"] = "f_1999"
    with pytest.raises(ValidationError) as exc:
        SceneTask.model_validate_json(json.dumps(data))
    assert exc.value.errors(
        include_input=False, include_context=False, include_url=False
    ) == [
        {
            "type": "value_error",
            "loc": (),
            "msg": "Value error, Frames must form a tree rooted at world",
        }
    ]


def test_shared_ancestors_and_world_roots_are_not_false_cycles():
    data = task_with_chain(500)
    pose = data["scene"]["frames"][0]["pose_in_parent"]
    data["scene"]["frames"].extend(
        {"id": f"leaf_{i}", "parent": "f_499", "pose_in_parent": deepcopy(pose)}
        for i in range(500)
    )
    data["scene"]["frames"].append(
        {"id": "world_child", "parent": "world", "pose_in_parent": deepcopy(pose)}
    )
    assert (
        SceneTask.model_validate_json(json.dumps(data)).model_dump(mode="json") == data
    )


class CountedParents(Mapping):
    """Count actual graph accesses independently from the Pydantic frame model."""

    def __init__(self, values):
        self.values = values
        self.reads = 0
        self.membership_checks = 0

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)

    def __contains__(self, key):
        self.membership_checks += 1
        return key in self.values

    def __getitem__(self, key):
        self.reads += 1
        return self.values[key]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("shape", ["chain", "shared_ancestor", "world_children"])
def test_each_graph_edge_is_read_once(shape, reverse):
    if shape == "chain":
        parents = {f"f_{i}": f"f_{i - 1}" if i else "world" for i in range(12000)}
    elif shape == "shared_ancestor":
        parents = {f"f_{i}": f"f_{i - 1}" if i else "world" for i in range(6000)}
        parents.update({f"leaf_{i}": "f_5999" for i in range(6000)})
    else:
        parents = {f"f_{i}": "world" for i in range(12000)}
    if reverse:
        parents = dict(reversed(list(parents.items())))
    counted = CountedParents(parents)
    scene_schema._validate_frame_tree(counted)
    assert counted.reads == len(parents)
    assert counted.membership_checks == len(parents)
