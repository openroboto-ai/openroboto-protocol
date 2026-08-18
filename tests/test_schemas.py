"""`schemas.py` 的契约测试。

**这个文件的定位和普通单测不同**：它锁的不是「函数算得对不对」，而是
「响应长什么样」。字段少一个、名字改一个、枚举多一个词，都要在这里红 ——
因为真正的消费方（前端、GPU worker、矿工 CLI、外部验证者）在别人的机器上，
改坏了不会报错，只会静默拿到 `undefined`。

三类断言，对应任务书要求的三件事：

1. **字段集不多不少** —— 每个响应模型的键集合逐字写死在这里（`_RESPONSE_KEYS`）。
   用序列化 schema 而不是 `model_fields`，这样 `computed_field`
   （`ProgressAccepted.status`）也算在内 —— 消费方看到的是序列化后的 JSON。
2. **枚举值必须来自 `status.py` 的词表** —— 阶段词与 `ALL_STAGES` 相等；
   榜位词 / 轮次词与生命周期词零交集（「一个响应一套词」）。
3. **咬过人的行为** —— 每条测试的 docstring 里写清它守的是哪一次事故。
"""

from __future__ import annotations

import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from openroboto_protocol import schemas as s
from openroboto_protocol.constants import REQUIRED_ENVS
from openroboto_protocol.status import ALL_STAGES, ALL_STATUSES, STATUS_PENDING

# ─────────────────────────────────────────────────────────────────────────────
# 1. 字段集不多不少
# ─────────────────────────────────────────────────────────────────────────────

#: 每个模型序列化后的键集合。**逐字写死**，改这里等于改对外契约。
#:
#: 来源：6 份契约卡的「响应字段」行，每一行都标着线上实测或生产代码出处。
#: 加字段是 minor（消费方缺键有默认值）、删字段和改名是 major —— 两种都必须
#: 先改这张表，改不动就说明这个改动本来就不该做。
_RESPONSE_KEYS: dict[type[BaseModel], set[str]] = {
    # —— 响应信封（ADR 02）——
    s.Envelope: {"data", "meta"},
    s.ListEnvelope: {"data", "meta"},
    s.ErrorEnvelope: {"error", "meta"},
    s.ErrorBody: {"code", "message", "retryable"},
    s.ValidationErrorEnvelope: {"error", "meta"},
    s.ValidationErrorBody: {"code", "message", "retryable", "fields"},
    s.Meta: {"request_id", "generated_at"},
    s.ListMeta: {"request_id", "generated_at", "page"},
    s.PageMeta: {"total", "limit", "offset", "has_more"},
    # —— 共用片段 ——
    s.MinerRef: {"hotkey", "display_name"},
    s.ModelRef: {"name", "hf_repo", "revision"},
    s.ScoreStat: {"mean", "std", "trials"},
    s.Reason: {"code", "message", "retryable", "source"},
    # —— worker 契约组 ——
    s.QueueTask: {
        "task_id",
        "miner_uid",
        "miner_hotkey",
        "hf_repo_id",
        "hf_commit",
        "round_num",
        "seed",
        "block_hash",
        "drand_random",
        "drand_round",
        "eval_status",
        "created_at",
        "submitted_at",
        "task_version",
        "env_list",
    },
    s.QueueResponse: {"queue_size", "tasks"},
    s.EnvScore: {
        "env_name",
        "score",
        "samples",
        "duration_sec",
        "error",
        "base_suite",
        "perturbation",
    },
    s.ScoreSubmission: {
        "success",
        "total_score",
        "env_scores",
        "error",
        "duration_sec",
        "miner_hotkey",
        "hf_repo_id",
        "hf_commit",
        "round_num",
        "benchmark",
        "init_seed",
        "expected_trials_per_task",
        "per_task_scores",
    },
    s.ScoreAccepted: {"task_id", "ok", "ignored", "message"},
    s.ProgressUpdate: {"task_id", "stage", "detail", "worker_id"},
    # `status` 是 computed_field —— 旧调用方读它，前端读 `stage`，两个都要给。
    s.ProgressAccepted: {"task_id", "stage", "success", "status"},
    s.SubmissionRecord: {
        "task_id",
        "status",
        "hotkey",
        "miner_hotkey",
        "hf_repo_id",
        "hf_commit",
        "round_num",
        "result",
        "reason",
    },
    # —— benchmark meta ——
    s.BenchmarkSpec: {
        "suite",
        "tasks_per_round",
        "trials_per_task",
        "sim_engine",
        "timestep_ms",
        "control",
        "observations",
    },
    s.BenchmarkMeta: {"name", "version", "phase", "updated_at", "maintainer", "spec"},
    # —— queue / submissions / scan-rejections ——
    s.QueueSummary: {
        "pending",
        "evaluating",
        "evaluated",
        "eval_failed",
        "rejected",
        "superseded",
        "unknown",
        "total",
    },
    s.QueueStatusTask: {
        "task_id",
        "hotkey",
        "uid",
        "eval_status",
        "burn_status",
        "commit_block",
        "burn_block",
        "hf_repo_id",
        "hf_commit",
        "submitted_at",
        "round_num",
        "reason",
        "stage",
        "detail",
        "queue_position",
        "evaltime",
    },
    s.QueueStatusResponse: {"summary", "tasks"},
    s.SubmissionHistoryItem: {
        "id",
        "task_id",
        "uid",
        "hotkey",
        "round_num",
        "hf_repo_id",
        "hf_commit",
        "commit_block",
        "commit_block_timestamp",
        "burn_tx_hash",
        "burn_block",
        "burn_status",
        "block_hash",
        "eval_status",
        "env_list",
        "burn_amount_tao",
        "result",
        "detail",
        "reject_reason",
        "seed",
        "drand_random",
        "drand_round",
        "model_hash",
        "avg_score",
        "stage",
        "submitted_at",
        "created_at",
        "updated_at",
        "reason",
    },
    s.SubmissionHistoryResponse: {
        "submissions",
        "total",
        "limit",
        "offset",
        "success",
    },
    s.PerTaskScore: {"task_id", "success_rate", "trials"},
    s.EvalEnvironment: {"env_hash", "sim", "eval_commit", "seed"},
    s.SubmissionArtifacts: {"score_json_url", "logs_url"},
    s.SubmissionDetail: {
        "submission_id",
        "round_id",
        "miner",
        "model",
        "eval_status",
        "scored_at",
        "submitted_at",
        "score",
        "per_task",
        "environment",
        "artifacts",
        "reason",
    },
    s.ScanRejection: {
        "uid",
        "hotkey",
        "round_num",
        "hf_commit",
        "hf_repo_id",
        "commit_block",
        "burn_tx_hash",
        "burn_block",
        "commit_block_timestamp",
        "task_id",
        "reject_reason",
        "created_at",
        "reason",
    },
    s.ScanRejectionsResponse: {"rejections", "total", "limit", "offset", "success"},
    # —— 榜单与轮次 ——
    s.TasksPassed: {"passed", "total"},
    s.LeaderboardAudit: {"score_json_url", "logs_url", "env_hash"},
    s.LeaderboardRow: {
        "rank",
        "round_num",
        "submission_id",
        "miner_uid",
        "miner",
        "model",
        "score",
        "delta_vs_base",
        "tasks_passed",
        "status",
        "audit",
        "submitted_at",
        "scored_at",
    },
    s.Baseline: {"model_name", "hf_repo", "score", "revision"},
    s.LeaderboardResponse: {"round_id", "generated_at", "baseline", "total", "rows"},
    s.Champion: {
        "miner_hotkey",
        "miner_name",
        "model_name",
        "score",
        "delta_vs_prev_champion",
        "settled_at",
        "held",
    },
    s.RoundSummaryEntry: {"id", "label", "status", "champion"},
    s.RoundDetail: {
        "id",
        "label",
        "status",
        "network",
        "base_model",
        "submission_count",
        "started_at",
        "ends_at",
        "champion",
    },
    s.CurrentRoundResponse: {"round"},
    s.RoundsSummary: {"rounds_settled", "cumulative_improvement"},
    s.RoundHistoryResponse: {"summary", "rounds", "total"},
    # —— 运维探针 ——
    s.LivenessResponse: {"round", "netuid", "status"},
    s.ReadinessCheck: {"ok", "detail"},
    s.ReadinessResponse: {"ready", "database", "migration", "alembic_version"},
}


def _serialized_keys(model: type[BaseModel]) -> set[str]:
    """模型序列化后的 JSON 键集合（含 `computed_field`）。"""
    schema = model.model_json_schema(mode="serialization")
    return set(schema["properties"])


@pytest.mark.parametrize(
    ("model", "expected"),
    list(_RESPONSE_KEYS.items()),
    ids=lambda v: getattr(v, "__name__", ""),
)
def test_field_set_is_exact(model: type[BaseModel], expected: set[str]) -> None:
    """字段集**不多不少**。

    少一个 = 消费方拿 `undefined`（前端 `Tolerate rebuilt-backend field renames`
    那次提交就是在替我们擦屁股）；多一个 = 悄悄扩大了对外承诺，而这个包的版本号
    就是契约版本，加字段必须是一次显式的 minor bump。
    """
    assert _serialized_keys(model) == expected


def test_every_exported_model_is_pinned() -> None:
    """新加的模型必须同时加进 `_RESPONSE_KEYS`。

    没有这条，「加一个模型但忘了钉字段集」会完全静默 —— 而那正是这个文件要防的事。
    """
    exported = {
        obj
        for name in s.__all__
        if isinstance(obj := getattr(s, name), type)
        and issubclass(obj, BaseModel)
        and obj is not s.Contract
    }
    assert exported == set(_RESPONSE_KEYS)


def test_history_item_never_exposes_legacy_status_column() -> None:
    """history 行**永不返回 `status`**（迁移前的遗留列）。

    前端 `normalizeHistoryStatus()` 是 `submission.status || submission.eval_status`，
    **先读 `status`**，而生产两列有 52 行不一致 —— 95 条里 33 条状态显示错误就是
    这么来的。旧实现靠出口 `row.pop("status", None)` 才躲过一劫；这里靠模型里根本
    没有这个字段。
    """
    assert "status" not in _serialized_keys(s.SubmissionHistoryItem)
    assert "eval_status" in _serialized_keys(s.SubmissionHistoryItem)


def test_history_item_hides_internal_columns() -> None:
    """4 个内部字段一个都不许出现。

    `repo_hash` 是**抄袭判定的模型指纹**，公开它等于把判定依据交出去；
    `legacy_task_id` / `hotkey_tag` / `worker_id` 是运维内部字段。
    线上实测这 33 个键**全量公开**，这条是收窄后的回归护栏。
    """
    leaked = {"legacy_task_id", "repo_hash", "hotkey_tag", "worker_id", "eval_detail"}
    assert leaked & _serialized_keys(s.SubmissionHistoryItem) == set()


def test_scan_rejection_has_no_surrogate_id() -> None:
    """线上不返回 `id`（新骨架多加了一个），这里也不加。"""
    assert "id" not in _serialized_keys(s.ScanRejection)


# ─────────────────────────────────────────────────────────────────────────────
# 2. 枚举值必须来自 status.py 的词表
# ─────────────────────────────────────────────────────────────────────────────


def test_stage_vocabulary_is_status_py() -> None:
    """阶段词表**只有一份**：`EvalStage` 必须逐字等于 `status.ALL_STAGES`。

    ZCY-158 的形状就是同一件事在四个仓库里各有一套写法。这条一红，说明这个模块
    又长出了第五套。
    """
    assert frozenset(get_args(s.EvalStage)) == ALL_STAGES


def test_display_vocabularies_never_overlap_lifecycle_words() -> None:
    """榜位词 / 轮次词与生命周期词**零交集** —— 「一个响应一套词」。

    生产 `/api/v1/leaderboard` 在同一个 `status` 字段里同时输出 `champion`（榜位词）
    和 `scored`（生命周期词），而且因为一句没有 `ORDER BY` 的 `LIMIT 1`，
    同一份数据两次请求会给出不同的词。
    """
    assert s.LEADERBOARD_STATUSES & ALL_STATUSES == frozenset()
    assert s.ROUND_STATUSES & ALL_STATUSES == frozenset()
    assert s.LEADERBOARD_STATUSES & s.ROUND_STATUSES == frozenset()


def test_leaderboard_status_rejects_lifecycle_words() -> None:
    """把生命周期词塞进榜单 `status` 必须失败，不能只是「约定不这么干」。"""
    with pytest.raises(ValidationError):
        _leaderboard_row(status="evaluating")
    with pytest.raises(ValidationError):
        # 新骨架 mock 的现值。前端联合类型里没有，CSS 也没有，
        # 而且 `eliminated` 语义上不成立 —— 挑战失败的矿工根本不在 rows 里。
        _leaderboard_row(status="eliminated")


def test_round_status_rejects_undecided_scoring_word() -> None:
    """`scoring` 第三态没有站得住的定义（spec 04 §9 Q2），未裁决前不进词表。"""
    with pytest.raises(ValidationError):
        s.RoundSummaryEntry(id=1, label="Round 01", status="scoring")  # type: ignore[arg-type]


def test_queue_summary_buckets_are_real_status_words() -> None:
    """summary 的每个桶都必须是一个真实存在的状态词，`unknown` / `total` 除外。

    反过来也查一遍：`ALL_STATUSES` 里没有桶的那几个词（`received` /
    `burn_checking` / `burn_passed` / `seed_failed`）会落进 `unknown` ——
    **落进去要告警，不许静默丢**，那是 ZCY-130 少算 45 条的教训。
    """
    assert set(s.QUEUE_SUMMARY_BUCKETS) <= ALL_STATUSES
    bucket_fields = set(s.QueueSummary.model_fields) - {"unknown", "total"}
    assert bucket_fields == set(s.QUEUE_SUMMARY_BUCKETS)
    assert ALL_STATUSES - set(s.QUEUE_SUMMARY_BUCKETS) != set()


def test_status_valued_fields_stay_open_typed() -> None:
    """生命周期状态字段注解成 `str`，**不是 `Literal`**。

    这是权衡过的：库里冒出一个词表外的状态词时，正确反应是那一行降级 + 告警，
    而不是整个端点 500 —— 5xx 对 worker 是「重复写库」的按钮（spec 07 §0.3）。
    """
    for model, name in s.STATUS_VALUED_FIELDS:
        assert name in model.model_fields, f"{model.__name__}.{name} 没了"
        assert model.model_fields[name].annotation is str


def test_reason_code_vocabulary_is_closed() -> None:
    """`reason.code` 是受控词表，词表外的码在模型层就进不来。

    ZCY-162：`superseded` 的拒绝原因**无处可读**，前端只能硬编码
    `unavailable`。`SUPERSEDED` 必须在词表里，这是那句硬编码的解药。
    """
    assert "SUPERSEDED" in s.REASON_CODES
    assert "INFRA_ERROR" in s.REASON_CODES
    with pytest.raises(ValidationError):
        s.Reason(code="NOPE", message="x", retryable=False, source="scan")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        s.Reason(code="SUPERSEDED", message="x", retryable=False, source="nope")  # type: ignore[arg-type]


def test_infra_failure_is_retryable_not_a_business_rejection() -> None:
    """基建故障不是业务拒绝。

    `burn_error: failed_to_create_subtensor` 这类必须 `retryable=True`，
    否则矿工烧掉的 TAO 被一次链 RPC 抖动白扔。模型只保证这个字段存在且是 bool，
    取值由扫链侧决定 —— 这条测的是「这个开关确实在契约里」。
    """
    reason = s.Reason(
        code="INFRA_ERROR",
        message="failed to create subtensor",
        retryable=True,
        source="scan",
    )
    assert reason.retryable is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. 咬过人的行为
# ─────────────────────────────────────────────────────────────────────────────


def _env_score(name: str, score: float = 0.5, samples: int = 100) -> dict[str, Any]:
    return {"env_name": name, "score": score, "samples": samples}


def _full_env_scores() -> list[dict[str, Any]]:
    return [_env_score(name) for name in sorted(REQUIRED_ENVS)]


def _leaderboard_row(**overrides: Any) -> s.LeaderboardRow:
    payload: dict[str, Any] = {
        "rank": 1,
        "round_num": 1,
        "submission_id": "task_abc_r1_v1",
        "miner_uid": 218,
        "miner": {"hotkey": "5FQxZBhriyAv6K", "display_name": "5FQxZBhriyAv"},
        "model": {"name": "pi0.5", "hf_repo": "x/y", "revision": "abc"},
        "score": {"mean": 0.53, "std": None, "trials": 6},
        "delta_vs_base": 0.0271,
        "tasks_passed": {"passed": 6, "total": 6},
        "status": "champion",
        "audit": {"score_json_url": "/api/v1/submissions/task_abc_r1_v1/score.json"},
    }
    payload.update(overrides)
    return s.LeaderboardRow.model_validate(payload)


# --- 空 env_list（事故 ⑦：worker 空转） ---


def test_empty_env_list_is_unrepresentable() -> None:
    """派发任务的 `env_list` **不可能**是空数组。

    2026-08-14 uid 221/231 的提交进了队列、worker 一直空转，卡的就是这里：
    库里存的是字符串 `'[]'`，非空字符串是 truthy，`or` 兜底和 `.get(k, default)`
    都兜不住。必须先 `json.loads` 再判空、空则回填 6 个 suite ——
    模型这一层是最后一道，让「空」在类型上就不合法。
    """
    with pytest.raises(ValidationError):
        _queue_task(env_list=[])


def test_env_list_must_be_a_list_not_a_json_string() -> None:
    """`env_list` 是数组，**不是 JSON 字符串**。

    直接迭代字符串会拆成单字符，曾把任务错报成 "invalid env names"。
    worker 侧 `parse_env_list` 的兜底是事故遗留，不是设计 —— 后端一律给数组。
    """
    with pytest.raises(ValidationError):
        _queue_task(env_list='["libero_spatial"]')


def _queue_task(**overrides: Any) -> s.QueueTask:
    payload: dict[str, Any] = {
        "task_id": "task_abc_r1_v1",
        "miner_uid": 218,
        "miner_hotkey": "5FQxZ",
        "hf_repo_id": "x/y",
        "hf_commit": "a" * 40,
        "round_num": 1,
        "seed": 42,
        "block_hash": "0x" + "b" * 64,
        "drand_random": "c" * 64,
        "drand_round": 1234,
        "eval_status": STATUS_PENDING,
        "env_list": sorted(REQUIRED_ENVS),
    }
    payload.update(overrides)
    return s.QueueTask.model_validate(payload)


# --- 出分入口的四道检查（事故：samples=-5 + 1 个 suite 直接夺擂） ---


def test_bool_cannot_masquerade_as_a_score() -> None:
    """`{"score": true}` 必须被拒 —— **两道锁都要在**。

    Python 里 `isinstance(True, int)` 是 True，所以「score 是数字」这条检查
    如果写成 `isinstance(x, (int, float))` 就漏得掉。
    而 pydantic 在 lax 模式下会先把 `true` 转成 `1.0`（实测），
    所以模型上必须是 `StrictFloat`、且原始 JSON 还要过一遍 `check_env_scores`。
    """
    with pytest.raises(ValidationError):
        s.EnvScore.model_validate({"env_name": "e", "score": True, "samples": 1})
    with pytest.raises(ValidationError):
        s.EnvScore.model_validate({"env_name": "e", "score": 0.5, "samples": True})
    with pytest.raises(s.ContractError) as exc:
        s.check_env_scores([{"env_name": "e", "score": True, "samples": 1}])
    assert exc.value.code == s.CODE_INVALID_SCORE


def test_strict_score_still_accepts_json_integers() -> None:
    """`StrictFloat` 不能误伤 worker 真会发的整数。

    实测 pydantic strict 模式对 int→float 是放行的；worker 的
    `total_score: 0` / `score: 1` 必须照收，否则出分路径直接断。
    """
    assert s.EnvScore.model_validate(
        {"env_name": "e", "score": 1, "samples": 100}
    ).score == pytest.approx(1.0)


def test_check_env_scores_rejects_nan_out_of_range_and_bad_samples() -> None:
    """NaN / 越界 / 负 samples 全部带稳定 code `INVALID_SCORE`。

    NaN 必须**单独拦**：它和任何数比较都是 False，`0 <= x <= 1` 漏得掉。
    历史代价是 `{"score": 99.0, "samples": -5}` + 只交 1 个 suite → 200 →
    直接夺擂拿 7% 权重。
    """
    # `check_env_scores` 收的是**原始 JSON**，所以合法输入本来就包含
    # 「根本不是 list」这种形状 —— 类型标 object 是故意的，不是偷懒。
    bad_payloads: list[object] = [
        [{"env_name": "e", "score": math.nan, "samples": 1}],
        [{"env_name": "e", "score": 99.0, "samples": 1}],
        [{"env_name": "e", "score": -0.1, "samples": 1}],
        [{"env_name": "e", "score": 0.5, "samples": -5}],
        [{"env_name": "e", "score": 0.5, "samples": 1.5}],
        [{"env_name": "e", "score": "0.5", "samples": 1}],
        [{"env_name": "e", "samples": 1}],
        ["not-an-object"],
        [],
        "not-a-list",
    ]
    for payload in bad_payloads:
        with pytest.raises(s.ContractError) as exc:
            s.check_env_scores(payload)
        assert exc.value.code == s.CODE_INVALID_SCORE, payload


def test_check_env_scores_accepts_zero_success_rate() -> None:
    """成功率为零是**有效分数**，不是失败。`success` 的语义是「跑完了协议」。"""
    s.check_env_scores([{"env_name": "e", "score": 0.0, "samples": 500}])


def test_required_envs_gate_is_six_not_four() -> None:
    """必需 suite 是 **6 个不是 4 个**。

    worker 的 `--benchmark libero` profile 只产出 4 个 base env：
    6-env gate 会拒它，4-env gate 会**收下** —— 而 4 个 suite 的均值和 6 个的均值
    不可比，混进同一张榜就是送分。这正是这条校验存在的全部理由。
    """
    assert len(REQUIRED_ENVS) == 6
    s.check_required_envs(_full_env_scores())
    with pytest.raises(s.ContractError) as exc:
        s.check_required_envs(_full_env_scores()[:4])
    assert exc.value.code == s.CODE_MISSING_ENVS
    # 缺的名字要写进 message，矿工才知道自己少交了什么。
    assert "libero" in str(exc.value)


def test_required_envs_gate_tolerates_garbage_shapes() -> None:
    """非法形状不该在这里抛 `TypeError` —— 它只负责回答「齐没齐」。"""
    with pytest.raises(s.ContractError):
        s.check_required_envs("nope")
    with pytest.raises(s.ContractError):
        s.check_required_envs([None, 1, "x"])


def test_score_submission_keeps_total_score_verbatim() -> None:
    """`total_score` **原样存，不许重算**。

    worker 按 profile 权重加权算出来（`libero_plus` 的 `PLUS_SUITE_TASK_COUNTS`
    不等权），核对用 `abs_tol=1e-9` 逐位比。后端一重算两个值就不相等 →
    核对永远失败 → 每次超时/5xx 都重复 POST。
    """
    body = {
        "success": True,
        "total_score": 0.5029173,
        "env_scores": _full_env_scores(),
    }
    parsed = s.ScoreSubmission.model_validate(body)
    assert parsed.total_score == body["total_score"]
    assert parsed.model_dump()["total_score"] == body["total_score"]


def test_env_score_echoes_base_suite_and_perturbation() -> None:
    """`base_suite` / `perturbation` 必须**原样回显**。

    worker 的 `remote_score_matches` 逐条比对它们。被当作未声明的额外字段丢掉的话，
    本地有值 / 远端 None → 核对失败 → 重复 POST。
    """
    parsed = s.ScoreSubmission.model_validate(
        {
            "success": True,
            "total_score": 0.5,
            "env_scores": [
                {
                    "env_name": "libero_spatial",
                    "score": 0.5,
                    "samples": 100,
                    "base_suite": "libero_spatial",
                    "perturbation": "lan",
                }
            ],
        }
    )
    echoed = parsed.model_dump()["env_scores"][0]
    assert echoed["base_suite"] == "libero_spatial"
    assert echoed["perturbation"] == "lan"


def test_unknown_payload_fields_are_ignored_not_rejected() -> None:
    """评测方加一个新字段**不该**是销毁 GPU 工时的按钮。

    4xx 对 worker 是 `permanent` → `abandoned` → 丢弃一次完整评测结果
    （8 小时 GPU 时间）。所以 extra 必须是 ignore，不许改成 forbid。
    """
    parsed = s.ScoreSubmission.model_validate(
        {
            "success": True,
            "total_score": 0.5,
            "env_scores": _full_env_scores(),
            "a_field_invented_next_quarter": {"deep": [1, 2, 3]},
        }
    )
    assert "a_field_invented_next_quarter" not in parsed.model_dump()


def test_score_accepted_carries_the_terminal_guard_flag() -> None:
    """终态守卫命中时是 **200 + `ignored=True`**，不是 4xx 也不是 5xx。

    5xx 会让 worker 无限退避重试、一直卡在这个永远不会再有效的任务上领不到新活；
    200 是唯一让它「记为已提交、继续往下走」的答案。
    """
    accepted = s.ScoreAccepted(
        task_id="task_x", ignored=True, message="superseded, discarded"
    )
    assert accepted.ok is True
    assert accepted.ignored is True


# --- 进度上报（ZCY-158 词表 + 事故 ⑧ detail 被挑键） ---


@pytest.mark.parametrize(
    "word",
    [
        "downloading",
        "prechecking",
        "precheck",  # 前端 `QueueProgressStage` 里的写法
        "evaluating",  # worker 内部词
        "running",  # 公开文档 SUBNET_OVERVIEW.md §6 用的词
        "RUNNING",  # 旧实现 .strip().lower()
        "  running  ",
        "benchmark_running",  # 库里的存储形态
    ],
)
def test_progress_accepts_every_partys_spelling(word: str) -> None:
    """四方词表全收下 —— 少收一个就是 400。

    而 `_report_progress` 是 best-effort（吞掉 `BackendError` 只打 warning），
    **400 不会被任何人发现**，进度条就那么消失了（2026-08-14 实际发生）。
    """
    update = s.ProgressUpdate.from_payload({"task_id": "t", "stage": word})
    assert update.stage in ALL_STAGES


def test_progress_status_wins_over_stage() -> None:
    """`status` 与 `stage` 都接受，**`status` 优先**（既有调用方行为不变）。"""
    update = s.ProgressUpdate.from_payload(
        {"task_id": "t", "status": "downloading", "stage": "running"}
    )
    assert update.stage == "downloading"
    # 空 `status` 不该把 `stage` 顶掉 —— 那正是 `Unknown status: ""` 的来历。
    fallback = s.ProgressUpdate.from_payload(
        {"task_id": "t", "status": "  ", "stage": "running"}
    )
    assert fallback.stage == "running"


def test_progress_rejects_unknown_and_missing_stage() -> None:
    """未知 / 空 stage → `INVALID_STAGE`，**不是默认空串**。

    放宽成默认空串的后果是：打错字的 stage 静默入库、前端渲染不出进度条，
    而且没有任何人会发现。
    """
    bodies = (
        {"task_id": "t"},
        {"task_id": "t", "stage": ""},
        {"task_id": "t", "stage": "typoo"},
    )
    for body in bodies:
        with pytest.raises(s.ContractError) as exc:
            s.ProgressUpdate.from_payload(body)
        assert exc.value.code == s.CODE_INVALID_STAGE


def test_progress_done_and_failed_are_the_known_time_bomb() -> None:
    """worker 的 `_PROGRESS_STAGE_MAP` 里有 `done` / `failed`，后端词表里没有。

    当前 `worker.py` 只用三个词所以没触发。**这是一颗定时炸弹**：评测方哪天上报
    终态就 400。这条测试钉住「今天确实是 400」，改这个行为要连着 worker 一起改。
    """
    for word in ("done", "failed"):
        with pytest.raises(s.ContractError):
            s.ProgressUpdate.from_payload({"task_id": "t", "stage": word})


def test_progress_from_payload_reraises_non_contract_errors() -> None:
    """不是 stage 问题的校验失败要原样抛出，不许伪装成 `INVALID_STAGE`。"""
    with pytest.raises(ValidationError):
        s.ProgressUpdate.from_payload("not-a-dict")  # type: ignore[arg-type]


def test_progress_model_normalizes_even_when_fastapi_parses_it() -> None:
    """归一化在**模型层**，不在路由里。

    两条路径（`/api/benchmark-progress` 与 `/api/v1/benchmark/progress`）无论谁先接、
    是直接交给 FastAPI 还是走 `from_payload`，都不可能各自复制一份再漂掉。
    """
    direct = s.ProgressUpdate.model_validate({"task_id": "t", "stage": "evaluating"})
    assert direct.stage == "running"
    # 幂等：已经是规范词的输入原样通过。
    assert s.ProgressUpdate.model_validate(direct.model_dump()).stage == "running"


def test_progress_detail_is_passed_through_whole() -> None:
    """`detail` 对象**整个原样存** —— 事故 ⑧ 的守卫。

    2026-08-14 的部署改成只挑 `progress` / `current_env` 两个顶层键，而 worker
    根本不发这两个字段，于是库里永远是 `{"progress": null, "current_env": null}`。
    队列页从 `7/16 SUITES / libero_goal_lan` 变成光秃秃的 `EVALUATING`，
    后端也因此答不上「任务跑到哪一步」。
    """
    detail = {
        "suites_done": 7,
        "suites_total": 16,
        "current_suite": "libero_goal_lan",
        "episodes_done": 40,
        "a_field_worker_added": "keep me",
    }
    update = s.ProgressUpdate.from_payload(
        {"task_id": "t", "stage": "running", "detail": detail, "worker_id": "v-01"}
    )
    assert update.detail == detail
    assert update.worker_id == "v-01"


def test_progress_detail_falls_back_to_flat_top_level_fields() -> None:
    """`detail` 不是对象 / 是 None / 缺失 → 退回顶层扁平写法，**不抛异常**。"""
    assert s.extract_progress_detail({"detail": "garbage", "suites_done": 2}) == {
        "suites_done": 2
    }
    assert s.extract_progress_detail({"detail": None, "episodes_total": 100}) == {
        "episodes_total": 100
    }
    # `detail` 优先，顶层独有的键补进来。
    assert s.extract_progress_detail(
        {"detail": {"suites_done": 9}, "suites_done": 1, "episodes_done": 40}
    ) == {"suites_done": 9, "episodes_done": 40}


def test_progress_detail_empty_is_an_empty_object() -> None:
    """完全没有进度信息 → **空对象 `{}`**，绝不再产生 `{"progress":null,...}` 空壳。"""
    assert s.extract_progress_detail({"task_id": "t"}) == {}


def test_progress_response_cannot_disagree_with_itself() -> None:
    """响应同时给 `status` 和 `stage`，且**不可能不一致**。

    前端类型叫 `stage`、旧调用方读 `status`，只给一个就总有一方拿到 `undefined`
    —— 这正是 ZCY-158 的形状。`status` 是 computed_field，不一致在类型层不可表达。
    """
    accepted = s.ProgressAccepted(task_id="t", stage="running")
    dumped = accepted.model_dump()
    assert dumped["status"] == dumped["stage"] == "running"
    assert dumped["success"] is True
    # 想手工把两者掰开也做不到：`status` 不是可赋值字段，传进来会被忽略。
    forced = s.ProgressAccepted(task_id="t", stage="running", status="downloading")  # type: ignore[call-arg]
    assert forced.status == "running"


# --- 落库核对 / 读路径 ---


def test_submission_record_keeps_both_hotkey_spellings() -> None:
    """`hotkey` 与 `miner_hotkey` 双写，**一个都不能删**（worker 两个都认）。"""
    record = _submission_record()
    assert record.hotkey == record.miner_hotkey


def test_submission_record_result_is_none_when_not_scored_yet() -> None:
    """还没评完时 `result` 是 `null`。

    库里是 `{}` 或 `""`，出口归一成 `null` —— worker 的核对因此返回 False，
    这是**正确**结果：确实没落库。
    ⚠️ 反过来，**不许**给 `success` / `total_score` 加默认值让 `{}` 也能解析成功，
    那等于凭空造一个 `total_score=0.0` 出来。
    """
    assert _submission_record().result is None
    with pytest.raises(ValidationError):
        s.ScoreSubmission.model_validate({})


def test_read_path_tolerates_historical_dirty_scores() -> None:
    """读路径**不加值域约束** —— 生产库里真的有 `score=99.0` 这种行。

    2026-08-14 那次就是这么进去的。给读模型加 `le=1.0` 等于让「读一条历史脏数据」
    变成 500，而 5xx 对 worker 是「重复 POST」的按钮。
    值域检查留在写路径的 `check_env_scores()` 上，一处守卫守在边界。
    """
    record = _submission_record(
        result={
            "success": True,
            "total_score": 99.0,
            "env_scores": [{"env_name": "e", "score": 99.0, "samples": -5}],
        }
    )
    assert record.result is not None
    assert record.result.env_scores[0].score == 99.0
    # 同一份数据走写路径必须被拒。
    with pytest.raises(s.ContractError):
        s.check_env_scores([{"env_name": "e", "score": 99.0, "samples": -5}])


def test_worker_status_words_are_not_what_the_backend_writes() -> None:
    """🔴 钉住那条**已确认的静默故障**：两套词表今天对不上。

    worker 的落库核对只接受 `done` / `scored` / `failed`，而后端写的是
    `evaluated` / `eval_failed`，0002 之后 `done` 已被 CHECK 禁止。
    推断核对**恒为 False** → 每次 POST /score 超时或 5xx 都走完整重试路径，
    而这条链路上没有任何告警。

    这条测试**不主张哪一边对**，它主张「这件事今天确实成立」——
    等 spec 07 §10 Q2 拍板后，改的是这条测试和 `worker_status_alias` 的接线。
    """
    assert s.WORKER_ACCEPTED_STATUSES & ALL_STATUSES == frozenset()


def test_worker_status_alias_is_defined_but_deliberately_unwired() -> None:
    """转换函数已就位、带 TODO，但**没有任何模型在调它**。

    把它放在明面上而不是偷偷接进查询里：ZCY-158 的教训就是翻译表被藏在消费方
    （worker 的 `_PROGRESS_STAGE_MAP` 至今还在），于是没人知道两边其实对不上。
    """
    assert s.worker_status_alias("evaluated") == "done"
    assert s.worker_status_alias("eval_failed") == "failed"
    # 表外的词原样返回 —— 合法性由 `ALL_STATUSES` 判，不是这个函数的事。
    assert s.worker_status_alias("superseded") == "superseded"
    assert s.worker_status_alias("whatever") == "whatever"
    # 未接线：详情响应仍然直出库里的规范词。
    assert _submission_record(status="evaluated").status == "evaluated"


def _submission_record(**overrides: Any) -> s.SubmissionRecord:
    payload: dict[str, Any] = {
        "task_id": "task_abc_r1_v1",
        "status": "evaluated",
        "hotkey": "5FQxZ",
        "miner_hotkey": "5FQxZ",
        "hf_repo_id": "x/y",
        "hf_commit": "a" * 40,
        "round_num": 1,
    }
    payload.update(overrides)
    return s.SubmissionRecord.model_validate(payload)


# --- 榜单 / 轮次 ---


def test_leaderboard_row_carries_round_num() -> None:
    """`round_num` 是生产有、新骨架漏了的那个键 —— 删掉读它的人拿 undefined。"""
    assert _leaderboard_row().round_num == 1


def test_champion_score_shape_matches_leaderboard_rank1() -> None:
    """`champion.score` 是裸 float，必须能等于榜单 rank1 的 `score.mean`。

    跨端点一致性断言 2（spec 04 §5）。这里只锁类型形状 ——
    值相等要在后端的集成测试里断。
    """
    row = _leaderboard_row()
    champion = s.Champion(
        miner_hotkey=row.miner.hotkey,
        miner_name=row.miner.display_name,
        model_name=row.model.name,
        score=row.score.mean,
    )
    assert champion.score == row.score.mean
    assert champion.held is True
    assert champion.delta_vs_prev_champion is None


def test_round_detail_start_and_end_stay_null() -> None:
    """没有轮次表，`started_at` / `ends_at` 编不出来 → 恒 `null`。

    **不要拿本地时间凑**。
    """
    detail = s.RoundDetail(
        id=1,
        label="Round 01",
        status="live",
        network="finney",
        base_model=s.ModelRef(name="pi0.5", hf_repo="x/y"),
        submission_count=117,
    )
    assert detail.started_at is None and detail.ends_at is None
    assert detail.champion is None


def test_empty_database_returns_round_null_not_an_error_object() -> None:
    """整库为空是 `{"round": null}` + 200。

    **不是** `{"error": "no rounds found"}` + 200（用 200 表达失败，前端已被迫在
    边界做归一化），**也不是** 404。
    """
    assert s.CurrentRoundResponse().model_dump() == {"round": None}


def test_score_std_is_none_not_zero_for_single_trial() -> None:
    """只跑过一次的提交 `std` 是 `None`，**不是 0** —— 0 会被读成「零方差」。"""
    assert s.ScoreStat(mean=0.5).std is None


# --- 探针 ---


def test_liveness_status_is_a_constant() -> None:
    """`/healthz` 的 `status` 永远是 `"ok"`，其它值不可表达。"""
    assert s.LivenessResponse(round=1, netuid=80).status == "ok"
    with pytest.raises(ValidationError):
        s.LivenessResponse(round=1, netuid=80, status="degraded")  # type: ignore[arg-type]


def test_readiness_body_shape_is_identical_for_200_and_503() -> None:
    """就绪与不就绪**同一个形状**，调用方不该为失败准备第二套解析。"""
    ready = s.ReadinessResponse(
        ready=True,
        database=s.ReadinessCheck(ok=True),
        migration=s.ReadinessCheck(ok=True),
        alembic_version="0002",
    )
    down = s.ReadinessResponse(
        ready=False,
        database=s.ReadinessCheck(ok=False, detail="database unreachable"),
        migration=s.ReadinessCheck(ok=False, detail="expected 0002, found 0000"),
    )
    assert set(ready.model_dump()) == set(down.model_dump())


# --- 结构性约束 ---


def test_models_are_frozen() -> None:
    """响应模型建好之后不许再改。

    这个仓库的历史问题恰恰是出口处被人手改字段（`row.pop("status")` 那一类）。
    """
    with pytest.raises(ValidationError):
        _leaderboard_row().rank = 2


def test_weights_are_fractions_not_u16_integers() -> None:
    """`/api/weights` 的值是 **0~1 的 float**，不是 u16 整数。

    新骨架 `legacy.py:244` 写的是 `dict[str, int]` —— 接上真实数据的那一刻
    Pydantic 拿 `0.9` 去满足 `int` 会 500，外部验证者的 `fetch_weights()`
    把异常吞成 `{}` → 发不出 `set_weights` → **全网排放停摆，只有一行 warning**。
    u16 归一化在调用方（`validator.normalize_weights`）。
    """
    weights: s.Weights = {"5HTwty": 0.9, "5FQxZ": 0.07, "5DoaV8": 0.02, "5Gpih1": 0.01}
    assert all(isinstance(v, float) for v in weights.values())
    assert sum(weights.values()) == pytest.approx(1.0)
    assert len(weights) <= 4


def test_leaderboard_generated_at_is_the_only_moving_field() -> None:
    """不变量 4：同一份数据两次请求，除 `generated_at` 外逐字段相等。

    这里锁的是**形状**（只有这一个字段带服务器时刻）；真正的幂等断言在后端。
    """
    keys = _RESPONSE_KEYS[s.LeaderboardResponse]
    assert "generated_at" in keys
    response = s.LeaderboardResponse(
        round_id=1,
        generated_at=datetime.now(UTC),
        baseline=s.Baseline(
            model_name="pi0.5", hf_repo="x/y", score=s.ScoreStat(mean=0.502917)
        ),
    )
    assert response.total == 0 and response.rows == []


def test_miner_facing_imports_do_not_require_pydantic() -> None:
    """矿工那几个模块 **不得**拖进 pydantic。

    这是把 pydantic 放进 `[schemas]` optional-dependency 的全部意义：矿工装这个包
    只为推导 seed 和解 commitment，不该在 GPU 机器上编译一个 pydantic-core 轮子。
    承诺靠「`__init__.py` 不 re-export 任何东西」成立 —— 这条测试钉住它。

    必须开子进程：本文件顶上已经 `from openroboto_protocol import schemas`，
    当前解释器里 `sys.modules` 早就有 pydantic 了，在进程内断言等于自欺。
    """
    probe = (
        "import sys; import openroboto_protocol, openroboto_protocol.seed, "
        "openroboto_protocol.commitment, openroboto_protocol.model_hash, "
        "openroboto_protocol.status, openroboto_protocol.constants; "
        "assert openroboto_protocol.__all__ == []; "
        "leaked = [m for m in sys.modules if m.split('.')[0] == 'pydantic']; "
        "print(leaked)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]", f"pydantic 被拖进来了: {result.stdout}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. 响应信封（ADR 02）
# ─────────────────────────────────────────────────────────────────────────────
#
# 这一组锁的是**信封本身**的四条规则，不是某个端点的字段。三条已经在类型层不可
# 表达（见 `schemas.py` 里那段注释），这里做的是回归护栏：有人把 `Meta.page`
# 加回去、给 `retryable` 补个默认值、或者顺手把探针也套上信封时，这里红。


def _meta() -> s.Meta:
    return s.Meta(request_id="a3f81dbf1c2e")


def _page_meta() -> s.PageMeta:
    return s.PageMeta(total=7, limit=50, offset=0, has_more=False)


def _miner() -> s.MinerRef:
    return s.MinerRef(hotkey="5FQxZBhriyAvXXXX", display_name="5FQxZBhriyAv")


def test_success_response_always_has_data_and_never_error() -> None:
    """成功响应**一定**有 `data`、**一定**没有 `error`。

    `code: 0` 表示成功是个约定，不看文档看不出来；`data` / `error` 二选一是
    **结构上**的区分。这里连「两个都有」这种状态都构造不出来 —— `Envelope`
    根本没有 `error` 字段，硬塞进来会被 `Contract` 的 `extra=ignore` 丢掉。
    """
    body = s.Envelope[s.MinerRef](data=_miner(), meta=_meta()).model_dump(mode="json")
    assert set(body) == {"data", "meta"}

    sneaked = s.Envelope[s.MinerRef].model_validate(
        {
            "data": {"hotkey": "5F", "display_name": "5F"},
            "meta": {"request_id": "x"},
            "error": {"code": "NOPE", "message": "x", "retryable": False},
        }
    )
    assert "error" not in sneaked.model_dump()


def test_error_response_always_has_error_and_never_data() -> None:
    """错误响应反之。`retryable` **没有默认值**，必须显式想一次。

    默认 `False` 等于替每个忘了想的人选了「不可重试」那一边 —— 而那一边的代价是
    worker 把一次链 RPC 抖动当成永久失败，销毁一份 8 小时的评测结果，
    矿工烧掉的 TAO 不退。
    """
    body = s.ErrorEnvelope(
        error=s.ErrorBody(
            code=s.CODE_INVALID_SCORE, message="score out of range", retryable=False
        ),
        meta=_meta(),
    ).model_dump(mode="json")
    assert set(body) == {"error", "meta"}
    assert "data" not in body

    with pytest.raises(ValidationError):
        s.ErrorBody(code="INFRA_ERROR", message="chain rpc down")  # type: ignore[call-arg]


def test_only_validation_errors_carry_fields() -> None:
    """`fields` **只在 422 的那个子类上**，普通错误连这个键都不出现。

    ADR 02 §8 未决问题 ② 的落地形状（2026-08-18）。走子类而不是
    `fields: … | None = None`：可选字段要靠每个出口记得 `exclude_none`，
    漏一个就多吐 `"fields": null`，而忘一次是静默的 —— 和 `meta.page` 走
    `ListMeta` 子类是同一个理由。

    这条同时钉住**基类没被顺手改**：`ValidationErrorBody` 继承 `ErrorBody`，
    下面那个键集断言是继承展开后的，基类少一个字段这里就红。
    """
    plain = s.ErrorEnvelope(
        error=s.ErrorBody(code="NOT_FOUND", message="no", retryable=False),
        meta=_meta(),
    ).model_dump(mode="json")
    assert "fields" not in plain["error"]

    invalid = s.ValidationErrorEnvelope(
        error=s.ValidationErrorBody(
            code="VALIDATION_ERROR",
            message="请求体校验失败（1 个字段）",
            retryable=False,
            fields=[
                {"loc": "body.env_scores", "msg": "field required", "type": "missing"}
            ],
        ),
        meta=_meta(),
    ).model_dump(mode="json")
    assert set(invalid) == {"error", "meta"}
    assert "data" not in invalid
    assert invalid["error"]["fields"][0]["loc"] == "body.env_scores"

    # `fields` 必填 —— 忘了带的 422 构造不出来，而「422 没有逐字段信息」
    # 正是这个子类存在的全部理由。
    with pytest.raises(ValidationError):
        s.ValidationErrorBody(  # type: ignore[call-arg]
            code="VALIDATION_ERROR", message="x", retryable=False
        )


def test_request_id_is_on_every_response_including_errors() -> None:
    """`meta.request_id` 恒存在。**错误响应上尤其存在** —— 那正是要查的那个。

    必填字段，缺了整个响应构造不出来。给它默认值等于允许「查不到的那个响应」
    存在，而出问题时用户能贴过来的只有它。
    """
    with pytest.raises(ValidationError):
        s.Meta()  # type: ignore[call-arg]

    for body in (
        s.Envelope[s.MinerRef](data=_miner(), meta=_meta()).model_dump(mode="json"),
        s.ListEnvelope[s.MinerRef](
            data=[], meta=s.ListMeta(request_id="a3f81dbf1c2e", page=_page_meta())
        ).model_dump(mode="json"),
        s.ErrorEnvelope(
            error=s.ErrorBody(code="NOT_FOUND", message="no", retryable=False),
            meta=_meta(),
        ).model_dump(mode="json"),
    ):
        assert body["meta"]["request_id"] == "a3f81dbf1c2e"
        assert body["meta"]["generated_at"] is not None


def test_page_meta_appears_only_on_list_endpoints() -> None:
    """`meta.page` **只有列表端点有**，单对象端点连这个键都不出现。

    不是靠 `exclude_none`（那要求每个路由记得写 `response_model_exclude_none=True`，
    漏一个就吐 `"page": null`，而且是静默的），是靠 `Envelope.meta` 的声明类型
    `Meta` 里**根本没有这个字段**。下面第二段证明：就算硬塞一个 `ListMeta` 进单对象
    信封，序列化仍按声明类型走。
    """
    assert "page" not in _serialized_keys(s.Meta)
    assert "page" in _serialized_keys(s.ListMeta)

    single = s.Envelope[s.MinerRef](
        data=_miner(),
        meta=s.ListMeta(request_id="a3f81dbf1c2e", page=_page_meta()),
    )
    assert "page" not in single.model_dump()["meta"]

    listed = s.ListEnvelope[s.MinerRef](
        data=[_miner()],
        meta=s.ListMeta(request_id="a3f81dbf1c2e", page=_page_meta()),
    ).model_dump(mode="json")
    assert listed["meta"]["page"] == {
        "total": 7,
        "limit": 50,
        "offset": 0,
        "has_more": False,
    }


@dataclass(frozen=True, slots=True)
class _RepoPage:
    """`app/repositories/pagination.py:Page` 的形状（dataclass，items 是 tuple）。"""

    items: tuple[int, ...]
    total: int
    limit: int
    offset: int


class _ApiPage(BaseModel):
    """`app/api/schemas.py:Page` 的形状（pydantic 模型，items 是 list）。"""

    items: list[int]
    total: int
    limit: int
    offset: int


@pytest.mark.parametrize(
    ("count", "total", "offset", "expected"),
    [
        (50, 137, 0, True),  # 第一页，后面还有
        (37, 137, 100, False),  # 末页正好取完
        (0, 0, 0, False),  # 空列表不是「还有下一页」
        (50, 50, 0, False),  # 一页装得下全部
    ],
)
def test_has_more_is_computed_in_exactly_one_place(
    count: int, total: int, offset: int, expected: bool
) -> None:
    """`has_more` 由 `PageMeta.of()` 算，调用方不许自己拼这个表达式。

    `offset + len(items) < total` 复制到 8 个列表端点上，写错一个就是静默翻页丢行
    —— 而翻页丢行没有任何一方会报错，矿工只会来问「dashboard 没有我的提交」
    （ZCY-162 的原始形状）。

    两种 `Page` 都得能直接喂进来：仓储层那个是 dataclass、API 层那个是 pydantic
    模型，`PageLike` 是结构类型，两个都不用改。
    """
    items = tuple(range(count))
    repo = _RepoPage(items=items, total=total, limit=50, offset=offset)
    api = _ApiPage(items=list(items), total=total, limit=50, offset=offset)
    assert s.PageMeta.of(repo) == s.PageMeta.of(api)
    assert s.PageMeta.of(repo).has_more is expected


def test_probes_are_never_enveloped() -> None:
    """探针是**唯一的例外**：`/healthz` `/readyz` 不套信封，`/metrics` 同理。

    它们的消费方是 PM2 / 负载均衡 / Prometheus，套了信封对方直接解析不了 ——
    健康检查解不出来的后果是**流量被摘掉或进程被反复重启**，比字段错还急。
    `/metrics` 在协议包里没有模型（它是 `text/plain` 的 Prometheus 文本格式，
    见后端 `app/core/metrics.py`），这条只能钉住两个探针。
    """
    for probe in (s.LivenessResponse, s.ReadinessResponse):
        assert _serialized_keys(probe) & {"data", "meta", "error"} == set()
    assert _serialized_keys(s.LivenessResponse) == {"round", "netuid", "status"}


def test_envelope_generics_survive_into_openapi() -> None:
    """`response_model=Envelope[LeaderboardRow]` 必须生成**具体**的 OpenAPI schema。

    泛型退化成 `data: object` 的话，前端和 worker 生成出来的类型里 `data` 是 `any`
    —— 那就等于把这个包存在的理由（字段契约在类型里）扔了。
    """
    single = s.Envelope[s.LeaderboardRow].model_json_schema(mode="serialization")
    assert single["title"] == "Envelope[LeaderboardRow]"
    assert single["properties"]["data"] == {"$ref": "#/$defs/LeaderboardRow"}
    assert "LeaderboardRow" in single["$defs"]

    listed = s.ListEnvelope[s.ScanRejection].model_json_schema(mode="serialization")
    assert listed["properties"]["data"]["type"] == "array"
    assert listed["properties"]["data"]["items"] == {"$ref": "#/$defs/ScanRejection"}


def test_envelopes_are_frozen_like_every_other_response() -> None:
    """信封也是响应模型，建好之后不许改 —— 出口处手改字段是这个仓库的历史问题。"""
    with pytest.raises(ValidationError):
        _meta().request_id = "另一个"
