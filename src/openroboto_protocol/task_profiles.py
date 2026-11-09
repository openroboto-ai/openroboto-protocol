"""Explicit multi-version dispatch; released task_contract functions stay frozen.

Consumers opt into this module when they understand new profiles. Development
profiles require explicit catalog opt-in and never imply deployment or coverage.
Verify v1 regression and v2 dispatch with tests/test_tabletop_v2.py. No I/O.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from . import tabletop_v2, task_contract
from .task_contract import ContractIssue, TaskContractError

__all__ = [
    "checker_types_for_profile",
    "is_v2",
    "parse_public_summary",
    "profile_document",
    "task_profile_catalog",
    "validate_task_document",
]


def is_v2(profile: object) -> bool:
    """True only for the full exact reference, never for an id-only claim."""
    return isinstance(profile, Mapping) and dict(profile) == tabletop_v2.profile_ref()


def profile_document(profile: object) -> dict[str, Any]:
    """Resolve an exact immutable contract or reject an unknown version."""
    if is_v2(profile):
        return tabletop_v2.profile_catalog()
    if profile == task_contract.execution_profile_ref():
        return task_contract.task_profile_catalog()[0]
    raise TaskContractError(
        [
            ContractIssue(
                "execution_profile_invalid",
                "execution_profile",
                "Unknown profile reference.",
            )
        ]
    )


def checker_types_for_profile(profile: object) -> tuple[str, ...]:
    """Wire vocabulary belongs to a verified exact profile reference."""
    return (
        ("TabletopProgramChecker",)
        if is_v2(profile)
        else task_contract.checker_types_for_profile(profile)
    )


def task_profile_catalog(
    *, include_development: bool = False
) -> tuple[dict[str, Any], ...]:
    """Keep released-only discovery by default; candidates are explicit opt-in."""
    released = task_contract.task_profile_catalog()
    return (
        (*released, tabletop_v2.profile_catalog()) if include_development else released
    )


def parse_public_summary(
    value: object, *, expected_profile: object = None
) -> dict[str, Any] | None:
    """Apply the same privacy/text limits while binding the new profile exactly."""
    if isinstance(value, Mapping) and is_v2(value.get("execution_profile")):
        if (
            expected_profile is not None
            and value["execution_profile"] != expected_profile
        ):
            raise TaskContractError(
                [
                    ContractIssue(
                        "public_summary_profile_mismatch",
                        "public_summary.execution_profile",
                        "The synopsis must reference the task profile.",
                    )
                ]
            )
        translated = copy.deepcopy(dict(value))
        translated["execution_profile"] = task_contract.execution_profile_ref()
        parsed = task_contract.parse_public_summary(translated)
        assert parsed is not None
        parsed["execution_profile"] = tabletop_v2.profile_ref()
        return parsed
    return task_contract.parse_public_summary(value, expected_profile=expected_profile)


def validate_task_document(
    task: Mapping[str, Any],
    *,
    expected_execution_profile: object = None,
    require_profile: bool = False,
) -> tuple[ContractIssue, ...]:
    """Dispatch by declared id, then validate its full digest, never fall back."""
    ref = task.get("execution_profile")
    if isinstance(ref, Mapping) and ref.get("id") == "panda-tabletop-v2":
        issues = list(
            tabletop_v2.validate_document(
                task, expected_execution_profile=expected_execution_profile
            )
        )
        try:
            parse_public_summary(task.get("public_summary"), expected_profile=ref)
        except TaskContractError as exc:
            issues.extend(exc.issues)
        return tuple(issues)
    return task_contract.validate_task_document(
        task,
        expected_execution_profile=expected_execution_profile,
        require_profile=require_profile,
    )
