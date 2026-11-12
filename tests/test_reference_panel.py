"""The role-free panel and signed manifest contract shared by both peers."""

import hashlib
import json

import pytest

from openroboto_protocol.reference_panel import (
    PANEL_MANIFEST_FIELDS,
    PANEL_MANIFEST_VERSION,
    manifest_hash,
    manifest_payload,
    validate_panel_definition,
)


def test_role_free_panel_definition() -> None:
    validate_panel_definition({"members": [{"id": "model-a"}, {"id": "model-b"}]})
    validate_panel_definition({"members": [None]})


@pytest.mark.parametrize(
    "panel",
    [
        {"members": [], "roles": {"best": "a", "worst": "b"}},
        {"members": [{}], "roles_as_of": None},
        {"members": [{}], "unknown": 1},
        {},
        {"members": []},
        {"members": {}},
        {"members": [{"id": "a", "role": "best"}]},
    ],
)
def test_invalid_panel_definition_is_rejected(panel: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_panel_definition(panel)


def test_manifest_version_and_fields_are_explicit() -> None:
    assert PANEL_MANIFEST_VERSION == 2
    assert PANEL_MANIFEST_FIELDS == {
        "version",
        "task_id",
        "run_id",
        "package_digest",
        "config_version",
        "config_digest",
        "panel",
        "settlement",
        "node_id",
        "key_id",
        "algorithm",
        "members",
        "trials_digest",
        "signature",
    }


def test_manifest_canonical_bytes_preserve_historical_signatures() -> None:
    # The cryptographic projection is version-independent. Only the acceptance
    # boundary rejects v1; an accepted original remains independently verifiable.
    for version in (1, 2):
        body = {"version": version, "task_id": "题", "members": []}
        if version == 1:
            body["roles"] = {"best": "a", "worst": "b"}
        expected = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        signed = {**body, "signature": "a" * 128}
        assert manifest_payload(signed) == expected
        assert manifest_hash(signed) == hashlib.sha256(expected).hexdigest()
        assert manifest_hash(signed) == manifest_hash(body)


def test_manifest_rejects_non_finite_json() -> None:
    with pytest.raises(ValueError):
        manifest_payload({"value": float("nan")})


def test_public_exports() -> None:
    from openroboto_protocol import reference_panel

    assert set(reference_panel.__all__) == {
        "PANEL_MANIFEST_FIELDS",
        "PANEL_MANIFEST_VERSION",
        "manifest_hash",
        "manifest_payload",
        "validate_panel_definition",
    }
