"""``model_hash`` 的边界行为。链上真实指纹在 ``test_model_golden_vectors.py``。"""

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
    """HF REST API 的真实形态：只有 ``oid``，它就是内容 sha256。"""
    assert extract_lfs_sha256({"oid": SHA_B, "size": 7, "pointerSize": 135}) == SHA_B


def test_empty_sha256_falls_back_to_oid() -> None:
    assert extract_lfs_sha256({"sha256": "", "oid": SHA_B}) == SHA_B


def test_dict_without_any_hash_yields_nothing() -> None:
    assert extract_lfs_sha256({"size": 7}) == ""


def test_string_form_strips_the_algorithm_prefix() -> None:
    assert extract_lfs_sha256(f"sha256:{SHA_A}") == SHA_A


def test_string_without_prefix_is_not_a_fingerprint() -> None:
    """没有 ``sha256:`` 前缀的裸串不认 —— 认了就等于接受未知摘要算法。"""
    assert extract_lfs_sha256(SHA_A) == ""


def test_missing_lfs_field_yields_nothing() -> None:
    assert extract_lfs_sha256(None) == ""


def test_huggingface_hub_blob_object_yields_nothing() -> None:
    """``BlobLfsInfo`` 对象不是 dict —— 调用方必须自己转，否则整仓算不出指纹。"""

    class BlobLfsInfo:
        sha256 = SHA_A

    assert extract_lfs_sha256(BlobLfsInfo()) == ""


def test_non_string_hash_raises_instead_of_being_skipped() -> None:
    """静默跳过会把「该拒的提交」变成「用剩下的文件算个指纹」，那是改行为。"""
    with pytest.raises(TypeError):
        extract_lfs_sha256({"sha256": 12345})


# ── fingerprint_lfs_sha256 ────────────────────────────────────────────────


def test_empty_input_has_no_fingerprint() -> None:
    """空串是「没有指纹」的哨兵，后端见到它直接拒（model_hash_empty）。"""
    assert fingerprint_lfs_sha256([]) == ""


def test_algorithm_is_sort_join_sha256() -> None:
    expected = hashlib.sha256(f"{SHA_A}\n{SHA_B}".encode()).hexdigest()
    assert fingerprint_lfs_sha256([SHA_B, SHA_A]) == expected


def test_duplicate_hashes_are_not_deduplicated() -> None:
    """同一份内容在仓库里出现两次会进两条，和生产一致。"""
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
    """两道防线：先看 ``type``，再看有没有 ``lfs``。"""
    tree = [{"type": "directory", "size": 0, "path": "params", "lfs": {"oid": SHA_A}}]
    assert model_hash_from_hf_tree(tree) == ""


def test_repo_without_lfs_files_has_no_fingerprint() -> None:
    """只有小文件的仓库算不出指纹 —— 后端据此拒，不是「指纹恰好为空」。"""
    tree = [{"type": "file", "size": 12, "path": "README.md"}]
    assert model_hash_from_hf_tree(tree) == ""
