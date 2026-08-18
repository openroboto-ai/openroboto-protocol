"""链上 commitment payload 的编解码 —— 矿工写、后端读，双方必须逐字节一致。

矿工把一次提交的元数据 JSON 塞进 Bittensor 的 `Commitments.set_commitment`
（`Data::BigRaw`），后端扫链读回来。**这一个 JSON 就是提交本身** —— 它决定
哪个 HF 仓库被评测、算哪一轮、拿哪笔 burn 交易去核对。编码端和解码端对不上，
矿工就是烧了 TAO 没人看见。

## 链上 JSON 的形状

键名压到一个字母不是为了好看：`Data::BigRaw` 上限 512 字节，一个 hf_repo_id
加两个 64 位十六进制哈希已经吃掉一半。改键名 = major。

| 键 | 字段 | 契约含义 |
|---|---|---|
| `s` | `hotkey_ss58` | 矿工 hotkey，SS58 全串 |
| `h` | `block_hash` | **矿工自报的**提交区块哈希，不可信，见下 |
| `c` | `hf_commit` | HuggingFace commit SHA，40 位十六进制 |
| `r` | `round_num` | 轮次号 |
| `i` | `hf_repo_id` | HuggingFace 仓库 id，如 `kyleab/pi05-scmGbsBoEmiQ` |
| `b` | `burn_tx_hash` | burn 交易哈希，**链上不带 `0x`** |
| `bb` | `burn_block` | burn 所在区块；0 时编码成 `null` |

## 两个必须记住的不对称

1. **`h` 是矿工自报的，不能直接喂给种子派生。** 扫链方拿到 `commit_block`
   之后必须用 `get_block_hash(commit_block)` 覆盖它 —— 链给的那个带 `0x`，
   矿工自报的那个不带。而 `derive_seed()` 是对字符串做 sha256，
   多两个字符就是另一个种子、另一批评测任务。本模块**原样返回**矿工自报值
   （不补 `0x`），覆盖是调用方的责任。
2. **`b` 反过来：解码时补 `0x`，编码时去 `0x`。** 生产两个消费方
   （backend/chain_scanner.py、scripts/seed_data.py）都这么做，去重键依赖它。

## 变体名是长度，不是版本号

SDK 回来的字段键长这样：`BigRaw` / `Raw119` / `Raw82`。`RawN` 里的 N 是
**字节数**，128 字节以内用 `RawN`，超了才用 `BigRaw`。它不携带任何客户端版本信息。

2026-08 的结论「UID 71 四次提交三次是 Raw119，旧版 rt.py 走了 Raw 分支」是误判。
链上原始数据（Taostats extrinsic API，2026-08-17 复核）：

    8797897  Raw119  netuid=126   ← 别的子网
    8808332  BigRaw  netuid=80    ← 本子网，后端正常解出
    8830097  Raw119  netuid=126   ← 别的子网
    8830168  Raw119  netuid=126   ← 别的子网

同一个 hotkey 在多个子网上都发 commitment；那三条 Raw119 是 netuid 126 的
二进制 payload，本来就不该被 netuid 80 的后端看见。误判的根因是 Taostats
的 `netuid` 查询参数**被静默忽略**（实测：带 `netuid=80` 仍返回 123/126/68/47
等子网的记录），离线分析脚本按 signer_address 拉数据时把别的子网算进来了。
矿工没有白烧 TAO。

因此本模块**不为 `Raw119` 做任何特殊处理**：任何 `Raw*` / `BigRaw` 变体都按同一套
JSON 解，解不出来时用 `DecodeFailure` 说清楚是哪一类，把变体名一并带回给调用方
记日志。真正的版本漂移信号是 `UNKNOWN_SCHEMA`（是 JSON 对象但一个已知键都没有），
不是变体名。

## 不负责什么

不碰链、不查 netuid、不做 burn 校验、不覆盖 `h`、不判断轮次是否当期 ——
那些都要 I/O 或后端配置，留在调用方。

## 谁消费它

* 矿工 CLI（`rt.py` / `openroboto submit`）—— `encode()`，烧 TAO 之前调；
* 后端扫链器 —— `decode()`，`get_commitment_metadata()` 的返回直接喂进来；
* 历史回填脚本 —— `decode()`，喂索引器返回的 `__kind`/`value` 形状。

## 最小验证

`uv run pytest tests/test_commitment.py` —— 其中 GV-1 是链上区块 8808332
那 295 个真实字节，编解码互为逆运算这条一旦红，就是格式漂了。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

# `Data::BigRaw` 的硬上限。超过这个长度的 extrinsic 会被链拒绝 ——
# 而 burn 是在提交之前就花掉的，所以必须在花钱之前发现。
MAX_COMMITMENT_BYTES: Final = 512

# 链上 JSON 的全部已知键。解码时「一个都不认识」= 对方不是本协议的客户端。
PAYLOAD_KEYS: Final = ("s", "h", "c", "r", "i", "b", "bb")


class DecodeFailure(StrEnum):
    """解码失败的分类 —— 调用方靠它区分「噪音」和「真出事了」。

    生产实测（2026-08-14，3000 行扫链日志）：772 条解码失败里 768 条是
    `NO_COMMITMENT`（该 hotkey 链上根本没提交过），4 条是 `NOT_UTF8`
    （别的子网的二进制 payload）。旧扫链器把这两类和真故障一起打成
    `decode FAILED` WARNING，于是 768 条噪音把真信号埋了。
    """

    NO_COMMITMENT = "no_commitment"
    """链上没有这条 commitment：`None`、空串、或 `info.fields` 里没有可用字段。
    这是**正常状态**，不是错误 —— 绝大多数 hotkey 从没提交过。"""

    NOT_HEX = "not_hex"
    """字段值声明成 `0x...` 但不是合法十六进制。"""

    NOT_UTF8 = "not_utf8"
    """字节流不是 UTF-8。基本可以断定是别的子网的二进制 payload。"""

    NOT_JSON = "not_json"
    """是 UTF-8 文本但不是合法 JSON。"""

    NOT_OBJECT = "not_object"
    """是合法 JSON 但顶层不是对象（比如是数组或数字）。"""

    UNKNOWN_SCHEMA = "unknown_schema"
    """是 JSON 对象，但 `PAYLOAD_KEYS` 一个都不出现。
    **这才是客户端版本漂移的真信号** —— 对方在用一套我们不认识的键名。"""


class CommitmentDecodeError(ValueError):
    """解码失败。`reason` 用于分类统计，`detail` 用于人读。"""

    def __init__(self, reason: DecodeFailure, detail: str = "") -> None:
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason = reason
        self.detail = detail


class CommitmentTooLargeError(ValueError):
    """编码结果超过 `MAX_COMMITMENT_BYTES`，这条 commitment 上不了链。

    必须在矿工烧 TAO **之前**抛出：burn 先发生、提交后发生，
    提交失败不会把钱退回来。实际唯一可能撑爆的字段是 `hf_repo_id`。
    """

    def __init__(self, size: int) -> None:
        super().__init__(
            f"commitment payload {size} bytes > {MAX_COMMITMENT_BYTES} limit; "
            f"shorten hf_repo_id"
        )
        self.size = size


@dataclass(frozen=True)
class CommitmentPayload:
    """一次提交的全部链上字段 —— 必须同源，所以绑成一个不可变整体。

    拆开传参数会让「这个 block_hash 配那个 burn_tx_hash」变成可能，
    而这两者一旦错配，seed 和 burn 校验会各自指向不同的提交。
    """

    hotkey_ss58: str
    """矿工 hotkey（SS58）。链上 `s`。"""

    block_hash: str
    """**矿工自报的**提交区块哈希，链上 `h`，不带 `0x`。

    ⚠️ 不可信。扫链方必须用 `get_block_hash(commit_block)` 覆盖之后
    再拿去派生种子，否则矿工可以自选种子。"""

    hf_commit: str
    """HuggingFace commit SHA，链上 `c`，40 位十六进制。
    生产上出现过空值（矿工没填 commit URL），本模块不拦，由后端 HF 校验拒。"""

    round_num: int
    """轮次号，链上 `r`。缺失按 0 处理 —— 0 永远不等于当期轮次，会被后端拒掉。"""

    hf_repo_id: str
    """HuggingFace 仓库 id，链上 `i`，如 `kyleab/pi05-scmGbsBoEmiQ`。"""

    burn_tx_hash: str
    """burn 交易哈希，链上 `b`。

    链上存的**不带** `0x`；`decode()` 解出来时补上 `0x`，`encode()` 写回时去掉。
    去重键（hotkey + round + burn_tx_hash）依赖这个规范化形式。"""

    burn_block: int
    """burn 所在区块，链上 `bb`。0 表示矿工没报，编码时写成 `null`。"""


@dataclass(frozen=True)
class DecodedCommitment:
    """`decode()` 的结果：矿工自报的 payload + 链信封给的、可信的那部分。

    两者分开放是因为信任级别不同：`payload` 全是矿工写的，
    `commit_block` 是链节点返回的。
    """

    payload: CommitmentPayload
    """矿工写在链上的那个 JSON。全部字段都是自报值。"""

    commit_block: int
    """commitment 所在区块号，来自链返回的元数据信封（可信）。
    调用方拿它去 `get_block_hash()` 换真区块哈希。输入里没有信封时为 0。"""

    data_variant: str
    """SDK 报出的 `Data` 变体名：`BigRaw` / `Raw119` / …。

    只是**字节长度标签**，不是版本号（见模块 docstring）。带回来是为了让
    调用方能在日志里看见形状变化，不要拿它做分支判断。
    直接传字节或 dict 时为空串。"""


def encode(payload: CommitmentPayload) -> bytes:
    """把 payload 编成上链的字节。键序和分隔符是契约的一部分，不能改。

    `json.dumps(separators=(",", ":"))` 的紧凑形式 + 固定键序 `s,h,c,r,i,b,bb`
    是链上历史数据的既成事实（见 tests/test_golden_vectors.py）。
    换个键序不会解码失败，但字节不再一致 —— 任何按字节比对的核对都会炸。

    超过 512 字节抛 `CommitmentTooLargeError`：这样的 payload 上不了链，
    而矿工此时已经烧过 TAO 了。
    """
    data = {
        "s": payload.hotkey_ss58,
        "h": _strip_0x(payload.block_hash),
        "c": payload.hf_commit,
        "r": payload.round_num,
        "i": payload.hf_repo_id,
        "b": _strip_0x(payload.burn_tx_hash),
        # 0 写成 null 是历史形状，不是笔误。
        "bb": payload.burn_block or None,
    }
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(blob) > MAX_COMMITMENT_BYTES:
        raise CommitmentTooLargeError(len(blob))
    return blob


def decode(raw: object) -> DecodedCommitment:
    """把扫链拿到的东西解成 `DecodedCommitment`，失败抛 `CommitmentDecodeError`。

    接受 SDK 实际会返回的全部形状（生产上一个都不能少）：

    * `bytes` / `str` —— 已经是 payload 本身；
    * `{"deposit":…, "block": N, "info": {"fields": [{"BigRaw": …}]}}`
      —— `get_commitment_metadata()` 的返回；字段值可能是 JSON 文本、
      `0x` 十六进制串、或整数元组，取决于 SDK 版本；
    * `{"__kind": "BigRaw", "value": "0x…"}` —— 索引器（Taostats/subsquid）的形状；
    * 直接就是 payload 的 dict —— 老代码的兜底路径。

    只取**第一个**可用的 `Raw*` / `BigRaw` 字段。链上一次 commitment 就一个字段，
    出现多个说明形状已经不是我们认识的了，与其猜不如让上面那条报出来。
    """
    if raw is None:
        raise CommitmentDecodeError(DecodeFailure.NO_COMMITMENT, "raw is None")

    if isinstance(raw, bytes | bytearray):
        if not raw:
            raise CommitmentDecodeError(DecodeFailure.NO_COMMITMENT, "empty bytes")
        return DecodedCommitment(_payload_from_bytes(bytes(raw)), 0, "")

    if isinstance(raw, str):
        # 生产实测：没有 commitment 的 hotkey，SDK 返回的是空串而不是 None。
        if not raw:
            raise CommitmentDecodeError(DecodeFailure.NO_COMMITMENT, "empty string")
        return DecodedCommitment(_payload_from_bytes(raw.encode("utf-8")), 0, "")

    if isinstance(raw, Mapping):
        commit_block = _as_int(raw.get("block"))
        for variant, value in _iter_data_fields(raw):
            blob = _field_bytes(value)
            if blob:
                return DecodedCommitment(
                    _payload_from_bytes(blob), commit_block, variant
                )
        # 兜底：信封里没有字段，但这个 dict 自己就长得像 payload。
        if _has_known_key(raw):
            return DecodedCommitment(_payload_from_mapping(raw), commit_block, "")
        raise CommitmentDecodeError(
            DecodeFailure.NO_COMMITMENT, "no Raw*/BigRaw field in info.fields"
        )

    raise CommitmentDecodeError(
        DecodeFailure.NO_COMMITMENT, f"unsupported type {type(raw).__name__}"
    )


# ─── 内部实现 ────────────────────────────────────────────────


def _strip_0x(value: str) -> str:
    return value[2:] if value.startswith("0x") else value


def _as_str(value: object) -> str:
    """非字符串一律当空 —— 矿工写了 `"i": 123` 这种，后端 HF 校验会拒。"""
    return value if isinstance(value, str) else ""


def _as_int(value: object, default: int = 0) -> int:
    """`None` / 非数字 → default。链上 `bb` 缺省就是 `null`。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _has_known_key(data: Mapping[Any, Any]) -> bool:
    return any(key in data for key in PAYLOAD_KEYS)


def _iter_data_fields(raw: Mapping[Any, Any]) -> Iterator[tuple[str, object]]:
    """从链信封里掏出 (变体名, 字段值)。

    `info.fields` 在不同 SDK 版本里是 `[{...}]` 或 `[[{...}]]`，两层都要认。
    """
    info = raw.get("info")
    if not isinstance(info, Mapping):
        return
    fields = info.get("fields")
    if not isinstance(fields, list | tuple):
        return
    for group in fields:
        entries = (group,) if isinstance(group, Mapping) else group
        if not isinstance(entries, list | tuple):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            # 索引器形状：{"__kind": "BigRaw", "value": "0x…"}
            kind = entry.get("__kind")
            if isinstance(kind, str) and _is_data_variant(kind):
                yield kind, entry.get("value")
                continue
            # SDK 形状：{"BigRaw": …}
            for key, value in entry.items():
                if isinstance(key, str) and _is_data_variant(key):
                    yield key, value


def _is_data_variant(key: str) -> bool:
    """`RawN`（N = 字节数，≤128）或 `BigRaw`（>128）。N 不是版本号。"""
    return key == "BigRaw" or key.startswith("Raw")


def _field_bytes(value: object) -> bytes | None:
    """把字段值还原成字节。认不出来返回 `None`（继续看下一个字段）。"""
    if isinstance(value, bytes | bytearray):
        return bytes(value)
    if isinstance(value, str):
        if value.startswith("0x"):
            try:
                return bytes.fromhex(value[2:])
            except ValueError as exc:
                raise CommitmentDecodeError(DecodeFailure.NOT_HEX, str(exc)) from exc
        return value.encode("utf-8")
    if isinstance(value, list | tuple):
        flat: list[int] = []
        for item in value:
            if isinstance(item, list | tuple):
                flat.extend(i for i in item if isinstance(i, int))
            elif isinstance(item, int):
                flat.append(item)
        try:
            return bytes(flat)
        except ValueError:
            return None
    return None


def _payload_from_bytes(blob: bytes) -> CommitmentPayload:
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CommitmentDecodeError(DecodeFailure.NOT_UTF8, str(exc)) from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CommitmentDecodeError(DecodeFailure.NOT_JSON, str(exc)) from exc
    if not isinstance(data, dict):
        raise CommitmentDecodeError(
            DecodeFailure.NOT_OBJECT, f"top level is {type(data).__name__}"
        )
    if not _has_known_key(data):
        raise CommitmentDecodeError(
            DecodeFailure.UNKNOWN_SCHEMA, f"keys={sorted(map(str, data))[:8]}"
        )
    return _payload_from_mapping(data)


def _payload_from_mapping(data: Mapping[Any, Any]) -> CommitmentPayload:
    """缺键一律走默认值 —— minor 版本加了新键的老数据必须还能解出来。"""
    burn_tx_hash = _as_str(data.get("b"))
    if burn_tx_hash and not burn_tx_hash.startswith("0x"):
        burn_tx_hash = f"0x{burn_tx_hash}"
    return CommitmentPayload(
        hotkey_ss58=_as_str(data.get("s")),
        # 不补 0x：矿工自报值原样返回，见模块 docstring 的不对称说明。
        block_hash=_as_str(data.get("h")),
        hf_commit=_as_str(data.get("c")),
        round_num=_as_int(data.get("r")),
        hf_repo_id=_as_str(data.get("i")),
        burn_tx_hash=burn_tx_hash,
        burn_block=_as_int(data.get("bb")),
    )
