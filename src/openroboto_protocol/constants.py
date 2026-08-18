"""协议常量 —— 每一个都写清单位和量纲。

写单位不是洁癖。`CHAMPION_MARGIN` 的旧注释写成 `min avg_score margin to dethrone
king (2%)`，值却是 `0.01`（avg_score 的绝对差值，不是百分比）。那个 "(2%)" 已经
误导过一次。排放权重更狠：全仓有两套数字（0.07/0.02/0.01 与 0.70/0.20/0.10），
没有任何一处说明它们是不同口径，看到哪份就以为是哪份。

**这个模块不负责**：运行时可调的后端参数。按 ADR 01，后端行为参数的唯一来源是
`backend.yaml`，`control.json` 只承载 payment / dataset / training / process。
这里放的是"两边必须有一致理解"的协议常量；`backend.yaml` 能覆盖的项会逐条注明。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ─────────────────────────────────────────────────────────────────────────────
# 排放权重
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EmissionWeights:
    """Top-K 排放权重。**两种口径绑在一起，只能有一个输入。**

    同一件事全仓有两套数字，且没有任何地方说明它们的区别：

    ===============================  ================================  ==========
    数字                             口径                              是否生效
    ===============================  ================================  ==========
    ``[0.07, 0.02, 0.01]``           占**全网总排放**的绝对份额        **生效**
    ``[0.70, 0.20, 0.10]``           Top-3 **之间**的相对分配          从未上线
    ===============================  ================================  ==========

    数学上一致：``0.70 × (1 − 0.90 burn) = 0.07``。所以两者不是矛盾，是同一件事的
    两种表达 —— 但混着用一定出事，因为 rank 1 拿 "0.7" 还是 "0.07" 差十倍。

    相对口径出现在 `owner/tools/round_controller.py:98` 生成的 control.json 里
    （键名 ``emission_weights``）。2026-08-17 实测 https://api.openroboto.ai/control.json
    **没有这个键** —— 它从未上线，链上排放一直走绝对口径。

    这里只存绝对份额，相对份额和 burn 份额都是**算出来的**，因此永远对得上。
    """

    #: rank 1..K 各自占全网总排放的份额。降序，和 < 1（差额被 burn 掉）。
    #: 量纲：无量纲比例，1.0 = 全网总排放。
    absolute: tuple[float, ...]

    @property
    def top_k(self) -> int:
        """进榜名额。榜单长度就是权重条数 —— 不允许两个数各写一处。"""
        return len(self.absolute)

    @property
    def relative(self) -> tuple[float, ...]:
        """Top-K **之间**的相对分配（和为 1.0）。control.json 那套口径。"""
        total = sum(self.absolute)
        return tuple(w / total for w in self.absolute)

    @property
    def burn_share(self) -> float:
        """没进榜的那部分 —— 全部烧掉。量纲同 `absolute`。

        ⚠️ 这是**协议口径的推论**（1 − Top-K 之和）。生产实际写链时用的是
        `backend.yaml` 的 `scanner.burn_ratio`（线上值 0.9）。两者当前一致；
        真要改哪个都必须同时改另一个，否则权重和不为 1。
        """
        return 1.0 - sum(self.absolute)


#: 生效口径的排放权重。**改这里就是改矿工到手的钱。**
TOP_K_EMISSION: Final[EmissionWeights] = EmissionWeights(
    absolute=(0.07, 0.02, 0.01),
)

#: 兼容旧名（`protocol/types.py:82` 一直叫这个）。元组而非列表：常量不该可变。
TOP_K_EMISSION_WEIGHTS: Final[tuple[float, ...]] = TOP_K_EMISSION.absolute

#: 进榜名额（King of the Hill 的榜单长度）。与权重条数同源。
TOP_K: Final[int] = TOP_K_EMISSION.top_k


# ─────────────────────────────────────────────────────────────────────────────
# 夺擂门槛
# ─────────────────────────────────────────────────────────────────────────────

#: 挑战者要夺擂，avg_score 必须 **严格大于** 擂主 + 这个值。
#:
#: 单位：avg_score 的**绝对差值**（avg_score 本身是 0..1 的 6 个 suite 均值），
#: 所以 0.01 = 0.01 分，**不是 1%，更不是旧注释写的 "(2%)"**。
#:
#: 比较符是严格大于，差值正好等于 margin 时挑战**失败**
#: （`backend/db/rankings.py:224` 的 `chall_avg > target_avg + margin`）。
#: 平局判失败是反抄袭的基石：复制擂主的权重只能打平，打平输在 margin 上。
#: 任何一处写成 `>=` 都等于单方面拆掉这道门槛。
#:
#: ⚠️ 运行时可被 `backend.yaml` 的 `ranking.champion_margin` 覆盖（线上值同为 0.01）。
#: 这里是协议默认值；矿工据此估算能不能夺擂，两边不一致就是矿工白烧 TAO。
CHAMPION_MARGIN: Final[float] = 0.01


# ─────────────────────────────────────────────────────────────────────────────
# LIBERO 评测环境
# ─────────────────────────────────────────────────────────────────────────────

#: 一次评测要跑的 LIBERO task suite，顺序即派发给 worker 的 `env_list` 顺序。
#: 2026-08-17 线上 117 条提交的 `env_list` 与 82 条出分记录的 `env_scores`
#: 都恰好是这 6 个，一个不多一个不少。
LIBERO_TASK_SUITES: Final[tuple[str, ...]] = (
    "libero_spatial",
    "libero_object",
    "libero_goal",
    "libero_10",
    "libero_object_swap",
    "libero_spatial_swap",
)

#: 出分时必须齐全的环境。缺一个就整条拒收（`MISSING_ENVS`），任务保持原状态好让
#: worker 重试 —— 3 个 suite 的均值和 6 个的均值不可比，混进同一张榜就是送分。
#: 与 `LIBERO_TASK_SUITES` 同源：不存在"允许跑 6 个但只要求 4 个"这种半截配置。
REQUIRED_ENVS: Final[frozenset[str]] = frozenset(LIBERO_TASK_SUITES)


# ─────────────────────────────────────────────────────────────────────────────
# drand 随机信标
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DrandBeacon:
    """drand 链的三个参数，绑成一条记录 —— 它们只能同时改。

    `genesis_time` 和 `period_seconds` 决定"某个时间点对应第几轮"，
    `chain_hash` 决定"去哪条链取这一轮"。串了任意一个，算出来的 round 指向另一条链
    的随机数，种子随之改变，而 **种子一变，历史评测就不可复现**（spec §5）。
    分开写三个模块常量，就给了串用的机会。
    """

    #: drand 链标识（也是 API 路径的一段）。
    chain_hash: str
    #: 该链第 1 轮的 Unix 时间戳（秒）。
    genesis_time: int
    #: 出块周期（秒）。
    period_seconds: int


#: 生产使用的 drand 链：League of Entropy 默认链（beaconID `default`，
#: schemeID `pedersen-bls-chained`）。
#:
#: 2026-08-17 对 `https://api.drand.sh/<chain_hash>/info` 实测核对：
#: period=30、genesis_time=1595431050、hash 一致。
#:
#: 轮次换算公式（`max(1, (ts - genesis) // period + 1)`）和取随机数的网络请求都
#: **不在这个模块里** —— 前者属于 `seed.py`，后者是 I/O，属于后端。
#:
#: ⚠️ `seed.py` 目前另有一份同值的 `DRAND_CHAIN_HASH`（拼 URL 用）。同一个常量两份
#: 副本正是这个包要消灭的东西；在收敛成一处之前，`tests/test_constants.py` 里有一条
#: 断言盯着两者相等，漂了立刻变红。
DRAND_DEFAULT_CHAIN: Final[DrandBeacon] = DrandBeacon(
    chain_hash="8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce",
    genesis_time=1595431050,
    period_seconds=30,
)
