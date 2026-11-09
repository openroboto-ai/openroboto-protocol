"""Compatibility, joint identity and explicit opt-in boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from openroboto_protocol import scene_contract as scene
from openroboto_protocol import tabletop_v2, task_profiles
from openroboto_protocol import task_contract as v1
from openroboto_protocol.package_digest import package_digest, package_files
from openroboto_protocol.tabletop_assets import canonical_digest
from openroboto_protocol.task_contract import ContractIssue

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/open_scene"


def read(name):
    return json.loads((EXAMPLES / name).read_text())


def test_public_surface_is_pinned():
    assert scene.__all__ == [
        "DISTRIBUTION_POLICY",
        "MAX_SCENE_INSTRUCTION_CHARACTERS",
        "CapabilityResponse",
        "CapabilityStatus",
        "SubmissionIdentity",
        "checker_types_for_profile",
        "parse_execution_contract",
        "parse_public_summary",
        "profile_catalog",
        "profile_document",
        "profile_ref",
        "task_profile_catalog",
        "validate_task_document",
    ]
    assert tabletop_v2.__all__ == [
        "asset_catalog",
        "example_definition",
        "profile_catalog",
        "profile_ref",
        "resource_catalog",
        "validate_definition",
        "validate_document",
        "validate_program",
    ]
    assert (
        tabletop_v2.profile_ref()["digest"]
        == "4206469478bc1f121c4f76e65d364af734fa08efe2fb3cb514c34371bd76f7b7"
    )


def test_new_catalog_and_legacy_entrypoints_do_not_promote_candidates():
    baseline = read("legacy-baseline.json")
    assert list(v1.task_profile_catalog()) == baseline["profile_catalog"]
    assert (
        scene.task_profile_catalog()
        == task_profiles.task_profile_catalog()
        == v1.task_profile_catalog()
    )
    candidates = scene.task_profile_catalog(include_development=True)
    assert len(candidates) == 3
    assert candidates[-1]["status"] == "development_candidate"
    assert candidates[-1]["execution_profile"] == scene.profile_ref()
    assert candidates[-2]["execution_profile"] == tabletop_v2.profile_ref()
    changed = scene.profile_catalog()
    changed["success"]["pose_tolerance_rad"] = 999
    assert scene.profile_catalog() == read("profile.json")
    assert package_digest(EXAMPLES / "legacy_v1") == baseline["package_digest"]
    assert [list(x) for x in package_files(EXAMPLES / "legacy_v1")] == baseline[
        "package_files"
    ]
    old = read("legacy_v1/task.json")
    assert v1.validate_task_document(old) == scene.validate_task_document(old) == ()
    assert scene.validate_task_document({}) == ()
    assert scene.validate_task_document(
        old, expected_execution_profile=scene.profile_ref()
    )
    # A v1 reader must reject a new profile instead of pretending it is v1.
    assert v1.validate_task_document(read("valid/execution/task.json"))


def test_versioned_scene_dispatch_never_falls_back_on_error():
    task = read("valid/execution/task.json")
    assert scene.validate_task_document(task, require_profile=True) == ()
    assert (
        scene.validate_task_document(
            task, expected_execution_profile=scene.profile_ref()
        )
        == ()
    )
    assert (
        scene.validate_task_document(
            task, expected_execution_profile=v1.execution_profile_ref()
        )[0].code
        == "execution_profile_mismatch"
    )
    task["execution_profile"]["digest"] = "f" * 64
    assert scene.validate_task_document(task)[0].code == "scene_invalid"
    task.pop("execution_profile")
    assert scene.validate_task_document(task)
    assert scene.validate_task_document(read("invalid-missing-dependency.json"))


@pytest.mark.parametrize(
    "bad", [None, {}, {"version": True}, {"version": 2}, {"version": 3.0}]
)
def test_execution_version_requires_integer_three(bad):
    with pytest.raises(ValueError):
        scene.parse_execution_contract(bad)


def test_shared_execution_checks_do_not_widen_permissions_or_rewrite_inputs():
    task = read("valid/execution/task.json")
    raw = task["execution_contract"]
    before = json.dumps(raw, sort_keys=True)
    parsed = scene.parse_execution_contract(raw)
    assert parsed["version"] == 3 and parsed["execution_profile"] == scene.profile_ref()
    assert json.dumps(raw, sort_keys=True) == before
    raw["execution_profile"] = tabletop_v2.profile_ref()
    with pytest.raises(ValueError):
        scene.parse_execution_contract(raw)
    raw = read("valid/execution/task.json")["execution_contract"]
    raw["permissions"]["allowed_sensors"].append("state")
    with pytest.raises(ValueError, match="sensors"):
        scene.parse_execution_contract(raw)


def test_both_roots_bind_joint_identity_and_no_digest_record_is_inside_a_root(tmp_path):
    manifest = read("valid/submission.json")
    execution = package_digest(EXAMPLES / "valid/execution")
    private = package_digest(EXAMPLES / "valid/private")
    identity = scene.SubmissionIdentity(execution, private)
    assert identity.digest == manifest["submission_digest"]
    assert manifest["execution_package_digest"] == execution
    assert manifest["private_package_digest"] == private
    assert identity.digest == canonical_digest(
        {
            "domain": "openroboto-scene-submission",
            "version": 1,
            "execution_package_digest": execution,
            "private_package_digest": private,
        }
    )
    with pytest.raises(FrozenInstanceError):
        identity.private_package_digest = "a" * 64
    (tmp_path / "reference.json").write_text("different reference")
    assert (
        scene.SubmissionIdentity(execution, package_digest(tmp_path)).digest
        != identity.digest
    )
    assert scene.SubmissionIdentity(private, execution).digest != identity.digest
    assert scene.SubmissionIdentity("a" * 64, private).digest != identity.digest
    for root in ("execution", "private"):
        assert not (EXAMPLES / "valid" / root / "submission.json").exists()
        assert not (EXAMPLES / "valid" / root / "receipt.json").exists()


@pytest.mark.parametrize("bad", ["", "A" * 64, "1" * 63, "g" * 64, 1])
def test_identity_rejects_invalid_digest_claims(bad):
    with pytest.raises(ValueError):
        scene.SubmissionIdentity(bad, "a" * 64)
    with pytest.raises(ValueError):
        scene.SubmissionIdentity("a" * 64, bad)


def test_capability_is_separate_from_admission_and_reward():
    assert scene.CapabilityResponse("supported").to_dict() == {
        "status": "supported",
        "issues": [],
    }
    finding = ContractIssue("dependency_missing", "scene.dependencies", "Missing file.")
    for status in (
        "development",
        "unsupported",
        "resource_unavailable",
        "invalid_package",
    ):
        response = scene.CapabilityResponse(status, (finding,)).to_dict()
        assert (
            response["status"] == status
            and response["issues"][0]["code"] == finding.code
        )
    with pytest.raises(ValueError):
        scene.CapabilityResponse("unknown")
    with pytest.raises(ValueError):
        scene.CapabilityResponse("supported", (finding,))
    with pytest.raises(ValueError):
        scene.CapabilityResponse("development")
    policy = dict(scene.DISTRIBUTION_POLICY)
    assert policy["model"] == policy["public"] == ()
    assert "private" not in policy["evaluation_worker"]
    assert all(
        "private" in policy[role] for role in ("author", "admission_worker", "reviewer")
    )


def test_v1_v2_and_scene_identity_imports_do_not_require_pydantic():
    code = """
import sys
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("pydantic"):
            raise AssertionError("pydantic imported on a legacy path")
sys.meta_path.insert(0, Block())
from openroboto_protocol import scene_contract as scene
assert scene.validate_task_document({}) == ()
assert len(scene.task_profile_catalog()) == 1
assert scene.SubmissionIdentity("a" * 64, "b" * 64).digest
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_scene_facade_reuses_exact_profile_and_summary_dispatch():
    assert scene.profile_document(scene.profile_ref()) == scene.profile_catalog()
    assert (
        scene.profile_document(v1.execution_profile_ref())
        == v1.task_profile_catalog()[0]
    )
    assert scene.checker_types_for_profile(scene.profile_ref()) == (
        "TabletopProgramChecker",
    )
    assert scene.checker_types_for_profile(v1.execution_profile_ref()) == (
        "TabletopZoneChecker",
    )
    assert scene.checker_types_for_profile({"id": "panda-open-scene-v1"}) == ()
    with pytest.raises(v1.TaskContractError):
        scene.profile_document({"id": "panda-open-scene-v1"})
    assert scene.parse_public_summary(None) is None
    summary = read("valid/execution/task.json")["public_summary"]
    assert (
        scene.parse_public_summary(summary, expected_profile=scene.profile_ref())
        == summary
    )
    with pytest.raises(v1.TaskContractError):
        scene.parse_public_summary(summary, expected_profile=v1.execution_profile_ref())
    summary["execution_profile"]["digest"] = "f" * 64
    with pytest.raises(v1.TaskContractError):
        scene.parse_public_summary(summary)
