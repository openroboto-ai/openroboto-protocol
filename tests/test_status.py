"""status.py 的契约测试。

每一条断言要么来自线上实测，要么来自生产代码里一条有出处的路径。
带 `线上` 字样的注释是 2026-08-17 对 https://api.openroboto.ai 的只读实测结果。
"""

from __future__ import annotations

import dataclasses

import pytest

from openroboto_protocol import status as S

# ── 词表本身 ──────────────────────────────────────────────────────────────


def test_all_statuses_is_the_transition_table_keys() -> None:
    """状态全集与转移表同源 —— 不可能出现"表里有、词表没有"。"""
    assert S.ALL_STATUSES == set(S.STATUS_TRANSITIONS)


def test_transition_targets_are_all_known_statuses() -> None:
    """转移表不能指向词表外的状态（写错一个字母就会在这里露出来）。"""
    for src, targets in S.STATUS_TRANSITIONS.items():
        assert targets <= S.ALL_STATUSES, f"{src} 指向了未知状态"


def test_production_observed_statuses_are_all_legal() -> None:
    """线上 `GET /api/v1/submissions/history?limit=500` 出现过的 eval_status
    共 4 种（evaluated 65 / superseded 32 / eval_failed 13 / rejected 7），
    `GET /api/v1/queue/status` 的 summary 另有 pending / evaluating。
    词表漏掉任何一个都会让线上真实数据变成"非法状态"。
    """
    observed = {
        "evaluated",
        "superseded",
        "eval_failed",
        "rejected",
        "pending",
        "evaluating",
    }
    assert observed <= S.ALL_STATUSES


def test_superseded_is_in_the_vocabulary() -> None:
    """老 `backend/protocol/status.py` 的 ALL_STATUSES 漏了它，线上却有 32 条。"""
    assert S.STATUS_SUPERSEDED in S.ALL_STATUSES
    assert S.is_terminal(S.STATUS_SUPERSEDED)


def test_transition_table_is_read_only() -> None:
    """共享契约不能被消费方就地改掉。"""
    with pytest.raises(TypeError):
        S.STATUS_TRANSITIONS["pending"] = frozenset()  # type: ignore[index]


# ── 终态 / 冻结态 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("st", ["evaluated", "eval_failed", "rejected", "superseded"])
def test_terminal_states_have_no_outgoing_edges(st: str) -> None:
    """spec 不变量 7：单向，不许回退。"""
    assert S.is_terminal(st)
    assert S.STATUS_TRANSITIONS[st] == frozenset()


@pytest.mark.parametrize(
    "st",
    [
        "received",
        "burn_checking",
        "burn_passed",
        "pending",
        "seed_failed",
        "evaluating",
    ],
)
def test_non_terminal_states(st: str) -> None:
    assert not S.is_terminal(st)


def test_seed_failed_is_retryable_not_terminal() -> None:
    """drand 拿不到时的 seed_failed 会被扫链下一轮重试回 pending。"""
    assert not S.is_terminal(S.STATUS_SEED_FAILED)
    assert S.can_transition(S.STATUS_SEED_FAILED, S.STATUS_PENDING)


def test_frozen_is_a_strict_subset_of_terminal() -> None:
    """冻结态（DB 拒绝任何写入）是终态的子集，不是同一件事。"""
    assert S.FROZEN_STATUSES < S.TERMINAL_STATUSES
    assert S.FROZEN_STATUSES == {"rejected", "superseded"}


# ── 状态机 ────────────────────────────────────────────────────────────────


def test_spec_invariant_7_happy_path() -> None:
    """pending → evaluating → evaluated / eval_failed / rejected。"""
    assert S.can_transition(S.STATUS_PENDING, S.STATUS_EVALUATING)
    for terminal in (S.STATUS_EVALUATED, S.STATUS_EVAL_FAILED, S.STATUS_REJECTED):
        assert S.can_transition(S.STATUS_EVALUATING, terminal)


def test_state_machine_never_goes_backwards() -> None:
    """回退一律非法 —— 这是 2026-08-14 事故 ⑤ 的核心。"""
    assert not S.can_transition(S.STATUS_EVALUATING, S.STATUS_PENDING)
    assert not S.can_transition(S.STATUS_EVALUATED, S.STATUS_EVALUATING)
    assert not S.can_transition(S.STATUS_PENDING, S.STATUS_RECEIVED)


def test_superseded_cannot_be_revived_by_a_late_score() -> None:
    """线上实证：uid 175 的 id=79 已被顶掉，worker 仍在跑，跑完出分想把它写成
    evaluated。复活会撞 idx_sub_hotkey_round_commit 唯一约束 → 出分接口 500，
    worker 几小时 GPU 白跑；就算不撞，被顶掉的版本也重新参与了排名。
    """
    for late in (S.STATUS_EVALUATED, S.STATUS_EVAL_FAILED, S.STATUS_EVALUATING):
        assert not S.can_transition(S.STATUS_SUPERSEDED, late)
        assert not S.can_transition(S.STATUS_REJECTED, late)


def test_pending_to_terminal_directly_is_legal() -> None:
    """历史真实路径：修复前 update_task_progress 只写 stage 不推状态，
    全库 0 条 evaluating，线上 65 条 evaluated 全是从 pending 直接落的。
    判它非法等于宣布线上历史非法。
    """
    assert S.can_transition(S.STATUS_PENDING, S.STATUS_EVALUATED)
    assert S.can_transition(S.STATUS_PENDING, S.STATUS_EVAL_FAILED)


def test_supersede_only_from_pending() -> None:
    """`supersede_pending` 的 WHERE 就一条：eval_status = 'pending'。"""
    assert S.can_transition(S.STATUS_PENDING, S.STATUS_SUPERSEDED)
    assert not S.can_transition(S.STATUS_EVALUATING, S.STATUS_SUPERSEDED)


def test_reject_is_reachable_from_every_non_terminal_state() -> None:
    """扫链侧任何一步都可能判拒（burn 不合格 / HF 结构 / 重复 / 轮次不匹配）。"""
    for src in S.ALL_STATUSES - S.TERMINAL_STATUSES:
        assert S.can_transition(src, S.STATUS_REJECTED), src


def test_idempotent_rewrite_is_allowed_except_when_frozen() -> None:
    """worker 出分 POST 超时会重投同一份分数，落库就是一次 evaluated → evaluated。
    但冻结态连同值重写都要挡住（生产 SQL 谓词就是这么写的）。
    """
    assert S.can_transition(S.STATUS_EVALUATED, S.STATUS_EVALUATED)
    assert S.can_transition(S.STATUS_PENDING, S.STATUS_PENDING)
    assert not S.can_transition(S.STATUS_SUPERSEDED, S.STATUS_SUPERSEDED)
    assert not S.can_transition(S.STATUS_REJECTED, S.STATUS_REJECTED)


def test_unknown_status_is_rejected_fail_closed() -> None:
    """未知词一律 False，不做"看起来像什么就当什么"的猜测。"""
    assert not S.can_transition("banana", S.STATUS_PENDING)
    assert not S.can_transition(S.STATUS_PENDING, "banana")
    assert not S.can_transition("", "")
    # 老词表里的写法也不是合法状态 —— 先过 normalize_status 再判。
    assert not S.can_transition("done", "failed")


def test_scan_phase_chain() -> None:
    assert S.can_transition(S.STATUS_RECEIVED, S.STATUS_BURN_CHECKING)
    assert S.can_transition(S.STATUS_BURN_CHECKING, S.STATUS_BURN_PASSED)
    assert S.can_transition(S.STATUS_BURN_PASSED, S.STATUS_PENDING)
    assert S.can_transition(S.STATUS_BURN_PASSED, S.STATUS_SEED_FAILED)
    # burn 校验没过就不该拿到种子
    assert not S.can_transition(S.STATUS_RECEIVED, S.STATUS_PENDING)


# ── 阶段词表 ──────────────────────────────────────────────────────────────


def test_wire_stage_vocabulary_matches_production() -> None:
    """线上 stage 出现过 running 61 / "" 47 / downloading 8 / prechecking 1。
    **从没出现过 evaluating** —— 对外规范词是 running，这是四方词表之争的裁决依据。
    """
    assert S.ALL_STAGES == {"downloading", "prechecking", "running"}
    assert "evaluating" not in S.ALL_STAGES


def test_worker_internal_word_maps_to_the_wire_word() -> None:
    """worker 内部叫 evaluating（run_eval.py 写进度文件用的就是它），
    对外必须是 running。这一行取代 `_PROGRESS_STAGE_MAP`。
    """
    assert S.normalize_stage("evaluating") == S.STAGE_RUNNING
    assert S.normalize_stage("running") == S.STAGE_RUNNING


def test_frontend_and_legacy_words_are_accepted() -> None:
    """按公开文档或前端类型写的调用方曾经因此收到 400（2026-08-14 实际发生）。"""
    assert S.normalize_stage("precheck") == S.STAGE_PRECHECKING
    assert S.normalize_stage("benchmark_running") == S.STAGE_RUNNING
    assert S.normalize_stage("benchmark_downloading") == S.STAGE_DOWNLOADING
    assert S.normalize_stage("benchmark_prechecking") == S.STAGE_PRECHECKING


def test_normalize_stage_strips_and_lowercases() -> None:
    """生产入口做的就是 `.strip().lower()`。"""
    assert S.normalize_stage("  RUNNING \n") == S.STAGE_RUNNING


def test_normalize_stage_rejects_unknown() -> None:
    """`scoring` 只存在于前端词表，没有任何后端路径产出它；空串是"没有阶段"。"""
    assert S.normalize_stage("scoring") is None
    assert S.normalize_stage("claimed") is None
    assert S.normalize_stage("") is None


def test_stage_records_are_frozen() -> None:
    """三个名字绑成一条记录，改不动 —— 错配在类型层就不可能发生。"""
    stage = S.STAGES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        stage.wire = "nope"  # type: ignore[misc]


def test_stage_stored_form_is_the_prefixed_one() -> None:
    """库里存 benchmark_ 前缀，出口必须翻译；不翻译前端渲染不出进度条。"""
    assert [s.stored for s in S.STAGES] == [
        "benchmark_downloading",
        "benchmark_prechecking",
        "benchmark_running",
    ]


def test_stage_order_is_the_worker_execution_order() -> None:
    assert [s.wire for s in S.STAGES] == ["downloading", "prechecking", "running"]


# ── 历史遗留状态词 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("legacy", "unified"),
    [
        ("enqueued", "pending"),
        ("waiting", "evaluating"),
        ("benchmark_downloading", "evaluating"),
        ("benchmark_prechecking", "evaluating"),
        ("benchmark_running", "evaluating"),
        ("benchmark_done", "evaluated"),
        ("benchmark_failed", "eval_failed"),
        ("done", "evaluated"),
        ("failed", "eval_failed"),
    ],
)
def test_legacy_status_aliases(legacy: str, unified: str) -> None:
    """生产库的遗留 `status` 列还在用这些词，与 eval_status 有 52 行不一致。"""
    assert S.normalize_status(legacy) == unified
    assert unified in S.ALL_STATUSES


def test_normalize_status_passes_unknown_through() -> None:
    """与老实现一致：不在表里的原样返回，合法性交给 ALL_STATUSES 判。"""
    assert S.normalize_status("evaluated") == "evaluated"
    assert S.normalize_status("banana") == "banana"


def test_legacy_alias_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        S.LEGACY_STATUS_ALIASES["done"] = "banana"  # type: ignore[index]
