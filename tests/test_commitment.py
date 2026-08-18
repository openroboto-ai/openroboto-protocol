"""commitment 编解码的契约测试。

前半是**黄金向量** —— 链上已经发生过的输入输出对，是历史不是期望，
改一条就是改写链上事实（本该放进 tests/test_golden_vectors.py，
该文件此刻被 seed 模块占用，合并时把「黄金向量」一节整体搬过去即可）。
后半是各类畸形输入的分类：解不出来时必须**说清楚是哪一类**，
而不是像旧扫链器那样统统打成一句 `decode FAILED`。
"""

from __future__ import annotations

import pytest

from openroboto_protocol.commitment import (
    MAX_COMMITMENT_BYTES,
    CommitmentDecodeError,
    CommitmentPayload,
    CommitmentTooLargeError,
    DecodeFailure,
    decode,
    encode,
)

# ═══════════════════════════════════════════════════════════════════
# 黄金向量 —— 链上事实，改动即改写历史
# ═══════════════════════════════════════════════════════════════════

# GV-1：netuid 80，区块 8808332，`Commitments.set_commitment`，`Data::BigRaw`。
# 出处：Taostats extrinsic API，2026-08-17 复核，链上原始字节 295 个。
# 这是「UID 71 四次提交」里**唯一真正发给本子网**的那次，后端当时正常解出。
GV1_BLOCK = 8808332
GV1_JSON = (
    '{"s":"5D33cWAUBDJLKEP6c2hCYxumbGKzV92qrbDuscmGbsBoEmiQ",'
    '"h":"94f06bc414624cf0935730f43a5d761df16b5e51d9327388287b280701cd0a22",'
    '"c":"09ecbfb798b7ab080fd5f54b60b3830d7e1a52e0",'
    '"r":1,'
    '"i":"kyleab/pi05-scmGbsBoEmiQ",'
    '"b":"ad2b5d0dee272c4a7459a737697e1cb538899b39e054fc8af40cf2d81cc9f310",'
    '"bb":8808331}'
)
GV1_BYTES = GV1_JSON.encode("utf-8")
GV1_PAYLOAD = CommitmentPayload(
    hotkey_ss58="5D33cWAUBDJLKEP6c2hCYxumbGKzV92qrbDuscmGbsBoEmiQ",
    block_hash="94f06bc414624cf0935730f43a5d761df16b5e51d9327388287b280701cd0a22",
    hf_commit="09ecbfb798b7ab080fd5f54b60b3830d7e1a52e0",
    round_num=1,
    hf_repo_id="kyleab/pi05-scmGbsBoEmiQ",
    # 链上存的是不带 0x 的裸哈希，解码时补 0x —— 去重键依赖这个形式。
    burn_tx_hash="0xad2b5d0dee272c4a7459a737697e1cb538899b39e054fc8af40cf2d81cc9f310",
    burn_block=8808331,
)

# GV-2：netuid **126**，区块 8797897，`Data::Raw119`。同一个 hotkey 发的，
# 但收件人是**别的子网**。这条正是「Raw119 后端解不出来」误判的源头：
# Taostats 的 netuid 过滤参数被静默忽略，离线分析把它算成了本子网的提交。
GV2_RAW119_HEX = (
    "0xdfb254add593413b116e2d3e45a57944592cf0a59cbbc3a3d8b5ddb51c355c0a"
    "51f9c84fe283d5806026bfd99fb6739c9d3b2974d10e14eb99718b425218ad1dae"
    "0dc8a8af925e037049a058ae5e071d598c4855cb21d052dbd90d3c41455ca68fa7"
    "b13a0282be1588607ba05adb7a344b5908f03bdd6a"
)


def test_gv1_on_chain_bytes_decode_to_the_recorded_submission() -> None:
    """链上那 295 个字节，必须解出当时后端入库的那条提交。"""
    assert len(GV1_BYTES) == 295
    result = decode(GV1_BYTES)
    assert result.payload == GV1_PAYLOAD
    assert result.commit_block == 0  # 裸字节没有链信封
    assert result.data_variant == ""


def test_gv1_reencodes_to_the_exact_on_chain_bytes() -> None:
    """编解码互为逆运算：键序、紧凑分隔符、`bb` 的整数形式逐字节一致。

    这条一旦红，说明编码格式漂了 —— 新矿工写上链的字节和历史不再同形。
    """
    assert encode(GV1_PAYLOAD) == GV1_BYTES


def test_gv1_through_the_sdk_envelope() -> None:
    """走 `get_commitment_metadata()` 的返回形状，结果必须完全一样。"""
    raw = {
        "deposit": 0,
        "block": GV1_BLOCK,
        "info": {"fields": [{"BigRaw": GV1_JSON}]},
    }
    result = decode(raw)
    assert result.payload == GV1_PAYLOAD
    assert result.commit_block == GV1_BLOCK
    assert result.data_variant == "BigRaw"


def test_gv1_through_the_indexer_envelope() -> None:
    """走索引器（Taostats/subsquid）的 `__kind` / `value` 十六进制形状。"""
    raw = {"info": {"fields": [{"__kind": "BigRaw", "value": "0x" + GV1_BYTES.hex()}]}}
    result = decode(raw)
    assert result.payload == GV1_PAYLOAD
    assert result.data_variant == "BigRaw"


def test_gv2_foreign_subnet_raw119_is_classified_not_crashed() -> None:
    """别的子网的 Raw119：明确报 NOT_UTF8，不崩、也不静默吞掉。"""
    assert len(bytes.fromhex(GV2_RAW119_HEX[2:])) == 119  # RawN 的 N 就是字节数

    raw = {"block": 8797897, "info": {"fields": [{"Raw119": GV2_RAW119_HEX}]}}
    with pytest.raises(CommitmentDecodeError) as exc:
        decode(raw)
    assert exc.value.reason is DecodeFailure.NOT_UTF8


def test_gv3_foreign_raw82_matches_the_production_error() -> None:
    """复现生产日志里那 4 条 `can't decode byte 0xec in position 5`。

    出处：`_salvage/prod-logs-tail/chain_scanner.log`（2026-08-14），
    字段是 `Raw82`，内容是个负载均衡器域名 —— 又一条别的子网的 payload。
    这里用日志留下的前缀字节复现同一个分类。
    """
    prefix = "0x0c000d3401ec646f67656c617965722d"
    with pytest.raises(CommitmentDecodeError) as exc:
        decode({"info": {"fields": [{"Raw82": prefix}]}})
    assert exc.value.reason is DecodeFailure.NOT_UTF8
    assert "position 5" in exc.value.detail


def test_gv4_hotkey_without_commitment_returns_empty_string() -> None:
    """生产实测：没提交过的 hotkey，SDK 返回**空串**而不是 `None`。

    2026-08-14 的 3000 行扫链日志里，772 条 decode FAILED 有 768 条是这个 ——
    正常状态被打成 WARNING，把真信号埋了。必须能和真故障区分开。
    """
    with pytest.raises(CommitmentDecodeError) as exc:
        decode("")
    assert exc.value.reason is DecodeFailure.NO_COMMITMENT


# ═══════════════════════════════════════════════════════════════════
# encode
# ═══════════════════════════════════════════════════════════════════


def test_encode_strips_0x_from_both_hashes() -> None:
    """链上存的是裸哈希。`h` 和 `b` 带 `0x` 进来都要去掉，否则字节和历史不一致。"""
    blob = encode(
        CommitmentPayload(
            hotkey_ss58="5Dxxx",
            block_hash="0xaabb",
            hf_commit="c" * 40,
            round_num=2,
            hf_repo_id="u/r",
            burn_tx_hash="0xccdd",
            burn_block=7,
        )
    )
    assert b'"h":"aabb"' in blob
    assert b'"b":"ccdd"' in blob


def test_encode_writes_null_for_missing_burn_block() -> None:
    """`bb` 为 0 时写 `null` 是历史形状，不是笔误。"""
    blob = encode(
        CommitmentPayload("5D", "h", "c", 1, "u/r", "", burn_block=0),
    )
    assert b'"bb":null' in blob
    assert b'"b":""' in blob


def test_encode_keeps_non_ascii_repo_id_verbatim() -> None:
    """`ensure_ascii=False`：非 ASCII 原样写 UTF-8，不转 `\\uXXXX`。

    转义会让同一个仓库名产生两种字节，链上比对就对不上了。
    """
    blob = encode(CommitmentPayload("5D", "", "", 1, "用户/模型", "", 0))
    assert "用户/模型".encode() in blob


def test_encode_rejects_payload_that_cannot_land_on_chain() -> None:
    """>512 字节上不了链，而 burn 已经花掉了 —— 必须在花钱前炸。"""
    with pytest.raises(CommitmentTooLargeError) as exc:
        encode(CommitmentPayload("5D", "h", "c", 1, "u/" + "x" * 600, "", 0))
    assert exc.value.size > MAX_COMMITMENT_BYTES
    assert "hf_repo_id" in str(exc.value)


def test_encode_accepts_payload_exactly_at_the_limit() -> None:
    """边界是 512 本身可以，不是 511。"""
    fixed = len(encode(CommitmentPayload("5D", "h", "c", 1, "", "", 0)))
    repo_id = "x" * (MAX_COMMITMENT_BYTES - fixed)
    assert len(encode(CommitmentPayload("5D", "h", "c", 1, repo_id, "", 0))) == (
        MAX_COMMITMENT_BYTES
    )


# ═══════════════════════════════════════════════════════════════════
# decode —— 信封形状
# ═══════════════════════════════════════════════════════════════════


def test_decode_accepts_bytearray_and_str_payloads() -> None:
    """SDK 版本不同，payload 可能是 bytes、bytearray 或已解码的 str。"""
    assert decode(bytearray(GV1_BYTES)).payload == GV1_PAYLOAD
    assert decode(GV1_JSON).payload == GV1_PAYLOAD


def test_decode_accepts_raw_bytes_field_values() -> None:
    """字段值本身就是字节（部分 SDK 版本不做解码）。"""
    raw = {"info": {"fields": [{"BigRaw": bytearray(GV1_BYTES)}]}}
    assert decode(raw).payload == GV1_PAYLOAD


def test_decode_accepts_int_tuple_field_values() -> None:
    """老 SDK 把字节返回成（可能嵌套一层的）整数元组。"""
    tail = tuple(GV1_BYTES[4:])
    raw = {"info": {"fields": ({"Raw200": (tuple(GV1_BYTES[:4]), *tail)},)}}
    assert decode(raw).payload == GV1_PAYLOAD


def test_decode_accepts_doubly_nested_field_groups() -> None:
    """`fields` 有时是 `[[{...}]]` 而不是 `[{...}]`，两层都要认。"""
    raw = {"info": {"fields": [[{"BigRaw": GV1_JSON}]]}}
    assert decode(raw).payload == GV1_PAYLOAD


def test_decode_falls_back_to_a_bare_payload_dict() -> None:
    """调用方直接把解好的 dict 递进来（老代码的兜底路径）。"""
    result = decode({"block": 42, "r": 3, "i": "u/r"})
    assert result.payload.round_num == 3
    assert result.payload.hf_repo_id == "u/r"
    assert result.commit_block == 42
    assert result.data_variant == ""


def test_decode_reads_commit_block_from_a_string_envelope() -> None:
    """某些 SDK / 索引器把区块号给成字符串。"""
    raw = {"block": "8808332", "info": {"fields": [{"BigRaw": GV1_JSON}]}}
    assert decode(raw).commit_block == 8808332


def test_decode_skips_unusable_fields_and_keeps_looking() -> None:
    """空值、认不出来的类型、非 Raw 变体，都跳过继续找。"""
    raw = {
        "info": {
            "fields": [
                123,  # 整个 group 不是 mapping 也不是序列
                ["not-a-mapping"],
                {"netuid": 80},  # 不是 Data 变体
                {"__kind": "TimelockEncrypted", "value": "0xdead"},  # 别的子网在用
                {"Raw0": ""},  # 空值
                {"Raw8": None},  # 认不出来的类型
                {"__kind": "Raw0", "value": ""},  # 索引器形状的空值
                {"BigRaw": GV1_JSON},
            ]
        }
    }
    assert decode(raw).payload == GV1_PAYLOAD


def test_decode_rejects_oversized_ints_in_a_tuple_field() -> None:
    """元组里混进非字节的东西（>255 的整数、`None`）：这不是字节流，跳过。"""
    with pytest.raises(CommitmentDecodeError) as exc:
        decode({"info": {"fields": [{"Raw4": (999, None)}]}})
    assert exc.value.reason is DecodeFailure.NO_COMMITMENT


@pytest.mark.parametrize(
    ("raw", "detail_hint"),
    [
        (None, "None"),
        (b"", "empty"),
        ("", "empty"),
        (12345, "int"),
        ({"info": "not-a-mapping"}, "no Raw"),
        ({"info": {"fields": "not-a-sequence"}}, "no Raw"),
        ({"info": {"fields": []}}, "no Raw"),
        ({"deposit": 0, "block": 1}, "no Raw"),
    ],
)
def test_decode_reports_no_commitment_for_empty_envelopes(
    raw: object, detail_hint: str
) -> None:
    """「链上没有这条提交」是正常状态，必须和真故障分开 —— 生产上它是 99% 的量。"""
    with pytest.raises(CommitmentDecodeError) as exc:
        decode(raw)
    assert exc.value.reason is DecodeFailure.NO_COMMITMENT
    assert detail_hint in exc.value.detail


# ═══════════════════════════════════════════════════════════════════
# decode —— 内容分类
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("0xzz", DecodeFailure.NOT_HEX),
        ("0xec", DecodeFailure.NOT_UTF8),
        ("not json", DecodeFailure.NOT_JSON),
        ("[1,2,3]", DecodeFailure.NOT_OBJECT),
        ('{"foo":1}', DecodeFailure.UNKNOWN_SCHEMA),
        ("{}", DecodeFailure.UNKNOWN_SCHEMA),
    ],
)
def test_decode_classifies_每一类畸形输入(value: str, reason: DecodeFailure) -> None:
    """调用方要能按 reason 分桶统计：噪音归噪音，版本漂移归版本漂移。"""
    with pytest.raises(CommitmentDecodeError) as exc:
        decode({"info": {"fields": [{"BigRaw": value}]}})
    assert exc.value.reason is reason


def test_unknown_schema_is_the_real_version_drift_signal() -> None:
    """一个已知键都没有 = 对方在用我们不认识的键名。错误里要带上键名好排查。"""
    with pytest.raises(CommitmentDecodeError) as exc:
        decode('{"hotkey":"5D","repo":"u/r"}')
    assert exc.value.reason is DecodeFailure.UNKNOWN_SCHEMA
    assert "hotkey" in exc.value.detail


def test_decode_error_message_carries_the_reason() -> None:
    """日志里只印 str(exc) 也要能看出分类。"""
    with pytest.raises(CommitmentDecodeError, match="no_commitment: raw is None"):
        decode(None)
    with pytest.raises(CommitmentDecodeError, match=r"^not_utf8$"):
        raise CommitmentDecodeError(DecodeFailure.NOT_UTF8)


# ═══════════════════════════════════════════════════════════════════
# decode —— 字段级容错
# ═══════════════════════════════════════════════════════════════════


def test_decode_defaults_every_missing_key() -> None:
    """minor 版本新增的键，老数据缺它必须还能解出来（AGENTS 规则 ②）。"""
    payload = decode('{"i":"u/r"}').payload
    assert payload == CommitmentPayload("", "", "", 0, "u/r", "", 0)


def test_decode_coerces_wrong_typed_fields_instead_of_crashing() -> None:
    """矿工写错类型不该让整轮扫链炸；坏字段退化成默认值，后端各自的校验会拒。"""
    payload = decode('{"s":5,"i":null,"r":"7","bb":"x","c":true}').payload
    assert payload.hotkey_ss58 == ""
    assert payload.hf_repo_id == ""
    assert payload.round_num == 7  # 字符串数字照收
    assert payload.burn_block == 0  # 非数字退默认
    assert payload.hf_commit == ""  # bool 不是字符串
    # bool 在 Python 里是 int 的子类，不能顺手当成 1/0 收进来
    assert decode('{"r":true,"i":"u/r"}').payload.round_num == 0


def test_decode_does_not_double_prefix_an_already_prefixed_burn_hash() -> None:
    """有客户端会自己带上 `0x`。补两次会让去重键和历史对不上。"""
    assert decode('{"b":"0xabc"}').payload.burn_tx_hash == "0xabc"
    assert decode('{"b":"abc"}').payload.burn_tx_hash == "0xabc"
    assert decode('{"b":"","r":1}').payload.burn_tx_hash == ""


def test_decode_leaves_block_hash_unprefixed() -> None:
    """⚠️ 红线：`h` 是矿工自报值，**不补 0x**、不做任何加工。

    `derive_seed()` 对 `f"{block_hash}:{round}:{drand}"` 做 sha256，
    多两个字符就是另一个种子、另一批任务。调用方必须用链上真哈希覆盖它，
    这里替它「顺手补个 0x」等于悄悄改了种子。
    """
    assert decode(GV1_BYTES).payload.block_hash == GV1_PAYLOAD.block_hash
    assert not decode(GV1_BYTES).payload.block_hash.startswith("0x")


def test_payload_is_frozen() -> None:
    """七个字段必须同源。可变的话就能出现「这个 block_hash 配那个 burn_tx」。"""
    with pytest.raises(AttributeError):
        decode(GV1_BYTES).payload.round_num = 99  # type: ignore[misc]
