"""模型指纹：把一个 HuggingFace 仓库的 LFS 内容折成一个 64 位十六进制串。

契约含义
--------
两份提交的指纹相同 = 同一份权重。后端据此判抄袭：同一轮里指纹撞了，
链上 ``commit_block`` 更早的是正版，晚的被标 ``rejected``。
所以这个算法必须能被矿工独立复算 —— 算出来和后端不一致，赔的是矿工的钱。

**空串是哨兵，不是指纹。** 后端拿到空串直接拒（``reject_reason=model_hash_empty``），
它的含义是「这个仓库没给出任何 LFS 指纹」，不是「指纹恰好为空」。

只搬了算法，没搬 I/O
--------------------
取仓库文件列表那半留在调用方（后端 scanner 用 HF tree API：token、超时、
重试、404 不重试的判定都在那边）。这个包一旦发网络请求，两边就无法再被
证明一致 —— 而"能被证明一致"是它唯一的存在理由。

HF 有两条列表接口，形状不同，这里都能拼：

- ``/api/models/<id>/tree/<rev>?recursive=true`` → 条目带 ``type`` 字段，
  直接用 :func:`model_hash_from_hf_tree`；
- ``/api/models/<id>`` 的 ``siblings`` / ``lfsFiles`` 回退路径 → **没有** ``type``
  字段，调用方自己用 :func:`extract_lfs_sha256` + :func:`fingerprint_lfs_sha256` 拼。

不负责
------
- 不取网络、不认 token、不重试；
- 不校验字符串是不是合法的 64 位 sha256（生产从不校验，这里保持一致）；
- 指纹撞了之后谁是正版由后端按 ``commit_block`` 判，不在这里。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any, Final

# 指纹算法的三个参数。改任何一个 = 链上所有历史指纹全部作废，
# 抄袭判定跟着全错。要动必须走 major，并且给出链上数据迁移方案。
_SEPARATOR: Final = "\n"
_ENCODING: Final = "utf-8"

# HF 的 LFS 字段用 ``sha256:<hex>`` 这种带前缀的字符串形态出现过，认它。
_SHA256_PREFIX: Final = "sha256:"


def extract_lfs_sha256(lfs_field: object) -> str:
    """从 HF tree 条目的 ``lfs`` 字段里取出那条 sha256；取不到返回空串。

    契约：HF 的 REST API 在 LFS 字段里**只给 ``oid``，不给 ``sha256`` 键**，
    而那个 ``oid`` 就是内容的 sha256 —— huggingface_hub 自己也这么映射
    （``BlobLfsInfo(sha256=lfs["oid"])``）。两个键都认，``sha256`` 优先。
    真实数据佐证：round 1 冠军仓库的 tree 响应里只有 ``oid``，
    所以 ``or oid`` 这条回退是**主路径**，不是防御性代码。

    ⚠️ 只认 ``dict`` 和 ``"sha256:..."`` 字符串两种形态，和生产一致。
    用 huggingface_hub SDK 的调用方拿到的 ``RepoFile.lfs`` 是 ``BlobLfsInfo``
    **对象**，两个分支都不匹配 → 静默得到空串 → 整个仓库算不出指纹。
    要走 SDK 就自己先转成 ``{"sha256": info.sha256}``。

    非字符串的 sha 值故意抛 ``TypeError`` 而不是静默跳过：生产里它会在
    ``"\\n".join`` 那一步炸，被调用方的 ``except Exception`` 接住并整条拒绝。
    静默跳过会把「该拒的」变成「用剩下的文件算个指纹」，那是改行为。
    """
    if isinstance(lfs_field, dict):
        sha = lfs_field.get("sha256", "") or lfs_field.get("oid", "")
        if not isinstance(sha, str):
            raise TypeError(f"LFS sha256 must be a string, got {type(sha).__name__}")
        return sha
    if isinstance(lfs_field, str) and lfs_field.startswith(_SHA256_PREFIX):
        return lfs_field[len(_SHA256_PREFIX) :]
    return ""


def fingerprint_lfs_sha256(lfs_sha256s: Iterable[str]) -> str:
    """一组 LFS sha256 → 单个指纹。空集合返回空串（= 没有指纹）。

    算法：字典序排序 → 换行连接 → UTF-8 编码 → sha256 → 小写十六进制。
    排序让文件顺序无关 —— 同一份权重换个上传顺序必须得到同一个指纹，
    否则矿工重传一次就"洗白"了抄袭。

    重复的 sha 不去重（同一份内容在仓库里出现两次会进两条），和生产一致。
    """
    ordered = sorted(lfs_sha256s)
    if not ordered:
        return ""
    return hashlib.sha256(_SEPARATOR.join(ordered).encode(_ENCODING)).hexdigest()


def model_hash_from_hf_tree(entries: Iterable[Mapping[str, Any]]) -> str:
    """HF tree API（``?recursive=true``）的返回 → 指纹。

    只看 ``type == "file"`` 的条目 —— HF 返回的目录 type 是 ``"directory"``，
    并且目录条目没有 ``lfs``，两层都挡得住。

    **只有 LFS 文件进指纹。** 走普通 git blob 的小文件（``config.json``、
    ``norm_stats.json``、``round_info.json``）不参与，所以指纹覆盖的是权重本身：
    改一行 ``round_info.json`` 不会换指纹，这是有意的 —— 抄袭者改元数据逃不掉。
    """
    return fingerprint_lfs_sha256(
        sha
        for entry in entries
        if entry.get("type") == "file" and (sha := extract_lfs_sha256(entry.get("lfs")))
    )
