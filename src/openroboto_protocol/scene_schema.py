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


class _Joint(_Closed):
    """Bind one scalar hinge/slide to a declared body's imported MuJoCo joint."""

    id: _Id
    name: _Text
    body_id: _Id
    kind: Literal["hinge", "slide"]


class _Object(_Closed):
    """Logical checker identity bound to actual geometry, never a proxy cube."""

    id: _Id
    body: _Text
    asset_id: _Path
    symmetry: Literal[
        "none", "isotropic", "axial_unsigned", "box", "polygon64", "prism"
    ]


class _Zone(_Closed):
    """Reuse the composed circular XY zone, capacity and asset acceptance rules."""

    id: _Id
    position: Annotated[list[float], Field(min_length=2, max_length=2)]
    radius_m: Annotated[float, Field(ge=0.025, le=0.20)]
    capacity: Annotated[int, Field(ge=1)]
    accepts: Annotated[list[_Path], Field(min_length=1)]


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
    """Version-2 task.json for the explicit imported-scene path only.

    v1 task.json is parsed by the existing task_contract entrypoint. An unknown
    profile, extra field, sensor widening or checker substitution fails closed.
    Public summaries are authored separately, never extracted from private text.
    """

    schema_version: Annotated[int, Field(ge=2, le=2)]
    source: Literal["axis"]
    task_id: _Id
    instruction: _Text
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
        ):
            raise ValueError("Use unique, non-reserved logical and physical bindings")
        files = {d.path: d for d in self.scene.dependencies}
        if any(o.asset_id not in files for o in objects.values()) or any(
            j.body_id not in objects for j in joints.values()
        ):
            raise ValueError("Objects and joints must bind declared scene resources")
        for zone in zones.values():
            if (
                zone.capacity > budget["max_objects"]
                or len(set(zone.accepts)) != len(zone.accepts)
                or any(
                    a not in {o.asset_id for o in objects.values()}
                    for a in zone.accepts
                )
            ):
                raise ValueError(
                    "Zone capacity and accepts must match declared object assets"
                )
        if (
            len(objects) > budget["max_objects"]
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
        issues = validate_program(
            [s.model_dump(mode="json") for s in definition.stages],
            definition.invariants,
            object_symmetries={k: o.symmetry for k, o in objects.items()},
            zone_ids=set(zones),
            max_stages=budget["max_stages"],
            max_hold_ticks=budget["max_control_steps_per_trial"] * 100,
        )
        if issues:
            raise TaskContractError(issues)
        return self


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
