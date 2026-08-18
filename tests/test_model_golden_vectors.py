"""黄金向量：链上真实发生过的模型指纹与仓库结构。改一条就是改历史。

三个 fixture 都是生产提交的 HuggingFace 仓库树，2026-08-17 从
``https://huggingface.co/api/models/<repo>/tree/<commit>?recursive=true`` 抓的，
``<commit>`` 就是链上公告、生产 DB 里记着的那个 revision：

- ``UID221_PYTORCH_TREE`` = uid 221 ``joseneto023dev/pi05-BRVeJ37DuryX@636dbaa3``
  —— 2026-08-14 被误拒的两个提交之一，扁平的 openpi PyTorch checkpoint；
- ``UID181_JAX_TREE`` = uid 181 ``OpenRd/pi05-vLUxPb8qTmGv@97da4275``
  —— 仓库根就是 JAX orbax checkpoint；
- ``UID130_NESTED_JAX_TREE`` = uid 130 ``swordswoman/pi05-j71bm5DVdD5X@ed38d896``
  —— 10.8 GB，整棵树嵌在 ``merged/`` 下面。

原样保留 ``type`` / ``size`` / ``path`` / ``lfs.oid`` 四项，去掉了判定用不到的
git blob ``oid`` 与 ``xetHash``。

期望指纹的来源
--------------
每条 ``*_MODEL_HASH`` 都是**生产 PostgreSQL 导出**
（``openroboto-backend/tests/fixtures/prod-data.sql``，round 1）里
``submissions.model_hash`` 存着的值，不是我们现算的。

抽出这个模块时把导出里 37 条「hf_commit 是完整 40 位 sha」的提交全跑了一遍：
**35 条逐字复现，0 条不一致**，另外 2 条的 revision 已经从 HF 上消失
（``joseneto023dev/pi05-6YM1X5xNCoQZ@a252578b``、
``joseneto023dev/pi05-aAxJBYQz5qqu@1a54a589`` → ``Invalid rev id``），
它们**不能**进黄金向量 —— 输入没了，测试会永远红。

这三棵树都通过了生产准入并进了评测队列，所以布局用例红了只有一种含义：
新规则会拒真实矿工，禁止上线。
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
    """把 (type, size, path[, lfs oid]) 还原成 HF tree API 的条目形状。"""
    out: list[dict[str, Any]] = []
    for entry in entries:
        node: dict[str, Any] = {"type": entry[0], "size": entry[1], "path": entry[2]}
        if len(entry) == 4:
            node["lfs"] = {"oid": entry[3], "size": entry[1], "pointerSize": 135}
        out.append(node)
    return out


def _files(tree: list[dict[str, Any]]) -> list[CheckpointFile]:
    """HF tree → 布局判定的输入。这段胶水故意留在调用方，不进协议包。"""
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


# ── 指纹：与生产 DB 里存着的 submissions.model_hash 逐字一致 ────────────────


def test_uid221_model_hash() -> None:
    """单个 LFS 权重文件。HF 只给 ``lfs.oid``，不给 ``lfs.sha256``。"""
    assert model_hash_from_hf_tree(UID221_PYTORCH_TREE) == UID221_MODEL_HASH


def test_uid181_model_hash() -> None:
    """4 个 LFS 分片；同目录下另有 6 个非 LFS 小文件，它们不进指纹。"""
    assert model_hash_from_hf_tree(UID181_JAX_TREE) == UID181_MODEL_HASH


def test_uid130_model_hash() -> None:
    """8 个 LFS 分片 —— 排序合并这一步只有多文件时才验得到。"""
    assert model_hash_from_hf_tree(UID130_NESTED_JAX_TREE) == UID130_MODEL_HASH


def test_model_hash_is_independent_of_listing_order() -> None:
    """同一份权重换个上传/返回顺序必须得到同一个指纹，否则重传一次就能洗白抄袭。"""
    assert (
        model_hash_from_hf_tree(list(reversed(UID130_NESTED_JAX_TREE)))
        == UID130_MODEL_HASH
    )


def test_model_hash_ignores_non_lfs_files() -> None:
    """改 ``round_info.json`` 这类元数据换不掉指纹 —— 抄袭者改元数据逃不掉。"""
    tampered = [
        {**e, "size": e["size"] + 1} if e["path"].endswith("round_info.json") else e
        for e in UID130_NESTED_JAX_TREE
    ]
    assert model_hash_from_hf_tree(tampered) == UID130_MODEL_HASH


def test_different_weights_give_different_model_hash() -> None:
    """三个仓库两两不同 —— 指纹撞了就会被判抄袭，撞错人代价是别人的钱。"""
    assert len({UID221_MODEL_HASH, UID181_MODEL_HASH, UID130_MODEL_HASH}) == 3


# ── 布局准入：这三个仓库在生产里都被放行过 ─────────────────────────────────


def test_uid221_layout_accepted() -> None:
    """2026-08-14 被误拒的提交必须通过。``.gitattributes`` 是 HF 自动生成的。"""
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
    """整棵 checkpoint 嵌在 ``merged/`` 下面，仍是合法提交。

    嵌套 1 层，在评测器能搜到的 2 层以内，所以不该有 warning。
    """
    report = check_checkpoint_layout(_files(UID130_NESTED_JAX_TREE))
    assert report.ok, report.errors
    assert report.kind is CheckpointKind.JAX
    assert report.warnings == ()


def test_real_repo_without_norm_stats_is_rejected() -> None:
    """同一个真实仓库，去掉 norm_stats 就必须被拒 —— 准入不是走过场。"""
    stripped = [
        f for f in _files(UID221_PYTORCH_TREE) if not f.path.endswith("norm_stats.json")
    ]
    report = check_checkpoint_layout(stripped)
    assert not report.ok
    assert [i.code for i in report.errors] == [FormatIssueCode.MISSING_NORM_STATS]
