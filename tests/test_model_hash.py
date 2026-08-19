"""``model_hash`` edge behaviour. Real on-chain fingerprints live in
``test_model_golden_vectors.py``."""

from __future__ import annotations

import hashlib

import pytest

from openroboto_protocol.model_hash import (
    extract_lfs_sha256,
    fingerprint_lfs_sha256,
    model_hash_from_hf_tree,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


# ── extract_lfs_sha256 ────────────────────────────────────────────────────


def test_sha256_key_wins_over_oid() -> None:
    assert extract_lfs_sha256({"sha256": SHA_A, "oid": SHA_B}) == SHA_A


def test_falls_back_to_oid() -> None:
    """The real shape of the HF REST API: only ``oid`` is there, and it *is* the
    content sha256."""
    assert extract_lfs_sha256({"oid": SHA_B, "size": 7, "pointerSize": 135}) == SHA_B


def test_empty_sha256_falls_back_to_oid() -> None:
    assert extract_lfs_sha256({"sha256": "", "oid": SHA_B}) == SHA_B


def test_dict_without_any_hash_yields_nothing() -> None:
    assert extract_lfs_sha256({"size": 7}) == ""


def test_string_form_strips_the_algorithm_prefix() -> None:
    assert extract_lfs_sha256(f"sha256:{SHA_A}") == SHA_A


def test_string_without_prefix_is_not_a_fingerprint() -> None:
    """A bare string with no ``sha256:`` prefix is not accepted — accepting it
    would mean accepting an unknown digest algorithm."""
    assert extract_lfs_sha256(SHA_A) == ""


def test_missing_lfs_field_yields_nothing() -> None:
    assert extract_lfs_sha256(None) == ""


def test_huggingface_hub_blob_object_yields_nothing() -> None:
    """A ``BlobLfsInfo`` object is not a dict — the caller has to convert it
    itself, otherwise the whole repo yields no fingerprint."""

    class BlobLfsInfo:
        sha256 = SHA_A

    assert extract_lfs_sha256(BlobLfsInfo()) == ""


def test_non_string_hash_raises_instead_of_being_skipped() -> None:
    """Skipping silently would turn "a submission that must be rejected" into
    "a fingerprint computed from the remaining files", and that is a behaviour
    change."""
    with pytest.raises(TypeError):
        extract_lfs_sha256({"sha256": 12345})


# ── fingerprint_lfs_sha256 ────────────────────────────────────────────────


def test_empty_input_has_no_fingerprint() -> None:
    """The empty string is the sentinel value for "no fingerprint"; the backend
    rejects outright when it sees it (model_hash_empty)."""
    assert fingerprint_lfs_sha256([]) == ""


def test_algorithm_is_sort_join_sha256() -> None:
    expected = hashlib.sha256(f"{SHA_A}\n{SHA_B}".encode()).hexdigest()
    assert fingerprint_lfs_sha256([SHA_B, SHA_A]) == expected


def test_duplicate_hashes_are_not_deduplicated() -> None:
    """The same content appearing twice in a repo contributes two entries,
    matching production."""
    assert fingerprint_lfs_sha256([SHA_A, SHA_A]) != fingerprint_lfs_sha256([SHA_A])


def test_fingerprint_is_lowercase_hex_of_64_chars() -> None:
    digest = fingerprint_lfs_sha256([SHA_A])
    assert len(digest) == 64
    assert digest == digest.lower()


# ── model_hash_from_hf_tree ───────────────────────────────────────────────


def test_directories_and_plain_blobs_are_excluded() -> None:
    tree = [
        {"type": "directory", "size": 0, "path": "params"},
        {"type": "file", "size": 12, "path": "config.json"},
        {"type": "file", "size": 9, "path": "model.safetensors", "lfs": {"oid": SHA_A}},
    ]
    assert model_hash_from_hf_tree(tree) == fingerprint_lfs_sha256([SHA_A])


def test_directory_carrying_an_lfs_field_is_still_excluded() -> None:
    """Two lines of defence: look at ``type`` first, then at whether there is an
    ``lfs`` field."""
    tree = [{"type": "directory", "size": 0, "path": "params", "lfs": {"oid": SHA_A}}]
    assert model_hash_from_hf_tree(tree) == ""


def test_repo_without_lfs_files_has_no_fingerprint() -> None:
    """A repo with only small files yields no fingerprint — the backend rejects on
    that basis, it is not "the fingerprint happens to be empty"."""
    tree = [{"type": "file", "size": 12, "path": "README.md"}]
    assert model_hash_from_hf_tree(tree) == ""
