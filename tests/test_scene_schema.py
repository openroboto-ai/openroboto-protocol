"""Imported-scene attack cases and detached-material verification; no simulator."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from openroboto_protocol import scene_schema
from openroboto_protocol.package_digest import package_digest
from openroboto_protocol.scene_schema import (
    PrivateReferenceBinding,
    SceneAdmissionResult,
    SceneTask,
    SubmissionManifest,
    dependency_issues,
    submission_issues,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/open_scene"


def read(name):
    return json.loads((EXAMPLES / name).read_text())


def task():
    return read("valid/execution/task.json")


def synchronize_checker(value):
    value["checker"] = {
        "checker": {
            "type": "TabletopProgramChecker",
            "definition": deepcopy(value["task_definition"]),
        }
    }


def test_public_surface_is_pinned_and_json_schema_is_reproducible():
    assert scene_schema.__all__ == [
        "PrivateReferenceBinding",
        "SceneAdmissionResult",
        "SceneTask",
        "SubmissionManifest",
        "dependency_issues",
        "submission_issues",
    ]
    for name, model in (
        ("task", SceneTask),
        ("private-binding", PrivateReferenceBinding),
        ("submission", SubmissionManifest),
        ("result", SceneAdmissionResult),
    ):
        assert model.model_json_schema() == read(f"schemas/{name}.schema.json")
    data = task()
    before = deepcopy(data)
    assert SceneTask.model_validate(data).model_dump(mode="json") == data
    assert (
        SceneTask.model_validate_json(json.dumps(data)).model_dump(mode="json") == data
    )
    assert data == before


@pytest.mark.parametrize(
    "path,bad",
    [(("schema_version",), v) for v in (True, 1, 2, 3.0, "3", 4)]
    + [
        (("execution_profile", "digest"), "f" * 64),
        (("environment", "id"), "other-simulator"),
        (("environment", "version"), " "),
        (("environment", "digest"), "A" * 64),
        (("robot", "artifact", "id"), "other-robot"),
        (("robot", "control_mapping", "digest"), "a" * 64),
        (("robot", "mount", "quaternion_xyzw"), [0, 0, 0, 0]),
        (("robot", "mount", "position_m"), [float("inf"), 0, 0]),
        (("scene", "units"), "millimetres"),
        (("scene", "coordinates"), "y-up"),
        (("scene", "joints", 0, "kind"), "free"),
        (("scene", "joints", 0, "body_id"), "missing"),
        (("scene", "initial_states", 0, "objects"), []),
        (("scene", "initial_states", 0, "joints"), []),
        (("scene", "initial_states", 0, "joints", 0, "joint_id"), "missing"),
        (("scene", "initial_states", 0, "joints", 0, "position"), True),
        (("scene", "initial_states", 0, "joints", 0, "position"), float("nan")),
        (("scene", "dependencies", 0, "requires"), ["assets/missing.obj"]),
        (("scene", "dependencies", 0, "size_bytes"), True),
        (("scene", "dependencies", 0, "size_bytes"), -1),
        (("scene", "dependencies", 0, "size_bytes"), 20_000_001),
        (("scene", "dependencies", 0, "requires"), ["scene.xml"]),
        (
            ("scene", "dependencies", 0, "requires"),
            ["assets/part.obj", "assets/part.obj"],
        ),
        (("scene", "dependencies", 1, "requires"), ["scene.xml"]),
        (("scene", "dependencies", 1, "source_revision"), "main"),
        (("scene", "dependencies", 1, "license"), ""),
        (("scene", "entrypoint"), "unknown.xml"),
        (("public_summary", "execution_profile"), {}),
        (("public_summary", "title"), ""),
        (("task_definition", "objects", 0, "asset", "id"), "unknown.obj"),
        (("task_definition", "objects", 0, "id"), "robot"),
        (("task_definition", "objects", 0, "id"), "zone_reserved"),
        (("task_definition", "stages", 0, "requires"), ["opened"]),
        (
            ("task_definition", "stages", 0, "goal"),
            {"type": "joint_position", "joint_id": "door_hinge"},
        ),
        (
            ("task_definition", "stages", 0, "goal"),
            {"type": "pose", "object_id": "unknown", "quaternion": [0, 0, 0, 1]},
        ),
        (("execution_contract", "version"), 2),
        (("extra",), "no-private-payloads-here"),
    ],
)
def test_untrusted_fields_fail_closed(path, bad):
    value = task()
    node = value
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = bad
    synchronize_checker(value)
    with pytest.raises(ValidationError):
        SceneTask.model_validate(value)


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "..",
        "../mesh.obj",
        "/mesh.obj",
        "a//b",
        "a/./b",
        "a/../b",
        "https://host/file",
        "a\\b",
        "a\x00b",
        "a\nb",
    ],
)
def test_noncanonical_dependency_paths_are_rejected(path):
    value = task()
    value["scene"]["entrypoint"] = path
    with pytest.raises(ValidationError):
        SceneTask.model_validate(value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["scene"]["dependencies"].append(
            deepcopy(d["scene"]["dependencies"][0])
        ),
        lambda d: d["scene"]["dependencies"][0].update(path="task.json"),
        lambda d: d["scene"]["dependencies"].append(
            {**d["scene"]["dependencies"][1], "path": "unused.obj"}
        ),
        lambda d: d["scene"]["initial_states"].append(
            deepcopy(d["scene"]["initial_states"][0])
        ),
        lambda d: d["task_definition"]["objects"].append(
            deepcopy(d["task_definition"]["objects"][0])
        ),
        lambda d: d["scene"]["joints"].append(deepcopy(d["scene"]["joints"][0])),
        lambda d: d["task_definition"]["objects"].append(
            {**d["task_definition"]["objects"][0], "id": "another"}
        ),
        lambda d: d["task_definition"].update(
            invariants=[{"type": "python", "code": "True"}]
        ),
        lambda d: d["task_definition"]["stages"][0].update(hold_ticks=500_001),
        lambda d: d.update(checker={}),
    ],
)
def test_graph_bindings_and_checker_substitution_are_rejected(mutation):
    value = task()
    mutation(value)
    if value["checker"]:
        synchronize_checker(value)
    with pytest.raises(ValidationError):
        SceneTask.model_validate(value)


def test_self_reference_dag_diamonds_and_long_chains():
    value = task()
    value["scene"]["dependencies"][0]["path"] = "task.json"
    value["scene"]["entrypoint"] = "task.json"
    with pytest.raises(ValidationError, match="own file digest"):
        SceneTask.model_validate(value)
    value = task()
    # A shared leaf is legal, unlike a cycle. Traverse it twice without recursion.
    leaf = deepcopy(value["scene"]["dependencies"][1])
    middle = {**leaf, "path": "middle.xml", "requires": [leaf["path"]]}
    value["scene"]["dependencies"][0]["requires"].append("middle.xml")
    value["scene"]["dependencies"].append(middle)
    assert SceneTask.model_validate(value)
    value = task()
    for i in range(1200):
        path = f"include-{i}.xml"
        value["scene"]["dependencies"][-1]["requires"] = [path]
        value["scene"]["dependencies"].append({**leaf, "path": path, "requires": []})
    assert SceneTask.model_validate(value)


def test_observation_resource_and_sensor_contracts_must_match():
    value = task()
    conditions = value["execution_contract"]
    conditions["observation_contract"] = "state"
    conditions["permissions"]["allowed_sensors"] += ["object_positions", "state"]
    with pytest.raises(ValidationError, match="selections must match"):
        SceneTask.model_validate(value)
    value = task()
    value["execution_contract"].update(resource_profile="tabletop-extended-v2")
    value["execution_contract"]["budget"]["max_steps"] = 2000
    with pytest.raises(ValidationError, match="selections must match"):
        SceneTask.model_validate(value)


def zone(i=0):
    return {
        "id": f"z{i}",
        "frame": "world",
        "position": [0.5, 0],
        "radius_m": 0.07,
        "capacity": 1,
        "accepts": [task()["task_definition"]["objects"][0]["asset"]],
    }


def test_zone_acceptance_and_resource_guards():
    value = task()
    value["task_definition"]["zones"] = [zone()]
    synchronize_checker(value)
    assert SceneTask.model_validate(value)
    for change in (
        lambda d: d["task_definition"]["zones"][0].update(
            accepts=[{**zone()["accepts"][0], "id": "missing"}]
        ),
        lambda d: d["task_definition"]["zones"][0].update(
            accepts=zone()["accepts"] * 2
        ),
        lambda d: d["task_definition"]["zones"][0].update(capacity=7),
        lambda d: d["task_definition"].update(zones=[zone()] * 2),
        lambda d: d["task_definition"].update(zones=[zone(i) for i in range(17)]),
        lambda d: d["task_definition"].update(zones=[zone(i) for i in range(33)]),
        lambda d: d["task_definition"].update(
            objects=[
                {**d["task_definition"]["objects"][0], "id": f"b{i}", "body": f"b{i}"}
                for i in range(7)
            ]
        ),
        lambda d: d["scene"].update(
            initial_states=[
                {**d["scene"]["initial_states"][0], "id": f"s{i}"} for i in range(65)
            ]
        ),
    ):
        changed = deepcopy(value)
        change(changed)
        synchronize_checker(changed)
        with pytest.raises(ValidationError):
            SceneTask.model_validate(changed)


def test_materialized_bytes_and_private_reference_binding_are_verified():
    value = SceneTask.model_validate(task())
    contents = {
        d.path: (EXAMPLES / "valid/execution" / d.path).read_bytes()
        for d in value.scene.dependencies
    }
    assert dependency_issues(value, contents) == ()
    contents["assets/part.obj"] += b"tampered"
    assert dependency_issues(value, contents)[0].code == "dependency_drift"
    contents.pop("assets/part.obj")
    assert dependency_issues(value, contents)[0].code == "dependency_missing"
    manifest = SubmissionManifest.model_validate(read("valid/submission.json"))
    binding = PrivateReferenceBinding.model_validate(
        read("valid/private/reference-binding.json")
    )
    args = dict(
        execution_package_digest=package_digest(EXAMPLES / "valid/execution"),
        private_package_digest=package_digest(EXAMPLES / "valid/private"),
        execution_contract=value.execution_contract,
    )
    assert submission_issues(manifest, binding, **args) == ()
    for key, code in (
        ("execution_package_digest", "execution_drift"),
        ("private_package_digest", "private_drift"),
    ):
        changed = {**args, key: "f" * 64}
        assert code in {i.code for i in submission_issues(manifest, binding, **changed)}
    changed = binding.model_dump()
    changed.update(
        execution_package_digest="a" * 64, execution_contract_digest="b" * 64
    )
    assert {
        i.code
        for i in submission_issues(
            manifest, PrivateReferenceBinding.model_validate(changed), **args
        )
    } == {"reference_binding_drift", "reference_contract_drift"}
    changed = manifest.model_dump()
    changed["private_package_digest"] = "f" * 64
    with pytest.raises(ValidationError):
        SubmissionManifest.model_validate(changed)
    for files in (["reference-binding.json"], ["reference.json", "reference.json"]):
        changed = binding.model_dump()
        changed["reference_files"] = files
        with pytest.raises(ValidationError):
            PrivateReferenceBinding.model_validate(changed)


def test_capability_result_axes_are_separate_and_not_a_local_success_claim():
    rows = read("capability-responses.json")
    for row in rows:
        result = {
            "schema_version": 1,
            "submission_digest": "a" * 64,
            "capability": row,
            "admission": "not_run",
            "reward_status": "not_evaluated",
            "evidence_digest": None,
        }
        assert SceneAdmissionResult.model_validate(result).model_dump() == result
    base = {
        "schema_version": 1,
        "submission_digest": "a" * 64,
        "capability": {"status": "supported", "issues": []},
        "admission": "admitted",
        "reward_status": "eligible",
        "evidence_digest": "b" * 64,
    }
    assert SceneAdmissionResult.model_validate(base)
    for change in (
        {"capability": rows[1]},
        {"evidence_digest": None},
        {"admission": "not_run"},
        {"capability": {"status": "supported", "issues": rows[1]["issues"]}},
        {"capability": {"status": "development", "issues": []}},
    ):
        with pytest.raises(ValidationError):
            SceneAdmissionResult.model_validate({**base, **change})


def refresh_descriptors(value):
    """Preserve content identity while testing invalid binding semantics."""
    from openroboto_protocol.tabletop_assets import canonical_digest

    assets = {}
    for asset in value["scene"]["assets"]:
        for section in ("visual", "collision", "physics", "symmetry", "provenance"):
            part = asset[section]
            part["digest"] = canonical_digest(
                {k: v for k, v in part.items() if k != "digest"}
            )
        asset["digest"] = canonical_digest(
            {k: v for k, v in asset.items() if k != "digest"}
        )
        assets[asset["id"]] = {k: asset[k] for k in ("id", "version", "digest")}
    for obj in value["task_definition"]["objects"]:
        if obj["asset"]["id"] in assets:
            obj["asset"] = assets[obj["asset"]["id"]]
    camera = value["scene"]["camera"]
    camera["digest"] = canonical_digest(
        {k: v for k, v in camera.items() if k != "digest"}
    )
    synchronize_checker(value)


@pytest.mark.parametrize(
    "path,bad",
    [
        (("scene", "assets", 0, "collision", "parts", 0, "size"), [1.0, 1.0]),
        (("scene", "assets", 0, "collision", "parts", 0, "mesh_path"), None),
        (
            ("scene", "assets", 0, "collision", "parts", 0, "mesh_path"),
            "assets/missing.obj",
        ),
        (
            ("scene", "assets", 0, "physics", "contacts", "panel", "friction"),
            [-1.0, 0.0, 0.0],
        ),
        (("scene", "assets", 0, "physics", "contacts"), {}),
        (
            ("scene", "assets", 0, "physics", "contacts", "panel", "contact_dimension"),
            True,
        ),
        (
            ("scene", "assets", 0, "physics", "contacts", "panel", "contact_dimension"),
            2,
        ),
        (("scene", "assets", 0, "physics", "mass_kg"), 0.0),
        (("scene", "assets", 0, "physics", "inertia_kg_m2"), [0.0] * 6),
        (("scene", "assets", 0, "symmetry", "category"), "axial_unsigned"),
        (("scene", "assets", 0, "symmetry", "axis_xyz"), [0.0, 0.0, 1.0]),
        (("scene", "assets", 0, "symmetry", "rotations_xyzw"), []),
        (("scene", "assets", 0, "symmetry", "rotations_xyzw"), [[0.0, 0.0, 1.0, 0.0]]),
        (
            ("scene", "assets", 0, "symmetry", "rotations_xyzw"),
            [[0.0, 0.0, 0.0, 1.0]] * 2,
        ),
        (("scene", "frames", 0, "parent"), "door_frame"),
        (("scene", "frames", 0, "parent"), "missing"),
        (("scene", "frames", 1, "id"), "world"),
        (("scene", "frames", 1, "parent"), "door_frame"),
        (("scene", "frames", 1, "parent"), "missing"),
        (("scene", "joints", 0, "axis_in_child_body"), [0.0, 0.0, 0.0]),
        (("scene", "joints", 0, "unit"), "m"),
        (("scene", "joints", 0, "parent_body"), "door"),
        (("scene", "initial_states", 0, "joints", 0, "position"), 2.0),
        (("scene", "platform_groups"), []),
        (("scene", "platform_groups", 0, "collision_geoms"), ["door_panel"]),
        (("scene", "contact_targets", 0, "id"), "robot"),
        (("scene", "contact_targets", 0, "entity_id"), "missing"),
        (("scene", "contact_targets", 0, "geom_names"), ["door_rim_not_handle"]),
        (("scene", "contact_targets", 0, "geom_names"), ["door_handle", "door_handle"]),
        (("scene", "camera", "frame"), "missing"),
        (("scene", "camera", "image_size"), [320, 180]),
        (("task_definition", "objects", 0, "body_frame"), "missing"),
        (("task_definition", "objects", 0, "collision_geoms"), {"panel": "door_panel"}),
        (("task_definition", "objects", 0, "free_joint_name"), "fake_reset"),
        (("task_definition", "objects", 0, "motion"), "fixed"),
    ],
)
def test_compound_frame_joint_contact_and_camera_bindings_fail_closed(path, bad):
    value = task()
    node = value
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = bad
    refresh_descriptors(value)
    with pytest.raises(ValidationError):
        SceneTask.model_validate(value)


def test_content_digests_cannot_be_reused_after_descriptor_changes():
    for section in ("visual", "collision", "physics", "symmetry", "provenance"):
        value = task()
        value["scene"]["assets"][0][section]["digest"] = "f" * 64
        with pytest.raises(ValidationError, match="canonical content"):
            SceneTask.model_validate(value)
    value = task()
    value["scene"]["assets"].append(deepcopy(value["scene"]["assets"][0]))
    with pytest.raises(ValidationError, match="unique assets/frames"):
        SceneTask.model_validate(value)
    value = task()
    part = value["scene"]["assets"][0]["collision"]["parts"][0]
    value["scene"]["assets"][0]["collision"]["parts"].append(deepcopy(part))
    refresh_descriptors(value)
    with pytest.raises(ValidationError, match="part ids"):
        SceneTask.model_validate(value)
    value = task()
    value["task_definition"]["objects"][0]["asset"]["digest"] = "f" * 64
    synchronize_checker(value)
    with pytest.raises(ValidationError, match="exact declared"):
        SceneTask.model_validate(value)


def test_symmetry_groups_are_explicit_and_shared_program_checks_remain_normative():
    for category, rotations, axis in (
        ("isotropic", [], None),
        ("axial_unsigned", [], [0.0, 0.0, 1.0]),
        ("box", [[0.0, 0.0, 0.0, 1.0]], None),
    ):
        value = task()
        value["scene"]["assets"][0]["symmetry"].update(
            category=category, rotations_xyzw=rotations, axis_xyz=axis
        )
        value["task_definition"]["stages"][0]["goal"] = {
            "type": "stable",
            "object_id": "door",
        }
        refresh_descriptors(value)
        assert SceneTask.model_validate(value)
    for category, axis in (("isotropic", None), ("axial_unsigned", [0.0, 0.0, 1.0])):
        value = task()
        value["scene"]["assets"][0]["symmetry"].update(category=category, axis_xyz=axis)
        refresh_descriptors(value)
        with pytest.raises(ValidationError, match="Continuous symmetry"):
            SceneTask.model_validate(value)


def test_primitive_and_compound_shape_descriptors_are_not_bbox_aliases():
    for kind, size in (
        ("box", [0.02, 0.03, 0.04]),
        ("sphere", [0.02]),
        ("cylinder", [0.02, 0.03]),
        ("capsule", [0.02, 0.03]),
    ):
        value = task()
        for section in ("collision", "visual"):
            value["scene"]["assets"][0][section]["parts"][0].update(
                kind=kind, size=size, mesh_path=None
            )
        refresh_descriptors(value)
        assert SceneTask.model_validate(value)


def test_free_reset_and_fixed_pose_assertions_do_not_invent_free_joints():
    for motion, free_joint in (("fixed", None), ("free", "door_free")):
        value = task()
        value["task_definition"]["objects"][0].update(
            motion=motion, free_joint_name=free_joint
        )
        value["scene"]["joints"] = []
        value["scene"]["initial_states"][0]["joints"] = []
        synchronize_checker(value)
        assert SceneTask.model_validate(value)
    value = task()
    obj = deepcopy(value["task_definition"]["objects"][0])
    obj.update(
        id="free_copy",
        body="copy",
        body_frame="copy_frame",
        motion="free",
        free_joint_name="hinge",
        collision_geoms={"panel": "copy_panel", "handle": "copy_handle"},
    )
    value["task_definition"]["objects"].append(obj)
    value["scene"]["frames"].append({**value["scene"]["frames"][1], "id": "copy_frame"})
    synchronize_checker(value)
    with pytest.raises(ValidationError, match="Free and scalar joint names"):
        SceneTask.model_validate(value)


def test_part_contact_targets_are_explicit_subsets_and_alias_overlap_is_legal():
    value = task()
    value["scene"]["contact_targets"].append(
        {"id": "panel_surface", "entity_id": "door", "geom_names": ["door_panel"]}
    )
    value["scene"]["contact_targets"].append(
        {
            "id": "whole_surface",
            "entity_id": "door",
            "geom_names": ["door_panel", "door_handle"],
        }
    )
    value["task_definition"]["stages"][0]["goal"] = {
        "type": "no_contact",
        "object_id": "door",
        "other_id": "panel_surface",
    }
    synchronize_checker(value)
    assert SceneTask.model_validate(value)
    value["scene"]["contact_targets"].append(
        deepcopy(value["scene"]["contact_targets"][0])
    )
    with pytest.raises(ValidationError):
        SceneTask.model_validate(value)
