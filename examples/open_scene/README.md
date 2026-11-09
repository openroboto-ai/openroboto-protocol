# Imported-scene contract fixtures

These are synthetic interface fixtures. `valid` means structural validation only:
the scene has no installed robot or runnable harness, the reference has no actions,
and its goal is already true at reset. It is deliberately **not** a solvability,
quality, server admission or reward example. The environment and control identity
documents in `identities/` are unregistered fixture descriptors. The Panda digest
reuses the existing platform manifest identity; it grants no adapter support.

## Entry and minimal dependency graph

```
valid/
  submission.json                  detached joint identity
  execution/
    task.json                      task schema 3; explicit scene profile
    scene.xml                      -> assets/part.obj
    assets/part.obj                 terminal dependency
  private/
    reference-binding.json         -> execution digest and execution conditions
    reference.json                 private synthetic payload
```

`legacy_v1/task.json` uses the existing v1 profile and checker. Its directory file
list, package digest and public catalog are frozen in `legacy-baseline.json`.
`invalid-missing-dependency.json` names a missing DAG node and must fail parsing.
Do not change existing historical golden vectors when evolving these new fixtures.

`scene.dependencies` lists every transitive scene/mesh/texture/include file. Edges
resolve from the **package root**, never the referring file's directory or CWD.
No absolute paths, URL fetches, `.`/`..` components, backslashes or symlinks are
permitted on the new path. Source URLs are provenance only; source_revision is a
fixed commit or content SHA-256. Dependencies must be reachable, unique and acyclic.
The importer must independently extract XML references, reject undeclared or
escaping resources, compare actual byte sizes/hashes via `dependency_issues`,
validate geometry/physics/symmetry, joint limits and all actual initial states.
It must compare robot/environment/control references against its trusted registry.
The package's decompressed actual size, including task.json and stray files, must
also fit the selected resource guard. A manifest's claimed sizes are not evidence.

`task_definition.objects` binds each logical entity to the actual MJCF body,
an exact `asset={id,version,digest}`, its body_frame, asset_pose_in_body,
collision_geoms (part ID to exact MJCF geom name), motion and nullable
free_joint_name. Fixed bodies and fixed children are explicit entities or contact
subsets; descendants are **never** collected implicitly. Each collision geom has
one entity/platform owner. A body owns precisely its declared part map; different
bodies cannot silently share a body frame or a collision geom.

`scene.assets` describes compound visual and collision `parts` separately, with
part-local transforms. Each part uses actual box/sphere/cylinder/capsule/mesh
geometry, not a bounding-box alias. Size conventions match MuJoCo: box
half-extents; sphere radius; cylinder/capsule radius and straight half-length
excluding cap radius; mesh XYZ scale plus materialized mesh_path. The importer
must use actual compiled collision hulls/parts, not visual faces or boxes, for
support/pose/zone geometry. Unsupported mesh/compound semantics must be rejected,
never approximated without a new identity.

Each asset has separately verified canonical digests for visual geometry,
collision geometry, physics, symmetry and provenance, then one complete asset
digest. Every descriptor digest hashes its complete typed JSON excluding only
its own digest. The asset digest includes all nested digests and values, id and
version. Physics binds mass, COM in asset axes, full inertia in the order
Ixx/Iyy/Izz/Ixy/Ixz/Iyz about COM, and friction/contact dimension for every
collision part. Geometry/physics/source bytes and all other MJCF options remain
bound by E as well; descriptor comparison is not a substitute for independently
compiling the actual model. The importer must verify all declared physical and
symmetry properties against those bytes and compiled data.

Symmetry declares an exact finite rotation set or a continuous isotropic/unsigned
axial group. Imported asymmetric objects use only the identity rotation; they
are never assigned a controlled cube group. The existing controlled-v2 catalog,
geometry digest and proper rotation groups remain unchanged.

`scene.frames` is a reset-reference tree rooted at implicit identity `world`;
`scene` is a required direct child of world. Transforms place child origins/axes
in parent coordinates. Body poses in initial_states and measured observations
are **world body-origin** poses, not geometry origins or COM. Geometry part poses
are asset-local; asset_pose_in_body maps the asset origin/axes into its body.
Robot mount is world-relative. Zones explicitly use world XY coordinates, and
accept exact asset references. Capacity remains matching assignment capacity,
not a global occupancy invariant.

Every initial state asserts every entity body's world pose and sets every scalar
joint once. Only `motion=free` has a free_joint_name and receives a free-body pose
reset. Apply free-body reset, then scalar joint values, then verify **all** body
pose assertions; fixed/articulated bodies receive no invented freejoint. Initial
velocities are zero. Joint bindings include id/name/body_id (the child entity),
actual parent_body, hinge/slide kind, unit axis_in_child_body, increasing range,
and rad/m unit. Initial values must be in range. The importer checks actual
compiled names, parent/child topology, DoF, axis, ranges and pose/joint consistency.
Scene joints are passive and move through real contacts. No author scene actuator
may widen the existing **nine-dimensional Panda action** mapping; discover and
reject unauthorized actuators after compilation.

`scene.platform_groups` binds trusted table/robot bodies and exact geom sets;
the worker must compare these against the registered robot/control/scene identity.
`scene.contact_targets` names explicit geom subsets of an entity, such as
socket_bottom distinct from socket_rim. Alias subsets may overlap; geometry
ownership may not. Existing supported_by/no_contact predicates may reference these
target IDs through the scene-only optional `validate_program(contact_targets=...)`.
The default controlled-v2 call and its findings are unchanged. No new joint score
predicate is introduced.

The scene adapter must normalize observations and the judge from the same entity,
body, frame and geom map. Keep exact physical geom IDs and a unique physical
contact identity in internal samples so selecting a target alias does not duplicate
contact forces or confuse rim/bottom. COM is a separate measured field used for
support, never substituted for position. Internal diagnostics, contacts/trace and
reference actions must not leak through unregistered model/tool output.

`scene.camera` binds name, frame, pose_in_frame, vertical_fov_deg, image_size and a
canonical calibration digest. The actual camera and renderer must match it.
Resolution is the existing observation contract's 320x180 (state) or 1280x720
(vision); the allowed sensor list remains exact and vision has no GT coordinates.
MJCF wxyz conversion is explicit; all contract quaternions are xyzw.

The current scene revision is `panda-open-scene-v1@2.0.0`, task schema 3 and
execution contract 3. The unpublished 0.16.0/schema-2 draft remains at commit
`7c2063d` and its original evidence directory; it is not accepted by upgrading
its version or digest in place. New required fields and semantics have a new
protocol candidate version and exact profile digest.

## Identity and private materials

Let `E = package_digest(execution/)` and `P = package_digest(private/)`.
Both calls use the unchanged package_digest rule: every regular file, Path-part
ordering, `sha256("\n".join(path + "\0" + file_sha256))`. Nothing is excluded
within either root; reference bytes, scene bytes, task.json and stray files count.

`SubmissionIdentity(E, P).digest` is SHA-256 of UTF-8 canonical JSON:

```json
{"domain":"openroboto-scene-submission","execution_package_digest":"E","private_package_digest":"P","version":1}
```

Replace E/P with the actual lowercase hex values. Canonical JSON uses sorted keys,
`ensure_ascii=False`, separators `(',', ':')`, and `allow_nan=False`, exactly as
`tabletop_assets.canonical_digest` specifies. This joint digest is a **new field**,
`submission_digest`; it never replaces the old meaning of `package_digest`.
A new-path API must carry both root digests and submission_digest explicitly.
Execution-only evaluation identity is E; a detached receipt must bind E to P and
the joint digest before splitting views. On-chain and historical v1 digest fields
are unchanged. HF repo@commit pins transport bytes, independently of this identity.

`private/reference-binding.json` binds E and the canonical normalized version-3
execution-contract digest; it cannot contain P or the joint digest. Private payloads
and their schema versions are part of P. `submission.json`, signatures and admission
receipts live **outside both roots**. Do not write them into either hashed tree.
This produces a directed identity graph with no self-reference. Replacing the
scene, checker, robot, environment, assets, execution conditions or private
reference changes at least one root and therefore submission_digest.

Parse `SubmissionManifest` and `PrivateReferenceBinding`, independently measure
both roots, then call `submission_issues` before distributing views. A consumer
must also check referenced private files exist and validate their declared schema.
Hashes establish content identity, not trust. A detached authenticated receipt
must bind submission_digest, E, P, normalized execution-contract digest, policy
version, worker/runtime exact identity and raw evidence/signature roots. Signature,
lease and attestation verification are owned by OPE-232/OPE-233. Nothing in these
fixtures is a real receipt or an admission attestation.

## Distribution

| Role | Execution files | Private reference files | Detached receipt |
| --- | --- | --- | --- |
| Author, own task | yes | yes | yes |
| Admission worker, assigned task | yes | yes | yes |
| Authorized reviewer | yes | yes | yes |
| Evaluation worker, assigned task | yes | no | yes |
| Model/tool process | no raw files | no | no |
| Public visitor | no | no | no |

The immutable `scene_contract.DISTRIBUTION_POLICY` is the shared role matrix.
Ownership, leases and scopes must be enforced by the caller. Model processes get
only registered tools and observations. Execution files include internal checker
configuration; they must not be mounted into the model process. Public responses
contain authored public_summary and sanitized status only, not private paths,
references, checker diagnostics, credentials, raw parser inputs or unfiltered logs.
OPE-232 must verify process/container access isolation; a role table is not proof.

## Results and consumer integration

`schemas/result.schema.json` and `SceneAdmissionResult` freeze the new-path result.
Capability statuses are `supported`, `development`, `unsupported`,
`resource_unavailable`, and `invalid_package`, each blocked status carrying stable
code/path/message findings. Only exact registry support and worker attestation can
justify `supported`; parsing or loading a mesh cannot. Admission separately uses
`not_run`, `physical_failed`, `infra_error`, `pending_review`, or `admitted`.
Reward status separately uses `not_evaluated`, `ineligible`, or `eligible`.
An admitted result requires an exact evidence digest. Eligibility still requires
server policy and quality/duplicate-review evidence; this schema does not grant it.
Infrastructure failure is not an author rejection. Existing status.py and v1
result semantics remain unchanged. HTTP auth/envelopes and retry codes remain the
backend's responsibility.

P00 owns public fields, digest rules and version changes. OPE-230 imports assets;
OPE-227/OPE-231 owns physical mappings/monitor integration; OPE-232 owns isolation
and signed evidence; OPE-233 owns API, explicit catalog gating and persistence;
OPE-234 owns CLI routing; OPE-235 consumes the same API/schema; OPE-236 owns review;
OPE-237 attests server readiness; OPE-238 owns integration/release gates. Every
consumer must use one reviewed protocol wheel version/SHA and these schemas.
Candidate profile discovery, exact operator enablement and consumer deployment are
separate. These downstream consumers have not yet switched to this new contract.

Run `uv run python scripts/export_scene_contract.py --output /tmp/scene-contract`
and compare its output with this directory (excluding this README). The exported
JSON Schemas are wire-shape artifacts; normative Python validation also enforces
the shared AST, cross-field references, no cycles and resource/permission binding.
