"""评测种子派生 —— 子网最红的一条线。

**承诺什么**：给定三个公开输入（commitment 所在区块的 hash、子网 round、drand
quicknet 信标的 randomness），任何人都能算出与后端完全相同的 uint32 seed。
公式是公开的，且公开也不泄密：矿工提交 commitment 的那一刻，容纳它的区块 hash 尚未
确定，之后那一轮的 drand randomness 也还不存在 —— 谁都无法提前算出自己的 seed。

**不负责什么**：
- 不取 drand（``fetch_drand`` 在后端，它要发网络请求）。
- 不从区块时间戳推算该用哪一轮 drand（同样留在后端的 I/O 那半）。
- 不做 base seed → 每个 LIBERO task seed 的二次派生（那在公开评测工具链里）。
- drand 不可用时怎么办不归这里管：后端必须**阻塞并重试**，绝不允许退化成
  「只用 block_hash」—— 那会改掉已公布的公式，并且把两路独立熵砍成一路。

**谁消费它**：后端在评测前派生 seed；矿工 / 审计方拿链上与信标上的公开值复算，
用来验证后端没有针对某个人挑 seed。

**最小验证方式**：``uv run pytest tests/test_golden_vectors.py`` ——
里面是链上真实发生过的输入输出对。

行为来源：``prototype/backend/seed.py`` 与公开仓 ``openroboto-cli/protocol/seed.py``
（两份逐字一致）。搬进这个包时**一个字节都没改**，历史评测必须继续可复现。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# drand quicknet 链的公开标识。换链 = 换熵源 = 历史全部不可复现，属 major 变更。
DRAND_CHAIN_HASH = "8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce"

# drand 公开信标的 HTTP 端点。放在这里只是为了让审计方拼得出可校验的 URL，
# 这个包本身不会去访问它（零 I/O 是这个包存在的前提）。
DRAND_API = "https://api.drand.sh"

# seed 的值域上界。它是契约的一部分，不是实现细节：seed 取 SHA256 摘要的末 4 字节，
# 所以最大 4294967295 —— 超出 PostgreSQL INTEGER 的范围。存 seed 的列必须是 BIGINT，
# 生产上为此做过一次在线迁移
# （prototype/backend/database.py:438 _migrate_seed_to_bigint）。
SEED_MAX = 0xFFFFFFFF


def derive_seed(block_hash: str, round_num: int, drand_random: str) -> int:
    """把三个公开输入派生成本轮评测的 uint32 seed。

    公式（已对外公布，改一个字符就是改历史）::

        message = UTF8("{block_hash}:{round_num}:{drand_random}")
        seed    = big_endian_uint32(SHA256(message)[-4:])

    三个入参按**原样**参与拼接，不做任何规范化 —— 不去 ``0x`` 前缀、不改大小写、
    不 strip 空白。生产库里存的 block_hash 就是带 ``0x`` 的小写十六进制，
    drand randomness 是不带前缀的小写十六进制；换一种写法就是另一个 seed。
    """
    # 下面三行与 prototype/backend/seed.py:26 及公开仓
    # openroboto-cli/protocol/seed.py:18 逐字一致。
    # `.encode()` 与 `.encode("utf-8")` 字节等价，UP012 想让我们删掉参数 ——
    # 但保持逐字相同，才能一眼看出这次抽包"只搬家、没改东西"。所以按下不表。
    seed_input = f"{block_hash}:{round_num}:{drand_random}".encode("utf-8")  # noqa: UP012
    digest = hashlib.sha256(seed_input).digest()
    return int.from_bytes(digest[-4:], byteorder="big")


def verify_seed(
    expected_seed: int,
    block_hash: str,
    round_num: int,
    drand_random: str,
) -> bool:
    """审计方向：已公布的 seed 是否确实由这三个公开输入算出。

    返回 False 只说明这条记录的 seed 与其**存储的输入**对不上，不区分是后端算错、
    输入被事后覆写、还是历史遗留数据 —— 判因交给调用方。
    """
    return expected_seed == derive_seed(block_hash, round_num, drand_random)


def drand_round_url(drand_round: int | str = "latest") -> str:
    """拼出某一轮 drand randomness 的公开查询 URL（只拼字符串，不发请求）。

    ``"latest"`` 表示最新一轮。轮次必须是正整数：drand 的轮次从 1 开始，
    0 在后端代表「还没算出该用哪一轮」这个哨兵值，不是一个可查的轮次。
    """
    if drand_round != "latest" and (
        not isinstance(drand_round, int) or drand_round <= 0
    ):
        raise ValueError("drand_round must be a positive integer or 'latest'")
    return f"{DRAND_API}/{DRAND_CHAIN_HASH}/public/{drand_round}"


@dataclass(frozen=True, slots=True)
class SeedInputs:
    """一次评测 seed 的三个输入，绑成一个不可变值。

    绑在一起是因为它们**必须同源**：三个字段得来自同一条 submission。
    拿 A 的 block_hash 配 B 的 drand_random 会安静地算出一个长得很正常的 uint32，
    没有任何东西会报错 —— 这种错配只能靠类型层堵死，所以有了这个 dataclass。

    ``frozen`` 不是洁癖：seed 输入一旦记录就属于历史，改它等于改谁拿到了钱。
    """

    #: commitment 所在区块的 hash，生产格式是带 ``0x`` 前缀的小写十六进制
    block_hash: str
    #: 子网轮次（不是 drand 轮次，两者不要混）
    round_num: int
    #: drand quicknet 该轮的 randomness，不带 ``0x`` 前缀的小写十六进制
    drand_random: str

    def derive(self) -> int:
        """派生这组输入对应的 uint32 seed。"""
        return derive_seed(self.block_hash, self.round_num, self.drand_random)

    def verify(self, expected_seed: int) -> bool:
        """核对一个已公布的 seed 是否出自这组输入。"""
        return self.derive() == expected_seed
