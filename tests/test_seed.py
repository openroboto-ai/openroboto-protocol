"""``openroboto_protocol.seed`` 的契约测试。

链上真实向量在 ``test_golden_vectors.py``；这里测的是**性质与边界**：
公式的确定性、值域、对输入写法的敏感性，以及哪些负例必须拒绝。
"""

from __future__ import annotations

import dataclasses

import pytest

from openroboto_protocol.seed import (
    DRAND_API,
    DRAND_CHAIN_HASH,
    SEED_MAX,
    SeedInputs,
    derive_seed,
    drand_round_url,
    verify_seed,
)

# 公开文档 openroboto-cli/docs/SEED_GENERATION.md 里承诺给外部审计方的样例。
# 它和黄金向量一样是对外承诺，不能因为"看着像凑数"就删掉。
DOC_BLOCK_HASH = "0x" + "11" * 32
DOC_ROUND = 1
DOC_DRAND = "22" * 32
DOC_SEED = 3898936287


def test_documented_example() -> None:
    """公开文档里那个 assert，必须一直成立。"""
    assert derive_seed(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND) == DOC_SEED


def test_is_deterministic() -> None:
    """同输入同输出 —— 没有时间、没有随机、没有环境变量参与。"""
    first = derive_seed(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    second = derive_seed(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    assert first == second


def test_stays_in_uint32_range() -> None:
    """取摘要末 4 字节，结果必然落在 [0, 4294967295]。存它的列必须是 BIGINT。"""
    assert SEED_MAX == 4294967295
    for i in range(64):
        seed = derive_seed(f"0x{i:064x}", i, f"{i:064x}")
        assert 0 <= seed <= SEED_MAX


@pytest.mark.parametrize(
    ("block_hash", "round_num", "drand_random"),
    [
        ("0x" + "11" * 32, 2, "22" * 32),  # 只改 round
        ("0x" + "12" * 32, 1, "22" * 32),  # 只改 block_hash
        ("0x" + "11" * 32, 1, "23" * 32),  # 只改 drand randomness
    ],
)
def test_every_input_participates(
    block_hash: str, round_num: int, drand_random: str
) -> None:
    """三个入参都真的进了哈希 —— 任一个变了，seed 必须变。"""
    assert derive_seed(block_hash, round_num, drand_random) != DOC_SEED


def test_inputs_are_not_normalised() -> None:
    """入参按原样拼接：大小写、``0x`` 前缀、空白都是有意义的。

    生产库存的是带 ``0x`` 的小写 hash。有人"顺手"去前缀或转大写，
    算出来就是另一个 seed，历史全崩 —— 所以这条差异必须被钉死。
    """
    lower = derive_seed("0xabcd", 1, "ef01")
    assert derive_seed("0xABCD", 1, "ef01") != lower
    assert derive_seed("abcd", 1, "ef01") != lower
    assert derive_seed(" 0xabcd", 1, "ef01") != lower


def test_separator_is_part_of_the_message() -> None:
    """``:`` 是消息格式的一部分，不是可换的分隔符 —— 换了就是另一条公式。"""
    assert derive_seed("a:b", 1, "c") != derive_seed("a", 1, "b:c")


def test_no_input_validation() -> None:
    """空串 / 0 轮次不会抛异常，照样出一个 uint32。

    这是**当前行为**，不是设计上的好主意：它意味着上游漏传字段时不会炸，
    会安静地算出一个看着正常的 seed。校验属于调用方的职责（后端在 drand
    取不到时必须阻塞任务，而不是拿空串来派生）。改这里 = 破坏性变更。
    """
    assert derive_seed("", 0, "") == 4092634947


def test_verify_seed_accepts_and_rejects() -> None:
    """审计方向：对上返回 True，任何一个输入错配都必须返回 False。"""
    assert verify_seed(DOC_SEED, DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    assert not verify_seed(DOC_SEED + 1, DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    assert not verify_seed(DOC_SEED, DOC_BLOCK_HASH, DOC_ROUND + 1, DOC_DRAND)
    assert not verify_seed(DOC_SEED, "0x" + "99" * 32, DOC_ROUND, DOC_DRAND)
    assert not verify_seed(DOC_SEED, DOC_BLOCK_HASH, DOC_ROUND, "99" * 32)


def test_drand_round_url_for_a_recorded_round() -> None:
    """审计方拿这个 URL 去核对信标，所以路径形状本身是契约。"""
    assert drand_round_url(6347967) == (
        f"{DRAND_API}/{DRAND_CHAIN_HASH}/public/6347967"
    )


def test_drand_round_url_defaults_to_latest() -> None:
    """不给轮次就是最新一轮。"""
    assert drand_round_url() == f"{DRAND_API}/{DRAND_CHAIN_HASH}/public/latest"
    assert drand_round_url("latest") == drand_round_url()


@pytest.mark.parametrize("bad_round", [0, -1, "6347967", "", "LATEST", 1.0])
def test_drand_round_url_rejects_non_rounds(bad_round: object) -> None:
    """必须拒绝的负例：0 是"还没算出轮次"的哨兵，字符串轮次、浮点、负数都不是轮次。"""
    with pytest.raises(ValueError, match="positive integer"):
        drand_round_url(bad_round)  # type: ignore[arg-type]


def test_drand_chain_hash_is_quicknet() -> None:
    """换链哈希 = 换熵源 = 历史全部不可复现。钉死它。"""
    assert DRAND_CHAIN_HASH == (
        "8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce"
    )
    assert DRAND_API == "https://api.drand.sh"


def test_seed_inputs_derive_matches_function() -> None:
    """dataclass 只是把三个字段绑在一起，派生结果与直接调函数必须一致。"""
    inputs = SeedInputs(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    assert inputs.derive() == DOC_SEED
    assert inputs.verify(DOC_SEED)
    assert not inputs.verify(DOC_SEED + 1)


def test_seed_inputs_is_frozen() -> None:
    """seed 输入一旦记录就是历史，不允许就地改写。"""
    inputs = SeedInputs(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    with pytest.raises(dataclasses.FrozenInstanceError):
        inputs.block_hash = "0x00"  # type: ignore[misc]


def test_seed_inputs_compares_by_value() -> None:
    """同源判断靠值相等，不靠对象身份；能进 set / 做 dict key。"""
    a = SeedInputs(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    b = SeedInputs(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    c = SeedInputs(DOC_BLOCK_HASH, DOC_ROUND + 1, DOC_DRAND)
    assert a == b
    assert a != c
    assert len({a, b, c}) == 2
