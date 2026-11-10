"""Closed imported-scene wire models, behind the existing [schemas] extra.

Authors, workers and API generators consume the same model_json_schema output.
The importer must independently check bytes, XML dependency closure, actual
geometry/physics, robot/control identity and initial states. These models do no
I/O and do not claim that a structurally valid scene can run or earn rewards.
Verify with tests/test_scene_schema.py; export with SceneTask.model_json_schema().
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from . import scene_contract
from .tabletop_assets import canonical_digest
from .tabletop_v2 import resource_catalog, validate_program
from .task_contract import ContractIssue, TaskContractError

__all__ = [
    "PrivateReferenceBinding",
    "SceneAdmissionResult",
    "SceneTask",
    "SubmissionManifest",
    "dependency_issues",
    "submission_issues",
]


def _path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or not path.parts
        or path.is_absolute()
        or path.as_posix() != value
        or any(p in {".", ".."} for p in path.parts)
        or any(ord(c) < 32 or c in "\\:" for c in value)
    ):
        raise ValueError(
            "Use a canonical package-relative POSIX path without traversal"
        )
    return value


def _quaternion(value: list[float]) -> list[float]:
    if abs(sum(v * v for v in value) - 1) > 1e-8:
        raise ValueError("Supply a unit xyzw quaternion; normalization is not implicit")
    return value


_Text = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]
# Only the model-facing scene instruction has the profile's larger capacity.
# Identity, provenance and definition text retain their independent limits.
_SceneInstruction = Annotated[
    str,
    Field(
        min_length=1,
        max_length=scene_contract.MAX_SCENE_INSTRUCTION_CHARACTERS,
        pattern=r"\S",
    ),
]
_Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Path = Annotated[str, AfterValidator(_path)]
_Vector = Annotated[list[float], Field(min_length=3, max_length=3)]
_Quaternion = Annotated[
    list[float], Field(min_length=4, max_length=4), AfterValidator(_quaternion)
]


class _Closed(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, allow_inf_nan=False
    )


class _ExactRef(_Closed):
    """One inseparable id/version/content reference; no floating dependency."""

    id: _Text
    version: _Text
    digest: _Digest


class _Dependency(_Closed):
    """An already materialized file and its direct package-relative dependencies."""

    path: _Path
    sha256: _Digest
    size_bytes: Annotated[int, Field(ge=0)]
    requires: list[_Path]
    source: _Text
    source_revision: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
    license: _Text


class _Pose(_Closed):
    """World coordinates in metres and an xyzw quaternion; MJCF uses wxyz."""

    position_m: _Vector
    quaternion_xyzw: _Quaternion


class _Robot(_Closed):
    """Platform robot bytes and tool/control mapping must match the registry."""

    artifact: _ExactRef
    control_mapping: _ExactRef
    mount: _Pose


class _Digested(_Closed):
    """Identity of the complete typed descriptor excluding only its own digest."""

    digest: _Digest

    @model_validator(mode="after")
    def content_identity(self) -> Self:
        if self.digest != canonical_digest(
            self.model_dump(mode="json", exclude={"digest"})
        ):
            raise ValueError("Descriptor digest does not match its canonical content")
        return self


class _GeometryPart(_Closed):
    """Actual shapes in asset coordinates; sizes follow MuJoCo conventions.

    Box: half-extents. Sphere: radius. Cylinder/capsule: radius and straight
    half-length (excluding cap radius). Mesh: XYZ scale, plus materialized path.
    """

    id: _Id
    kind: Literal["box", "sphere", "cylinder", "capsule", "mesh"]
    size: Annotated[list[float], Field(min_length=1, max_length=3)]
    mesh_path: _Path | None
    pose_in_asset: _Pose

    @model_validator(mode="after")
    def shape_parameters(self) -> Self:
        dimensions = {"box": 3, "sphere": 1, "cylinder": 2, "capsule": 2, "mesh": 3}
        if (
            len(self.size) != dimensions[self.kind]
            or any(v <= 0 for v in self.size)
            or (self.kind == "mesh") != (self.mesh_path is not None)
        ):
            raise ValueError(
                "Use exact positive shape dimensions or mesh XYZ scale and path"
            )
        return self


class _Geometry(_Digested):
    parts: Annotated[list[_GeometryPart], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_parts(self) -> Self:
        if len({p.id for p in self.parts}) != len(self.parts):
            raise ValueError("Geometry part ids must be unique within their asset")
        return self


class _ContactPhysics(_Closed):
    friction: Annotated[list[float], Field(min_length=3, max_length=3)]
    contact_dimension: Annotated[int, Field(ge=1, le=6)]

    @model_validator(mode="after")
    def nonnegative_friction(self) -> Self:
        if self.contact_dimension not in {1, 3, 4, 6} or any(
            v < 0 for v in self.friction
        ):
            raise ValueError(
                "Use nonnegative friction and contact dimension 1, 3, 4 or 6"
            )
        return self


class _Physics(_Digested):
    """Mass/COM/full inertia in asset frame; contact properties per collision part."""

    mass_kg: Annotated[float, Field(ge=0)]
    center_of_mass_m: _Vector
    # Tensor order is Ixx, Iyy, Izz, Ixy, Ixz, Iyz about COM in asset axes.
    inertia_kg_m2: Annotated[list[float], Field(min_length=6, max_length=6)]
    contacts: dict[_Id, _ContactPhysics]


class _Symmetry(_Digested):
    """Explicit proper rotation group or continuous axis, verified against geometry."""

    category: Literal[
        "none", "isotropic", "axial_unsigned", "box", "polygon64", "prism"
    ]
    rotations_xyzw: list[_Quaternion]
    axis_xyz: _Vector | None

    @model_validator(mode="after")
    def representation(self) -> Self:
        if self.category == "axial_unsigned":
            if (
                self.axis_xyz is None
                or abs(sum(v * v for v in self.axis_xyz) - 1) > 1e-8
            ):
                raise ValueError("Axial symmetry requires a unit asset-frame axis")
        elif self.axis_xyz is not None:
            raise ValueError("Only axial symmetry declares an axis")
        if self.category in {"isotropic", "axial_unsigned"}:
            if self.rotations_xyzw:
                raise ValueError(
                    "Continuous symmetry does not enumerate finite rotations"
                )
        elif not self.rotations_xyzw or len(
            {tuple(q) for q in self.rotations_xyzw}
        ) != len(self.rotations_xyzw):
            raise ValueError("Finite symmetry requires unique explicit rotations")
        if self.category == "none" and self.rotations_xyzw != [[0.0, 0.0, 0.0, 1.0]]:
            raise ValueError("Asymmetric geometry has only the identity rotation")
        return self


class _Provenance(_Digested):
    source: _Text
    source_revision: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
    license: _Text


class _Asset(_Digested):
    """Compound asset identity includes geometry, physics, symmetry and provenance."""

    id: _Id
    version: _Text
    visual: _Geometry
    collision: _Geometry
    physics: _Physics
    symmetry: _Symmetry
    provenance: _Provenance

    @model_validator(mode="after")
    def collision_physics(self) -> Self:
        if set(self.physics.contacts) != {p.id for p in self.collision.parts}:
            raise ValueError(
                "Contact physics must cover every collision part exactly once"
            )
        return self


class _Frame(_Closed):
    """Reset-reference child origin/axes in its parent; world is implicit identity."""

    id: _Id
    parent: _Id
    pose_in_parent: _Pose


def _validate_frame_tree(parents: Mapping[str, str]) -> None:
    """Verify world ancestry once per node, with no recursion or size limit.

    Unseen nodes are white, the current path is gray, and validated ancestors
    are black. A node becomes black only after its whole path reaches world or
    an already validated ancestor. Each parent edge is read at most once, so
    chains and shared subtrees take O(V + E) work and O(V) auxiliary memory.
    """
    validated = {"world"}
    for frame_id in parents:
        if frame_id in validated:
            continue
        path: list[str] = []
        visiting: set[str] = set()
        current = frame_id
        while current not in validated:
            if current not in parents or current in visiting:
                raise ValueError("Frames must form a tree rooted at world")
            visiting.add(current)
            path.append(current)
            current = parents[current]
        validated.update(path)


class _Joint(_Closed):
    """Passive scalar joint identity, checked against the actual compiled model."""

    id: _Id
    name: _Text
    body_id: _Id
    parent_body: _Text
    kind: Literal["hinge", "slide"]
    axis_in_child_body: _Vector
    range: Annotated[list[float], Field(min_length=2, max_length=2)]
    unit: Literal["rad", "m"]

    @model_validator(mode="after")
    def axis_range_and_unit(self) -> Self:
        if (
            abs(sum(v * v for v in self.axis_in_child_body) - 1) > 1e-8
            or self.range[0] >= self.range[1]
            or self.unit != ("rad" if self.kind == "hinge" else "m")
        ):
            raise ValueError(
                "Use a unit joint axis, increasing range and matching rad/m unit"
            )
        return self


class _Object(_Closed):
    """Explicit geometry ownership on one real body; descendants are never implicit."""

    id: _Id
    body: _Text
    asset: _ExactRef
    body_frame: _Id
    asset_pose_in_body: _Pose
    collision_geoms: dict[_Id, _Text]
    motion: Literal["fixed", "free", "articulated"]
    free_joint_name: _Text | None

    @model_validator(mode="after")
    def reset_binding(self) -> Self:
        if (self.motion == "free") != (self.free_joint_name is not None):
            raise ValueError("Only free bodies declare a free-joint reset target")
        return self


class _PlatformGroup(_Closed):
    """Exact trusted platform geom groups, never author-overridable target aliases."""

    id: Literal["table", "robot"]
    body: _Text
    collision_geoms: Annotated[list[_Text], Field(min_length=1)]


class _ContactTarget(_Closed):
    """Named subset of an entity's geoms; aliases may overlap, ownership may not."""

    id: _Id
    entity_id: _Id
    geom_names: Annotated[list[_Text], Field(min_length=1)]


class _Camera(_Digested):
    """Exact render camera and calibration; no state coordinates in vision mode."""

    name: _Text
    frame: _Id
    pose_in_frame: _Pose
    vertical_fov_deg: Annotated[float, Field(gt=0, lt=180)]
    image_size: Annotated[
        list[Annotated[int, Field(gt=0)]], Field(min_length=2, max_length=2)
    ]


class _Zone(_Closed):
    """World-XY circular zones; capacity is matching assignment, not occupancy."""

    id: _Id
    frame: Literal["world"]
    position: Annotated[list[float], Field(min_length=2, max_length=2)]
    radius_m: Annotated[float, Field(ge=0.025, le=0.20)]
    capacity: Annotated[int, Field(ge=1)]
    accepts: Annotated[list[_ExactRef], Field(min_length=1)]


class _ObjectState(_Closed):
    object_id: _Id
    pose: _Pose


class _JointState(_Closed):
    joint_id: _Id
    position: float


class _InitialState(_Closed):
    """Materialized initial states; every registered state is retained on failure."""

    id: _Id
    objects: list[_ObjectState]
    joints: list[_JointState]


class _Scene(_Closed):
    """A closed DAG of local files; no runtime URL resolution or robot replacement."""

    entrypoint: _Path
    dependencies: Annotated[list[_Dependency], Field(min_length=1)]
    joints: list[_Joint]
    assets: Annotated[list[_Asset], Field(min_length=1)]
    frames: Annotated[list[_Frame], Field(min_length=1)]
    platform_groups: list[_PlatformGroup]
    contact_targets: list[_ContactTarget]
    camera: _Camera
    initial_states: Annotated[list[_InitialState], Field(min_length=1)]
    units: Literal["metres_radians"]
    coordinates: Literal["right_handed_z_up_xyzw"]

    @model_validator(mode="after")
    def dependency_graph(self) -> Self:
        files = {d.path: d for d in self.dependencies}
        if len(files) != len(self.dependencies) or self.entrypoint not in files:
            raise ValueError(
                "Declare unique dependencies including the scene entrypoint"
            )
        if {"task.json", "submission.json", "receipt.json"} & files.keys():
            raise ValueError(
                "Identity/task records cannot reference their own file digest"
            )
        visited: set[str] = set()
        active: set[str] = set()
        pending = [(self.entrypoint, False)]
        while pending:
            path, exiting = pending.pop()
            if exiting:
                active.remove(path)
                visited.add(path)
                continue
            if path not in files or path in active:
                raise ValueError("Missing dependency or dependency cycle")
            if path in visited:
                continue
            active.add(path)
            requires = files[path].requires
            if len(set(requires)) != len(requires):
                raise ValueError("Duplicate dependency edge")
            pending.append((path, True))
            pending.extend((p, False) for p in reversed(requires))
        if visited != files.keys():
            raise ValueError(
                "Every dependency must be reachable from the scene entrypoint"
            )
        return self


class _Stage(_Closed):
    """The OPE-227 AST is validated by validate_program, including early rejection."""

    id: _Id
    requires: list[_Id]
    goal: dict[str, JsonValue]
    hold_ticks: int
    invariants: list[dict[str, JsonValue]]
    reject_early: bool


class _Definition(_Closed):
    template: Literal["imported_scene"]
    objects: Annotated[list[_Object], Field(min_length=1)]
    zones: list[_Zone]
    stages: list[_Stage]
    invariants: list[dict[str, JsonValue]]
    observation: Literal["state", "vision"]
    resource_profile: _Text


class SceneTask(_Closed):
    """Version-3 task.json for the explicit imported-scene path only.

    v1 task.json is parsed by the existing task_contract entrypoint. An unknown
    profile, extra field, sensor widening or checker substitution fails closed.
    Public summaries are authored separately, never extracted from private text.
    """

    schema_version: Annotated[int, Field(ge=3, le=3)]
    source: Literal["axis"]
    task_id: _Id
    instruction: _SceneInstruction
    execution_profile: _ExactRef
    environment: _ExactRef
    robot: _Robot
    execution_contract: dict[str, JsonValue]
    public_summary: dict[str, JsonValue]
    scene: _Scene
    task_definition: _Definition
    checker: dict[str, JsonValue]

    @model_validator(mode="after")
    def shared_contract(self) -> Self:
        ref = self.execution_profile.model_dump()
        if ref != scene_contract.profile_ref():
            raise ValueError("Use the exact imported-scene profile reference")
        if (
            self.environment.id != "panda-mujoco-scene"
            or self.robot.artifact.id != "franka-panda"
        ):
            raise ValueError(
                "This path requires the registered Panda/MuJoCo scene adapter"
            )
        conditions = scene_contract.parse_execution_contract(self.execution_contract)
        if conditions["agent"]["tools_digest"] != self.robot.control_mapping.digest:
            raise ValueError(
                "Robot control mapping must match the frozen tool interface"
            )
        definition = self.task_definition
        if (
            conditions["observation_contract"] != definition.observation
            or conditions["resource_profile"] != definition.resource_profile
        ):
            raise ValueError(
                "Task and execution observation/resource selections must match"
            )
        scene_contract.parse_public_summary(self.public_summary, expected_profile=ref)
        expected = {
            "checker": {
                "type": "TabletopProgramChecker",
                "definition": definition.model_dump(mode="json"),
            }
        }
        if self.checker != expected:
            raise ValueError("Use the exact composed program checker definition")
        budget = resource_catalog()[definition.resource_profile]
        objects = {o.id: o for o in definition.objects}
        zones = {z.id: z for z in definition.zones}
        joints = {j.id: j for j in self.scene.joints}
        if (
            len(objects) != len(definition.objects)
            or len(zones) != len(definition.zones)
            or len(joints) != len(self.scene.joints)
            or objects.keys() & zones.keys()
            or {"robot", "table", "franka"} & (objects.keys() | zones.keys())
            or any(
                i.startswith(("zone_", "target_zone_"))
                for i in objects.keys() | zones.keys()
            )
            or len({o.body for o in objects.values()}) != len(objects)
            or len({j.name for j in joints.values()}) != len(joints)
            or len({o.body_frame for o in objects.values()}) != len(objects)
        ):
            raise ValueError("Use unique, non-reserved logical and physical bindings")
        files = {d.path: d for d in self.scene.dependencies}
        self.scene_bindings(objects, joints, conditions)
        assets = {a.id: a for a in self.scene.assets}
        for zone in zones.values():
            if (
                zone.capacity > budget["max_objects"]
                or len({r.id for r in zone.accepts}) != len(zone.accepts)
                or any(
                    r.id not in {o.asset.id for o in objects.values()}
                    or r.model_dump()
                    != {
                        "id": assets[r.id].id,
                        "version": assets[r.id].version,
                        "digest": assets[r.id].digest,
                    }
                    for r in zone.accepts
                )
            ):
                raise ValueError(
                    "Zone capacity and accepts must match declared object assets"
                )
        if (
            sum(o.motion != "fixed" for o in objects.values()) > budget["max_objects"]
            or len(zones) > 32
            or (definition.observation == "vision" and len(zones) > 16)
            or len(self.scene.initial_states) > budget["max_instances"]
            or sum(d.size_bytes for d in files.values()) > budget["max_package_bytes"]
        ):
            raise ValueError("Scene exceeds the selected development resource guard")
        states = self.scene.initial_states
        if len({s.id for s in states}) != len(states):
            raise ValueError("Initial state ids must be unique")
        for state in states:
            if (
                len(state.objects) != len(objects)
                or {o.object_id for o in state.objects} != objects.keys()
                or len(state.joints) != len(joints)
                or {j.joint_id for j in state.joints} != joints.keys()
            ):
                raise ValueError(
                    "Each initial state must cover every object and scalar joint "
                    "exactly once"
                )
            if any(
                not joints[j.joint_id].range[0]
                <= j.position
                <= joints[j.joint_id].range[1]
                for j in state.joints
            ):
                raise ValueError(
                    "Initial scalar joint positions must be within declared ranges"
                )
        issues = validate_program(
            [s.model_dump(mode="json") for s in definition.stages],
            definition.invariants,
            object_symmetries={
                k: assets[o.asset.id].symmetry.category for k, o in objects.items()
            },
            contact_targets={t.id for t in self.scene.contact_targets},
            zone_ids=set(zones),
            max_stages=budget["max_stages"],
            max_hold_ticks=budget["max_control_steps_per_trial"] * 100,
        )
        if issues:
            raise TaskContractError(issues)
        return self

    def scene_bindings(
        self,
        objects: Mapping[str, _Object],
        joints: Mapping[str, _Joint],
        conditions: Mapping[str, Any],
    ) -> None:
        """Cross-bind descriptors; the importer still verifies compiled physics."""
        assets = {a.id: a for a in self.scene.assets}
        frames = {f.id: f for f in self.scene.frames}
        if (
            len(assets) != len(self.scene.assets)
            or len(frames) != len(self.scene.frames)
            or "world" in frames
            or "scene" not in frames
            or frames["scene"].parent != "world"
        ):
            raise ValueError(
                "Declare unique assets/frames with scene and implicit world"
            )
        _validate_frame_tree({name: frame.parent for name, frame in frames.items()})
        files = {d.path for d in self.scene.dependencies}
        for asset in assets.values():
            if any(
                p.mesh_path is not None and p.mesh_path not in files
                for geometry in (asset.visual, asset.collision)
                for p in geometry.parts
            ):
                raise ValueError("Mesh parts must bind materialized dependencies")
        owned_geoms: list[str] = []
        for obj in objects.values():
            bound_asset = assets.get(obj.asset.id)
            if bound_asset is None or obj.asset.model_dump() != {
                "id": bound_asset.id,
                "version": bound_asset.version,
                "digest": bound_asset.digest,
            }:
                raise ValueError(
                    "Every entity requires an exact declared compound asset"
                )
            if obj.body_frame not in frames or set(obj.collision_geoms) != {
                p.id for p in bound_asset.collision.parts
            }:
                raise ValueError(
                    "Bind the body frame and every collision part to an actual geom"
                )
            if obj.motion != "fixed" and (
                bound_asset.physics.mass_kg <= 0
                or any(v <= 0 for v in bound_asset.physics.inertia_kg_m2[:3])
            ):
                raise ValueError(
                    "Moving bodies require positive mass and diagonal inertia"
                )
            owned_geoms.extend(obj.collision_geoms.values())
        groups = {g.id: g for g in self.scene.platform_groups}
        if set(groups) != {"table", "robot"} or len(groups) != len(
            self.scene.platform_groups
        ):
            raise ValueError(
                "Bind both trusted table and robot geometry groups exactly once"
            )
        for group in groups.values():
            owned_geoms.extend(group.collision_geoms)
        if len(set(owned_geoms)) != len(owned_geoms):
            raise ValueError(
                "Each collision geom has exactly one entity/platform owner"
            )
        targets = {t.id: t for t in self.scene.contact_targets}
        if len(targets) != len(self.scene.contact_targets) or targets.keys() & (
            objects.keys() | {"table", "robot", "franka"}
        ):
            raise ValueError(
                "Contact target ids must be unique and distinct from owners"
            )
        for target in targets.values():
            owner = objects.get(target.entity_id)
            if (
                owner is None
                or len(set(target.geom_names)) != len(target.geom_names)
                or not set(target.geom_names) <= set(owner.collision_geoms.values())
            ):
                raise ValueError(
                    "Contact targets must name explicit geom subsets of their entity"
                )
        free_names = [
            o.free_joint_name for o in objects.values() if o.free_joint_name is not None
        ]
        if len(set(free_names)) != len(free_names) or set(free_names) & {
            j.name for j in joints.values()
        }:
            raise ValueError("Free and scalar joint names must be disjoint and unique")
        for joint in joints.values():
            child = objects.get(joint.body_id)
            if (
                child is None
                or child.motion != "articulated"
                or joint.parent_body == child.body
            ):
                raise ValueError(
                    "Bind scalar joints to an articulated child and distinct parent"
                )
        if (
            self.scene.camera.frame not in {"world", *frames}
            or self.scene.camera.image_size
            != scene_contract.profile_catalog()["camera_sizes"][
                conditions["observation_contract"]
            ]
        ):
            raise ValueError(
                "Camera frame and resolution must match the exact observation contract"
            )


def dependency_issues(
    task: SceneTask, contents: Mapping[str, bytes]
) -> tuple[ContractIssue, ...]:
    """Check already-read dependency bytes; no alternate filesystem walk is added.

    Importers must reject symlinks, escape paths, undeclared XML dependencies and
    robot conflicts before loading. package_digest remains the sole directory
    identity rule, including task.json and unrelated files. Actual decompressed
    package size, not these declared sizes, must also pass the resource guard.
    """
    issues = []
    for dependency in task.scene.dependencies:
        content = contents.get(dependency.path)
        if content is None:
            issues.append(
                ContractIssue(
                    "dependency_missing",
                    dependency.path,
                    "Materialize the declared dependency.",
                )
            )
        elif (
            len(content) != dependency.size_bytes
            or hashlib.sha256(content).hexdigest() != dependency.sha256
        ):
            issues.append(
                ContractIssue(
                    "dependency_drift",
                    dependency.path,
                    "Dependency bytes differ from the frozen declaration.",
                )
            )
    return tuple(issues)


class PrivateReferenceBinding(_Closed):
    """private/reference-binding.json names execution bytes, never its own root.

    Actions, solutions and raw evidence stay in this private root. A detached
    signed proof binds SubmissionManifest plus the reference execution contract;
    hashes alone are content identity, not an admission attestation.
    """

    schema_version: Annotated[int, Field(ge=1, le=1)]
    execution_package_digest: _Digest
    execution_contract_digest: _Digest
    reference_schema_version: Annotated[int, Field(ge=1, le=1)]
    reference_files: Annotated[list[_Path], Field(min_length=1)]

    @model_validator(mode="after")
    def no_self_reference(self) -> Self:
        if len(set(self.reference_files)) != len(self.reference_files) or {
            "reference-binding.json",
            "submission.json",
            "receipt.json",
        } & set(self.reference_files):
            raise ValueError(
                "Reference payloads must be unique and exclude identity records"
            )
        return self


class SubmissionManifest(_Closed):
    """Detached submission.json; identity is checked before any role-based split."""

    schema_version: Annotated[int, Field(ge=1, le=1)]
    execution_package_digest: _Digest
    private_package_digest: _Digest
    submission_digest: _Digest

    @model_validator(mode="after")
    def joint_identity(self) -> Self:
        identity = scene_contract.SubmissionIdentity(
            self.execution_package_digest, self.private_package_digest
        )
        if self.submission_digest != identity.digest:
            raise ValueError("Submission digest does not bind both package roots")
        return self


class _Finding(_Closed):
    """Sanitized diagnostics; raw inputs and private evidence are not public."""

    code: _Text
    path: str
    message: _Text


class _Capability(_Closed):
    status: scene_contract.CapabilityStatus
    issues: list[_Finding]

    @model_validator(mode="after")
    def status_and_findings(self) -> Self:
        scene_contract.CapabilityResponse(
            self.status,
            tuple(ContractIssue(i.code, i.path, i.message) for i in self.issues),
        )
        return self


class SceneAdmissionResult(_Closed):
    """Keep capability, physical admission and reward eligibility separate.

    Signatures, leases and eligibility policy are caller obligations. This model
    never rewrites status.py vocabulary or historical v1 results. It does not
    compute eligibility from scene complexity or a successful local load.
    """

    schema_version: Annotated[int, Field(ge=1, le=1)]
    submission_digest: _Digest
    capability: _Capability
    admission: Literal[
        "not_run", "physical_failed", "infra_error", "pending_review", "admitted"
    ]
    reward_status: Literal["not_evaluated", "ineligible", "eligible"]
    evidence_digest: _Digest | None

    @model_validator(mode="after")
    def separate_gates(self) -> Self:
        if self.capability.status != "supported" and self.admission != "not_run":
            raise ValueError("A blocked capability cannot claim physical admission")
        if self.admission == "admitted" and self.evidence_digest is None:
            raise ValueError("Admission requires an exact evidence digest")
        if self.reward_status == "eligible" and self.admission != "admitted":
            raise ValueError("Eligibility requires a separately admitted submission")
        return self


def submission_issues(
    manifest: SubmissionManifest,
    reference: PrivateReferenceBinding,
    *,
    execution_package_digest: str,
    private_package_digest: str,
    execution_contract: Mapping[str, Any],
) -> tuple[ContractIssue, ...]:
    """Compare independently measured roots and the reference's execution binding.

    Callers measure both roots with package_digest before splitting distribution;
    evaluation workers compare their execution root with the detached signed
    receipt. Signature verification and authenticated receipt storage stay in
    OPE-232/OPE-233, rather than being implied by a matching hash.
    """
    scene_contract.SubmissionIdentity(execution_package_digest, private_package_digest)
    checks = (
        (
            manifest.execution_package_digest == execution_package_digest,
            "execution_drift",
            "execution_package_digest",
        ),
        (
            manifest.private_package_digest == private_package_digest,
            "private_drift",
            "private_package_digest",
        ),
        (
            reference.execution_package_digest == execution_package_digest,
            "reference_binding_drift",
            "reference.execution_package_digest",
        ),
        (
            reference.execution_contract_digest
            == canonical_digest(
                scene_contract.parse_execution_contract(execution_contract)
            ),
            "reference_contract_drift",
            "reference.execution_contract_digest",
        ),
    )
    return tuple(
        ContractIssue(code, path, "Measured material differs from the frozen binding.")
        for passed, code, path in checks
        if not passed
    )
