"""Export portable interface fixtures and schemas, never physical evidence.

Run: uv run python scripts/export_scene_contract.py --output /tmp/scene-contract
Compare that directory with examples/open_scene. No network or simulator is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from openroboto_protocol import scene_contract as scene
from openroboto_protocol import task_contract as v1
from openroboto_protocol.package_digest import package_digest, package_files
from openroboto_protocol.scene_schema import (
    PrivateReferenceBinding,
    SceneAdmissionResult,
    SceneTask,
    SubmissionManifest,
)
from openroboto_protocol.tabletop_assets import canonical_digest


def export(root: Path) -> None:
    def write(path: str, value: object) -> None:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    environment = {
        "id": "panda-mujoco-scene",
        "version": "fixture-1",
        "purpose": "Interface fixture only; no registered adapter",
    }
    control = {
        "id": "panda-tools",
        "version": "fixture-1",
        "purpose": "Interface fixture only; no runnable harness",
    }
    for name, value in (("environment", environment), ("control", control)):
        write(f"identities/{name}.json", value)

    def ref(value: dict) -> dict:
        return {
            "id": value["id"],
            "version": value["version"],
            "digest": canonical_digest(value),
        }

    pose = {"position_m": [0.0, 0.0, 0.0], "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0]}
    conditions = {
        "version": 3,
        "execution_profile": scene.profile_ref(),
        "harness": {"name": "headless"},
        "agent": {
            "harness_commit": hashlib.sha1(
                b"interface-fixture-not-a-harness"
            ).hexdigest(),
            "system_prompt_digest": hashlib.sha256(b"fixture-prompt").hexdigest(),
            "tools_digest": ref(control)["digest"],
        },
        "permissions": {
            "mode": "privileged",
            "allowed_sensors": ["image", "proprioception", "success"],
        },
        "budget": {"max_api_calls": 12, "max_steps": 500, "wall_seconds": 60.0},
        "timing": {"control_step_seconds": 0.2, "clock_mode": "sim", "s": 0.0},
        "observation_contract": "vision",
        "resource_profile": "tabletop-standard-v2",
    }
    resources = {
        "scene.xml": (
            b'<mujoco><asset><mesh name="part" file="assets/part.obj"/>'
            b'</asset><worldbody><body name="door">'
            b'<joint name="hinge" type="hinge" axis="0 0 1" range="0 90"/>'
            b'<geom name="door_panel" type="mesh" mesh="part" mass="1"/>'
            b'<geom name="door_handle" type="capsule" size=".006 .02" '
            b'pos=".03 0 .02"/>'
            b"</body></worldbody></mujoco>\n"
        ),
        "assets/part.obj": (
            b"v 0 0 0\nv 0.04 0 0\nv 0 0.04 0\nv 0 0 0.04\n"
            b"f 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n"
        ),
    }
    dependencies = []
    for path, content in resources.items():
        target = root / "valid/execution" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        dependencies.append(
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "requires": ["assets/part.obj"] if path == "scene.xml" else [],
                "source": "fixture://ope229/synthetic",
                "source_revision": hashlib.sha256(content).hexdigest(),
                "license": "MIT",
            }
        )

    def digested(**value: object) -> dict:
        return {**value, "digest": canonical_digest(value)}

    parts = [
        {
            "id": "panel",
            "kind": "mesh",
            "size": [1.0, 1.0, 1.0],
            "mesh_path": "assets/part.obj",
            "pose_in_asset": pose,
        },
        {
            "id": "handle",
            "kind": "capsule",
            "size": [0.006, 0.02],
            "mesh_path": None,
            "pose_in_asset": {
                "position_m": [0.03, 0.0, 0.02],
                "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
        },
    ]
    asset = digested(
        id="door_compound",
        version="fixture-2",
        visual=digested(parts=parts),
        collision=digested(parts=parts),
        physics=digested(
            mass_kg=1.0,
            center_of_mass_m=[0.0, 0.0, 0.02],
            inertia_kg_m2=[0.001, 0.001, 0.001, 0.0, 0.0, 0.0],
            contacts={
                p["id"]: {"friction": [0.8, 0.01, 0.001], "contact_dimension": 6}
                for p in parts
            },
        ),
        symmetry=digested(
            category="none", rotations_xyzw=[[0.0, 0.0, 0.0, 1.0]], axis_xyz=None
        ),
        provenance=digested(
            source="fixture://ope229/synthetic",
            source_revision=hashlib.sha256(resources["scene.xml"]).hexdigest(),
            license="MIT",
        ),
    )
    asset_ref = {k: asset[k] for k in ("id", "version", "digest")}
    definition = {
        "template": "imported_scene",
        "observation": "vision",
        "resource_profile": "tabletop-standard-v2",
        "objects": [
            {
                "id": "door",
                "body": "door",
                "asset": asset_ref,
                "body_frame": "door_frame",
                "asset_pose_in_body": pose,
                "collision_geoms": {"panel": "door_panel", "handle": "door_handle"},
                "motion": "articulated",
                "free_joint_name": None,
            }
        ],
        "zones": [],
        "invariants": [],
        "stages": [
            {
                "id": "opened",
                "requires": [],
                "hold_ticks": 500,
                "invariants": [],
                "reject_early": False,
                "goal": {
                    "type": "pose",
                    "object_id": "door",
                    "quaternion": [0.0, 0.0, 0.0, 1.0],
                },
            }
        ],
    }
    task = {
        "schema_version": 3,
        "source": "axis",
        "task_id": "synthetic_door",
        "instruction": (
            "Interface example only; this is not a physical task "
            "or a reference success."
        ),
        "execution_profile": scene.profile_ref(),
        "environment": ref(environment),
        "robot": {
            "artifact": {
                "id": "franka-panda",
                "version": "panda-tabletop-v1/1.0.0",
                "digest": v1.task_profile_catalog()[0]["scene_manifest_sha256"],
            },
            "control_mapping": ref(control),
            "mount": pose,
        },
        "execution_contract": conditions,
        "public_summary": {
            "title": "Synthetic imported scene",
            "instruction": "Schema and identity example only.",
            "robot": "franka-panda",
            "execution_profile": scene.profile_ref(),
            "origin": "original",
        },
        "scene": {
            "entrypoint": "scene.xml",
            "dependencies": dependencies,
            "joints": [
                {
                    "id": "door_hinge",
                    "name": "hinge",
                    "body_id": "door",
                    "kind": "hinge",
                    "parent_body": "world",
                    "axis_in_child_body": [0.0, 0.0, 1.0],
                    "range": [0.0, 1.5707963267948966],
                    "unit": "rad",
                }
            ],
            "assets": [asset],
            "frames": [
                {"id": "scene", "parent": "world", "pose_in_parent": pose},
                {"id": "door_frame", "parent": "scene", "pose_in_parent": pose},
            ],
            "platform_groups": [
                {"id": "table", "body": "table", "collision_geoms": ["table_surface"]},
                {
                    "id": "robot",
                    "body": "franka",
                    "collision_geoms": ["franka/fixture_geom"],
                },
            ],
            "contact_targets": [
                {
                    "id": "handle_surface",
                    "entity_id": "door",
                    "geom_names": ["door_handle"],
                }
            ],
            "camera": digested(
                name="camera0",
                frame="world",
                pose_in_frame=pose,
                vertical_fov_deg=47.0,
                image_size=[1280, 720],
            ),
            "initial_states": [
                {
                    "id": "first",
                    "objects": [{"object_id": "door", "pose": pose}],
                    "joints": [{"joint_id": "door_hinge", "position": 0.0}],
                }
            ],
            "units": "metres_radians",
            "coordinates": "right_handed_z_up_xyzw",
        },
        "task_definition": definition,
        "checker": {
            "checker": {"type": "TabletopProgramChecker", "definition": definition}
        },
    }
    SceneTask.model_validate(task)
    write("valid/execution/task.json", task)
    execution_digest = package_digest(root / "valid/execution")
    write(
        "valid/private/reference.json",
        {
            "schema_version": 1,
            "purpose": "Synthetic private material, not an oracle",
            "actions": [],
        },
    )
    binding = {
        "schema_version": 1,
        "reference_schema_version": 1,
        "execution_package_digest": execution_digest,
        "execution_contract_digest": canonical_digest(
            scene.parse_execution_contract(conditions)
        ),
        "reference_files": ["reference.json"],
    }
    PrivateReferenceBinding.model_validate(binding)
    write("valid/private/reference-binding.json", binding)
    private_digest = package_digest(root / "valid/private")
    manifest = {
        "schema_version": 1,
        "execution_package_digest": execution_digest,
        "private_package_digest": private_digest,
        "submission_digest": scene.SubmissionIdentity(
            execution_digest, private_digest
        ).digest,
    }
    SubmissionManifest.model_validate(manifest)
    write("valid/submission.json", manifest)
    invalid = deepcopy(task)
    invalid["scene"]["dependencies"][0]["requires"] = ["assets/missing.obj"]
    write("invalid-missing-dependency.json", invalid)

    old_definition = {
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
    old = {
        "schema_version": 1,
        "source": "axis",
        "task_id": "legacy_fixture",
        "instruction": "Move the cube to its marked zone.",
        "execution_profile": v1.execution_profile_ref(),
        "task_definition": old_definition,
        "checker": {
            "checker": {"type": "TabletopZoneChecker", "goals": old_definition["goals"]}
        },
    }
    assert v1.validate_task_document(old) == ()
    write("legacy_v1/task.json", old)
    write(
        "legacy-baseline.json",
        {
            "profile_catalog": v1.task_profile_catalog(),
            "package_files": package_files(root / "legacy_v1"),
            "package_digest": package_digest(root / "legacy_v1"),
        },
    )
    write("profile.json", scene.profile_catalog())
    for name, model in (
        ("task", SceneTask),
        ("private-binding", PrivateReferenceBinding),
        ("submission", SubmissionManifest),
        ("result", SceneAdmissionResult),
    ):
        write(f"schemas/{name}.schema.json", model.model_json_schema())
    write(
        "capability-responses.json",
        [
            scene.CapabilityResponse("supported").to_dict(),
            *[
                scene.CapabilityResponse(
                    status, (scene.ContractIssue(code, path, message),)
                ).to_dict()
                for status, code, path, message in [
                    (
                        "development",
                        "capability_unverified",
                        "execution_profile",
                        "The selected adapter is a development candidate.",
                    ),
                    (
                        "unsupported",
                        "environment_unsupported",
                        "environment",
                        "No registered adapter matches this environment.",
                    ),
                    (
                        "resource_unavailable",
                        "worker_capacity",
                        "resource_profile",
                        "No authorized worker capacity matches this tier.",
                    ),
                    (
                        "invalid_package",
                        "dependency_missing",
                        "scene.dependencies",
                        "A required materialized dependency is missing.",
                    ),
                ]
            ],
        ],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    export(parser.parse_args().output)
