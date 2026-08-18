"""constants.py 的契约测试。

数值全部是钱路径上的。改红任何一条之前先问：链上已经按旧值发生过什么？
"""

from __future__ import annotations

import dataclasses
import itertools
import math

import pytest

from openroboto_protocol import constants as C

# ── 排放权重 ──────────────────────────────────────────────────────────────


def test_absolute_weights_are_the_effective_ones() -> None:
    """生效口径：占全网总排放的绝对份额（`protocol/types.py:82` 一直是这个值）。"""
    assert C.TOP_K_EMISSION_WEIGHTS == (0.07, 0.02, 0.01)
    assert C.TOP_K == 3
    assert C.TOP_K == len(C.TOP_K_EMISSION_WEIGHTS)


def test_relative_weights_match_the_control_json_dialect() -> None:
    """control.json 那套 [0.70, 0.20, 0.10] 是同一件事的另一种口径，不是另一套规则。

    这条断言就是"数学上一致"这句话的可执行形式：
    0.70 × (1 − 0.90 burn) = 0.07。两套数字从此不可能各自漂移。
    """
    assert all(
        math.isclose(got, want)
        for got, want in zip(C.TOP_K_EMISSION.relative, (0.70, 0.20, 0.10), strict=True)
    )
    assert math.isclose(sum(C.TOP_K_EMISSION.relative), 1.0)


def test_burn_share_is_the_remainder() -> None:
    """没进榜的 90% 全烧掉。与 backend.yaml 的 scanner.burn_ratio=0.9 一致。"""
    assert math.isclose(C.TOP_K_EMISSION.burn_share, 0.90)
    total = sum(C.TOP_K_EMISSION_WEIGHTS) + C.TOP_K_EMISSION.burn_share
    assert math.isclose(total, 1.0)


def test_weights_are_strictly_descending() -> None:
    """权重降序就是排名序：rank 1 拿 TOP_K_EMISSION_WEIGHTS[0]。
    排名引擎反过来靠"权重降序 = 排名顺序"还原榜单，打平或乱序会让还原出错。
    """
    w = C.TOP_K_EMISSION_WEIGHTS
    assert all(a > b for a, b in itertools.pairwise(w))
    assert all(0.0 < x < 1.0 for x in w)


def test_emission_weights_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        C.TOP_K_EMISSION.absolute = (0.7, 0.2, 0.1)  # type: ignore[misc]


# ── 夺擂门槛 ──────────────────────────────────────────────────────────────


def test_champion_margin_is_an_absolute_delta() -> None:
    """0.01 是 avg_score 的绝对差值，不是百分比。旧注释写 "(2%)" 已误导过一次。"""
    assert C.CHAMPION_MARGIN == 0.01


def test_a_tie_loses_the_challenge() -> None:
    """`chall_avg > target_avg + margin` —— 严格大于。

    平局判失败是反抄袭的基石：复制擂主的权重只能打平，打平输在 margin 上。
    任何一处写成 `>=` 都等于单方面拆掉这道门槛。
    """
    king = 0.80
    assert not king + C.CHAMPION_MARGIN > king + C.CHAMPION_MARGIN  # 差值正好等于门槛
    assert 0.8101 > king + C.CHAMPION_MARGIN  # 超出门槛才算赢
    assert not 0.8099 > king + C.CHAMPION_MARGIN


# ── LIBERO 环境 ───────────────────────────────────────────────────────────


def test_required_envs_are_the_six_production_suites() -> None:
    """线上 117 条提交的 env_list 与 82 条出分记录的 env_scores 都恰好是这 6 个。"""
    assert C.REQUIRED_ENVS == {
        "libero_spatial",
        "libero_object",
        "libero_goal",
        "libero_10",
        "libero_object_swap",
        "libero_spatial_swap",
    }
    assert len(C.LIBERO_TASK_SUITES) == 6


def test_required_envs_and_task_suites_are_same_source() -> None:
    """不存在"允许跑 6 个但只要求 4 个"这种半截配置 —— 4 个的均值和 6 个的不可比。"""
    assert C.REQUIRED_ENVS == frozenset(C.LIBERO_TASK_SUITES)
    assert len(C.REQUIRED_ENVS) == len(C.LIBERO_TASK_SUITES)  # 无重复


def test_task_suite_order_matches_the_dispatched_env_list() -> None:
    """派发给 worker 的 env_list 顺序，线上原样如此。"""
    assert C.LIBERO_TASK_SUITES[0] == "libero_spatial"
    assert C.LIBERO_TASK_SUITES[-1] == "libero_spatial_swap"


# ── drand ─────────────────────────────────────────────────────────────────


def test_drand_chain_parameters() -> None:
    """2026-08-17 对 https://api.drand.sh/<chain_hash>/info 实测核对过。
    改任何一个都会让种子指向另一条链的随机数 —— 历史评测随即不可复现（spec §5）。
    """
    chain = C.DRAND_DEFAULT_CHAIN
    assert chain.chain_hash == (
        "8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce"
    )
    assert chain.genesis_time == 1595431050
    assert chain.period_seconds == 30
    assert len(chain.chain_hash) == 64


def test_chain_hash_has_no_second_copy_that_drifted() -> None:
    """`seed.py` 目前另有一份同值的 `DRAND_CHAIN_HASH`（它拼 URL 要用）。

    同一个常量两份手工副本，正是这个包存在要消灭的东西 —— `protocol/types.py` 当年
    就是这么漂了 105 行的。在收敛成一处之前，这条断言负责让漂移当场变红。
    """
    from openroboto_protocol import seed

    assert seed.DRAND_CHAIN_HASH == C.DRAND_DEFAULT_CHAIN.chain_hash


def test_drand_beacon_is_frozen() -> None:
    """三个参数只能同时改，改不动比改错好。"""
    with pytest.raises(dataclasses.FrozenInstanceError):
        C.DRAND_DEFAULT_CHAIN.period_seconds = 3  # type: ignore[misc]
