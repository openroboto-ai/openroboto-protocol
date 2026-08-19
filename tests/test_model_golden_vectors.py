"""Golden vectors: model fingerprints and repo structures that really happened on
chain. Changing one of them means changing history.

All three fixtures are HuggingFace repo trees of production submissions, fetched
on 2026-08-17 from
``https://huggingface.co/api/models/<repo>/tree/<commit>?recursive=true``, where
``<commit>`` is the very revision announced on chain and recorded in the
production DB:

- ``UID221_PYTORCH_TREE`` = uid 221 ``joseneto023dev/pi05-BRVeJ37DuryX@636dbaa3``
  — one of the two submissions falsely rejected on 2026-08-14, a flat openpi
  PyTorch checkpoint;
- ``UID181_JAX_TREE`` = uid 181 ``OpenRd/pi05-vLUxPb8qTmGv@97da4275``
  — the repo root is itself a JAX orbax checkpoint;
- ``UID130_NESTED_JAX_TREE`` = uid 130 ``swordswoman/pi05-j71bm5DVdD5X@ed38d896``
  — 10.8 GB, with the whole tree nested under ``merged/``.

The four items ``type`` / ``size`` / ``path`` / ``lfs.oid`` are kept verbatim;
the git blob ``oid`` and ``xetHash``, which the verdict does not use, were
dropped.

Where the expected fingerprints come from
-----------------------------------------
Every ``*_MODEL_HASH`` is the value stored in ``submissions.model_hash`` in the
**production PostgreSQL dump**
(``openroboto-backend/tests/fixtures/prod-data.sql``, round 1); it is not
something we computed just now.

While extracting this module, all 37 submissions in the dump whose "hf_commit is
a full 40-character sha" were run through: **35 reproduced verbatim, 0
mismatched**, and the revisions of the other 2 have disappeared from HF
(``joseneto023dev/pi05-6YM1X5xNCoQZ@a252578b``,
``joseneto023dev/pi05-aAxJBYQz5qqu@1a54a589`` → ``Invalid rev id``). Those 2
**must not** go into the golden vectors — the inputs are gone, so the test would
be red forever.

All three trees passed production admission and entered the evaluation queue, so
there is only one possible meaning when a layout case goes red: the new rule
would reject real miners, and must not ship.
"""

from __future__ import annotations

from typing import Any

from openroboto_protocol.model_format import (
    CheckpointFile,
    CheckpointKind,
    FormatIssueCode,
    check_checkpoint_layout,
)
from openroboto_protocol.model_hash import model_hash_from_hf_tree


def _tree(
    *entries: tuple[str, int, str] | tuple[str, int, str, str],
) -> list[dict[str, Any]]:
    """Rebuild (type, size, path[, lfs oid]) into the entry shape of the HF tree
    API."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        node: dict[str, Any] = {"type": entry[0], "size": entry[1], "path": entry[2]}
        if len(entry) == 4:
            node["lfs"] = {"oid": entry[3], "size": entry[1], "pointerSize": 135}
        out.append(node)
    return out


def _files(tree: list[dict[str, Any]]) -> list[CheckpointFile]:
    """HF tree → the input of the layout verdict. This glue is deliberately left
    in the caller and does not go into the protocol package."""
    return [
        CheckpointFile(path=e["path"], size_bytes=e["size"])
        for e in tree
        if e["type"] == "file"
    ]


UID221_PYTORCH_TREE = _tree(
    ("directory", 0, "assets"),
    ("directory", 0, "assets/physical-intelligence"),
    ("directory", 0, "assets/physical-intelligence/libero"),
    ("file", 1519, ".gitattributes"),
    ("file", 1943, "assets/physical-intelligence/libero/norm_stats.json"),
    ("file", 149, "config.json"),
    (
        "file",
        7233650272,
        "model.safetensors",
        "86cb3c2a3ac8ade1640f7fa1657d85df7cf4560cc436626e56c4173d6c9b9809",
    ),
    ("file", 119, "round_info.json"),
)
UID221_MODEL_HASH = "cf9fb4ba2504e35b6120221e042fd0d565f44a7bde5e741c3edcbbb008564e53"

UID181_JAX_TREE = _tree(
    ("directory", 0, "assets"),
    ("directory", 0, "assets/physical-intelligence"),
    ("directory", 0, "assets/physical-intelligence/libero"),
    ("directory", 0, "params"),
    ("directory", 0, "params/d"),
    ("directory", 0, "params/ocdbt.process_0"),
    ("directory", 0, "params/ocdbt.process_0/d"),
    ("file", 1895, ".gitattributes"),
    ("file", 1986, "assets/physical-intelligence/libero/norm_stats.json"),
    ("file", 258, "params/_CHECKPOINT_METADATA"),
    ("file", 22089, "params/_METADATA"),
    ("file", 0, "params/commit_success.txt"),
    ("file", 2013, "params/d/0205a790fca0d61a156f7d1997925e38"),
    ("file", 117, "params/manifest.ocdbt"),
    (
        "file",
        2719569594,
        "params/ocdbt.process_0/d/01af9b532500fa152fac66d1a477ef7e",
        "f559903aa654e1f20f73ef794cf6016b38831988c2df746e4ab91c79addd986b",
    ),
    ("file", 1999, "params/ocdbt.process_0/d/0240f6141c4100b81b8936b22cf83715"),
    ("file", 1137, "params/ocdbt.process_0/d/06b01aefbf04c9ba54b0ff7dfaa6bd4d"),
    ("file", 213, "params/ocdbt.process_0/d/46c872851604dd25b33c7b4d8c39f50a"),
    (
        "file",
        1939700553,
        "params/ocdbt.process_0/d/607549e7fe926d83a32d981fc19a54ab",
        "e309d2944a4f53ec959cfc13dc67f710ea1ed6e25fb0fd211ab6ef79d7833445",
    ),
    (
        "file",
        1212428545,
        "params/ocdbt.process_0/d/8b1877650f65a92ff95fc5a83fc30528",
        "652d0fe72afde40176bb2eb3481d56b3fe804f4cdf02db6f38b1d02e282b130a",
    ),
    (
        "file",
        277718891,
        "params/ocdbt.process_0/d/ac23fd467df222951dcb69b7136b4e4f",
        "e2665687c1fbfd54db9d4998e9f55a03f91e507fe3ff90d2ee89cee65834c333",
    ),
    ("file", 5407, "params/ocdbt.process_0/d/e3dd7fb4fa35901dac286f41adf77028"),
    ("file", 402, "params/ocdbt.process_0/manifest.ocdbt"),
    ("file", 118, "round_info.json"),
)
UID181_MODEL_HASH = "81f3865269675fceebf14c6f81f4b10d9fff82d3f5dbe91c75719df8c7747340"

UID130_NESTED_JAX_TREE = _tree(
    ("directory", 0, "merged"),
    ("directory", 0, "merged/assets"),
    ("directory", 0, "merged/assets/physical-intelligence"),
    ("directory", 0, "merged/assets/physical-intelligence/libero"),
    ("directory", 0, "merged/params"),
    ("directory", 0, "merged/params/d"),
    ("directory", 0, "merged/params/ocdbt.process_0"),
    ("directory", 0, "merged/params/ocdbt.process_0/d"),
    ("file", 2327, ".gitattributes"),
    ("file", 1943, "merged/assets/physical-intelligence/libero/norm_stats.json"),
    ("file", 258, "merged/params/_CHECKPOINT_METADATA"),
    ("file", 19947, "merged/params/_METADATA"),
    ("file", 2087, "merged/params/d/ef46ae811f00c73de5d9d8b6aca73392"),
    ("file", 117, "merged/params/manifest.ocdbt"),
    (
        "file",
        370577010,
        "merged/params/ocdbt.process_0/d/0496c7a9d125a9817f7a9c9bd93eb17d",
        "ea597b71001a483a1256f2efd3222c36c9baeb34a26e7fe3d6726e61cb04b911",
    ),
    (
        "file",
        1514498265,
        "merged/params/ocdbt.process_0/d/0d82ccfe6c60fbb664bf0ae1404a9c6d",
        "5b89a92ac765014d47d3cdc4cac3c2f1b2ac1b611d98c48f1de40b1db4385347",
    ),
    ("file", 209, "merged/params/ocdbt.process_0/d/5b49c31915efcb433684e2f31fc98042"),
    (
        "file",
        2175364316,
        "merged/params/ocdbt.process_0/d/5b5aeb6f7029c44cce42ccc91fcf9d73",
        "f0e46ac235a9f79d9d3d1a6d068b0e603b4171df1f0abf76c502f736dbe09d11",
    ),
    (
        "file",
        2175513663,
        "merged/params/ocdbt.process_0/d/67713312ad62886e4bde7d2eb230e256",
        "5568fb74fae5cdf11d04a0da2cdbec129b6a9dcbe373f77421fbdc7ddaa083a8",
    ),
    (
        "file",
        1096331571,
        "merged/params/ocdbt.process_0/d/72ef75981911f367ac3879ed66842552",
        "b9d447d117215bc6ed3bef14efcdbea04a44bf3c953111d14c43b7fa3ae3a465",
    ),
    ("file", 1077, "merged/params/ocdbt.process_0/d/96302abed73d3129da964812a39eb0ee"),
    (
        "file",
        28791290,
        "merged/params/ocdbt.process_0/d/a03d5d3697936b091cf3893deb91bb52",
        "59d747ccf592623c8d402c5b0e5dc2a8471a6475d3e8817140d6145c385516a1",
    ),
    (
        "file",
        2175351961,
        "merged/params/ocdbt.process_0/d/bf25d95dc975ca20e05ddc679a50d86b",
        "b52c9ba6a59de7c9404289b561107e694434470abe15d60bbf47895fed285855",
    ),
    (
        "file",
        1818585087,
        "merged/params/ocdbt.process_0/d/c3d075dee73a5d3f4abecff5459a7351",
        "7f4f42c1939f49fc723a797c6e75645e142c6763fd79a53f2c4b6ecbe4080019",
    ),
    ("file", 5344, "merged/params/ocdbt.process_0/d/e5a55319d098d80140fe2caa774dbd74"),
    ("file", 2044, "merged/params/ocdbt.process_0/d/f494f578b232cd178835516db312f5a9"),
    ("file", 489, "merged/params/ocdbt.process_0/manifest.ocdbt"),
    ("file", 5, "merged/round_info.json"),
    ("file", 118, "round_info.json"),
)
UID130_MODEL_HASH = "d5ba20f63ec661af87f2a5c5c92ea8644f88f5b063dd5f30bd060d477d36ac5c"


# ── Fingerprints: verbatim identical to submissions.model_hash stored in the
#    production DB ──────────────────────────────────────────────────────────


def test_uid221_model_hash() -> None:
    """A single LFS weights file. HF only gives ``lfs.oid``, not
    ``lfs.sha256``."""
    assert model_hash_from_hf_tree(UID221_PYTORCH_TREE) == UID221_MODEL_HASH


def test_uid181_model_hash() -> None:
    """4 LFS shards; the same directory also holds 6 small non-LFS files, which
    do not go into the fingerprint."""
    assert model_hash_from_hf_tree(UID181_JAX_TREE) == UID181_MODEL_HASH


def test_uid130_model_hash() -> None:
    """8 LFS shards — the sort-and-join step can only be verified with multiple
    files."""
    assert model_hash_from_hf_tree(UID130_NESTED_JAX_TREE) == UID130_MODEL_HASH


def test_model_hash_is_independent_of_listing_order() -> None:
    """The same weights in a different upload/return order must give the same
    fingerprint, otherwise re-uploading once would launder plagiarism."""
    assert (
        model_hash_from_hf_tree(list(reversed(UID130_NESTED_JAX_TREE)))
        == UID130_MODEL_HASH
    )


def test_model_hash_ignores_non_lfs_files() -> None:
    """Editing metadata such as ``round_info.json`` does not change the
    fingerprint — a plagiarist cannot escape by editing metadata."""
    tampered = [
        {**e, "size": e["size"] + 1} if e["path"].endswith("round_info.json") else e
        for e in UID130_NESTED_JAX_TREE
    ]
    assert model_hash_from_hf_tree(tampered) == UID130_MODEL_HASH


def test_different_weights_give_different_model_hash() -> None:
    """The three repos are pairwise different — a fingerprint collision means a
    plagiarism verdict, and colliding with the wrong person costs somebody else
    their money."""
    assert len({UID221_MODEL_HASH, UID181_MODEL_HASH, UID130_MODEL_HASH}) == 3


# ── Layout admission: all three repos were let through in production ──────


def test_uid221_layout_accepted() -> None:
    """The submission falsely rejected on 2026-08-14 must pass.
    ``.gitattributes`` is generated automatically by HF."""
    report = check_checkpoint_layout(_files(UID221_PYTORCH_TREE))
    assert report.ok, report.errors
    assert report.kind is CheckpointKind.PYTORCH
    assert report.warnings == ()
    assert report.counted_size_bytes == 1943 + 149 + 7233650272 + 119


def test_uid181_layout_accepted() -> None:
    report = check_checkpoint_layout(_files(UID181_JAX_TREE))
    assert report.ok, report.errors
    assert report.kind is CheckpointKind.JAX
    assert report.warnings == ()


def test_uid130_nested_layout_accepted() -> None:
    """The whole checkpoint is nested under ``merged/``, and it is still a legal
    submission.

    Nested by 1 level, within the 2 levels the evaluator can search, so there
    should be no warning.
    """
    report = check_checkpoint_layout(_files(UID130_NESTED_JAX_TREE))
    assert report.ok, report.errors
    assert report.kind is CheckpointKind.JAX
    assert report.warnings == ()


def test_real_repo_without_norm_stats_is_rejected() -> None:
    """The same real repo must be rejected once norm_stats is removed —
    admission is not a formality."""
    stripped = [
        f for f in _files(UID221_PYTORCH_TREE) if not f.path.endswith("norm_stats.json")
    ]
    report = check_checkpoint_layout(stripped)
    assert not report.ok
    assert [i.code for i in report.errors] == [FormatIssueCode.MISSING_NORM_STATS]
