"""Contract tests for `schemas.py`.

**This file plays a different role from an ordinary unit test**: what it pins
down is not "does the function compute the right thing" but "what does the
response look like". One field missing, one name changed, one extra word in an
enum — all of them have to go red here, because the real consumers (the
frontend, the GPU worker, the miner CLI, external validators) run on other
people's machines: breaking them raises no error, they just silently get
`undefined`.

Three kinds of assertions, matching the three things the task asks for:

1. **The field set is neither more nor less** — the key set of every response
   model is written out verbatim here (`_RESPONSE_KEYS`). It uses the
   serialization schema rather than `model_fields`, so that `computed_field`
   (`ProgressAccepted.status`) is counted too — what the consumer sees is the
   serialized JSON.
2. **Enum values must come from the vocabulary in `status.py`** — the stage
   words equal `ALL_STAGES`; the leaderboard-position words / round words have
   zero overlap with the lifecycle words ("one response, one vocabulary").
3. **Behaviour that has bitten someone** — the docstring of each test spells out
   which incident it guards against.
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
# 1. The field set is neither more nor less
# ─────────────────────────────────────────────────────────────────────────────

#: The key set of every model after serialization. **Written out verbatim**;
#: changing it here is the same as changing the outward contract.
#:
#: Source: the "response fields" row of the 6 contract cards, each of which cites
#: a live measurement or a place in production code. Adding a field is minor (a
#: consumer missing the key has a default), deleting or renaming a field is major
#: — both of them must change this table first, and if it cannot be changed that
#: means the change should not have been made in the first place.
_RESPONSE_KEYS: dict[type[BaseModel], set[str]] = {
    # —— response envelopes (ADR 02) ——
    s.Envelope: {"data", "meta"},
    s.ListEnvelope: {"data", "meta"},
    s.ErrorEnvelope: {"error", "meta"},
    s.ErrorBody: {"code", "message", "retryable"},
    s.ValidationErrorEnvelope: {"error", "meta"},
    s.ValidationErrorBody: {"code", "message", "retryable", "fields"},
    s.Meta: {"request_id", "generated_at"},
    s.ListMeta: {"request_id", "generated_at", "page"},
    s.PageMeta: {"total", "limit", "offset", "has_more"},
    # —— shared fragments ——
    s.MinerRef: {"hotkey", "display_name"},
    s.ModelRef: {"name", "hf_repo", "revision"},
    s.ScoreStat: {"mean", "std", "trials"},
    s.Reason: {"code", "message", "retryable", "source"},
    # —— the worker contract group ——
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
    # `status` is a computed_field — old callers read it, the frontend reads
    # `stage`, and both have to be given.
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
    # —— leaderboard and rounds ——
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
    # —— operational probes ——
    s.LivenessResponse: {"round", "netuid", "status"},
    s.ReadinessCheck: {"ok", "detail"},
    s.ReadinessResponse: {"ready", "database", "migration", "alembic_version"},
}


def _serialized_keys(model: type[BaseModel]) -> set[str]:
    """The set of JSON keys a model serializes to (including
    `computed_field`)."""
    schema = model.model_json_schema(mode="serialization")
    return set(schema["properties"])


@pytest.mark.parametrize(
    ("model", "expected"),
    list(_RESPONSE_KEYS.items()),
    ids=lambda v: getattr(v, "__name__", ""),
)
def test_field_set_is_exact(model: type[BaseModel], expected: set[str]) -> None:
    """The field set is **neither more nor less**.

    One missing = the consumer gets `undefined` (the frontend commit
    `Tolerate rebuilt-backend field renames` was cleaning up after us); one extra
    = the outward promise has been quietly widened, and the version number of
    this package *is* the contract version, so adding a field has to be an
    explicit minor bump.
    """
    assert _serialized_keys(model) == expected


def test_every_exported_model_is_pinned() -> None:
    """A newly added model must be added to `_RESPONSE_KEYS` at the same time.

    Without this test, "adding a model but forgetting to pin its field set" would
    be completely silent — and that is exactly what this file exists to prevent.
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
    """A history row **never returns `status`** — a response is allowed only one
    status key.

    The frontend's `normalizeHistoryStatus()` is
    `submission.status || submission.eval_status`, i.e. it **reads `status`
    first**, and the one it reads first happens to be the un-normalized one —
    that is where 33 of the 95 rows showing the wrong status came from (measured
    on the 2026-08-19 copy of the two production sources: 80 rows disagree). The
    old implementation only escaped by doing `row.pop("status", None)` at the
    exit point; here it is because the model simply does not have this field.
    """
    assert "status" not in _serialized_keys(s.SubmissionHistoryItem)
    assert "eval_status" in _serialized_keys(s.SubmissionHistoryItem)


def test_history_item_hides_internal_columns() -> None:
    """None of the 4 internal fields may appear.

    `repo_hash` is **the model fingerprint used for the plagiarism verdict**;
    publishing it means handing over the basis of the verdict.
    `legacy_task_id` / `hotkey_tag` / `worker_id` are internal operational
    fields. Live measurement showed all 33 of these keys **fully exposed**; this
    test is the regression guard after narrowing that down.
    """
    leaked = {"legacy_task_id", "repo_hash", "hotkey_tag", "worker_id", "eval_detail"}
    assert leaked & _serialized_keys(s.SubmissionHistoryItem) == set()


def test_scan_rejection_has_no_surrogate_id() -> None:
    """Production does not return `id` (the new skeleton added one extra), so it
    is not added here either."""
    assert "id" not in _serialized_keys(s.ScanRejection)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Enum values must come from the vocabulary in status.py
# ─────────────────────────────────────────────────────────────────────────────


def test_stage_vocabulary_is_status_py() -> None:
    """There is **only one** stage vocabulary: `EvalStage` must equal
    `status.ALL_STAGES` verbatim.

    The shape of ZCY-158 was exactly this: the same thing spelled its own way in
    four repos. Once this goes red, this module has grown a fifth one.
    """
    assert frozenset(get_args(s.EvalStage)) == ALL_STAGES


def test_display_vocabularies_never_overlap_lifecycle_words() -> None:
    """The leaderboard-position words / round words have **zero overlap** with
    the lifecycle words — "one response, one vocabulary".

    Production `/api/v1/leaderboard` emits both `champion` (a
    leaderboard-position word) and `scored` (a lifecycle word) in the same
    `status` field, and because of a `LIMIT 1` with no `ORDER BY`, two requests
    over the same data give different words.
    """
    assert s.LEADERBOARD_STATUSES & ALL_STATUSES == frozenset()
    assert s.ROUND_STATUSES & ALL_STATUSES == frozenset()
    assert s.LEADERBOARD_STATUSES & s.ROUND_STATUSES == frozenset()


def test_leaderboard_status_rejects_lifecycle_words() -> None:
    """Stuffing a lifecycle word into the leaderboard `status` must fail; it must
    not be merely "a convention not to do that"."""
    with pytest.raises(ValidationError):
        _leaderboard_row(status="evaluating")
    with pytest.raises(ValidationError):
        # The current value in the new skeleton's mock. It is not in the
        # frontend union type, not in the CSS, and `eliminated` does not hold
        # semantically either — a miner who failed the challenge is not in rows
        # at all.
        _leaderboard_row(status="eliminated")


def test_round_status_rejects_undecided_scoring_word() -> None:
    """The third state `scoring` has no defensible definition (spec 04 §9 Q2), so
    it does not enter the vocabulary until that is settled."""
    with pytest.raises(ValidationError):
        s.RoundSummaryEntry(id=1, label="Round 01", status="scoring")  # type: ignore[arg-type]


def test_queue_summary_buckets_are_real_status_words() -> None:
    """Every bucket in the summary must be a status word that really exists,
    except `unknown` / `total`.

    Check it the other way round as well: the words in `ALL_STATUSES` that have
    no bucket (`received` / `burn_checking` / `burn_passed` / `seed_failed`) fall
    into `unknown` — **falling in there must raise an alert, they must not be
    dropped silently**; that is the lesson of ZCY-130 undercounting by 45 rows.
    """
    assert set(s.QUEUE_SUMMARY_BUCKETS) <= ALL_STATUSES
    bucket_fields = set(s.QueueSummary.model_fields) - {"unknown", "total"}
    assert bucket_fields == set(s.QUEUE_SUMMARY_BUCKETS)
    assert ALL_STATUSES - set(s.QUEUE_SUMMARY_BUCKETS) != set()


def test_status_valued_fields_stay_open_typed() -> None:
    """Lifecycle status fields are annotated as `str`, **not `Literal`**.

    This is a considered trade-off: when a status word outside the vocabulary
    turns up in the database, the correct reaction is to degrade that one row and
    raise an alert, not to 500 the whole endpoint — a 5xx is the "write to the
    database again" button for the worker (spec 07 §0.3).
    """
    for model, name in s.STATUS_VALUED_FIELDS:
        assert name in model.model_fields, f"{model.__name__}.{name} is gone"
        assert model.model_fields[name].annotation is str


def test_reason_code_vocabulary_is_closed() -> None:
    """`reason.code` is a controlled vocabulary; a code outside it cannot get in
    at the model layer.

    ZCY-162: the rejection reason for `superseded` was **nowhere to be read**, so
    the frontend had to hard-code `unavailable`. `SUPERSEDED` must be in the
    vocabulary; that is the antidote to that hard-coded line.
    """
    assert "SUPERSEDED" in s.REASON_CODES
    assert "INFRA_ERROR" in s.REASON_CODES
    with pytest.raises(ValidationError):
        s.Reason(code="NOPE", message="x", retryable=False, source="scan")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        s.Reason(code="SUPERSEDED", message="x", retryable=False, source="nope")  # type: ignore[arg-type]


def test_infra_failure_is_retryable_not_a_business_rejection() -> None:
    """An infrastructure failure is not a business rejection.

    Things like `burn_error: failed_to_create_subtensor` must be
    `retryable=True`, otherwise the TAO a miner burned is thrown away by one
    flaky chain RPC call. The model only guarantees that this field exists and is
    a bool; the value is decided by the chain-scanning side — what this test
    checks is that "the switch really is in the contract".
    """
    reason = s.Reason(
        code="INFRA_ERROR",
        message="failed to create subtensor",
        retryable=True,
        source="scan",
    )
    assert reason.retryable is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. Behaviour that has bitten someone
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


# --- empty env_list (incident ⑦: the worker spinning idle) ---


def test_empty_env_list_is_unrepresentable() -> None:
    """The `env_list` of a dispatched task **cannot** be an empty array.

    On 2026-08-14 the submissions of uid 221/231 entered the queue and the worker
    kept spinning idle; this is exactly where they got stuck: what the database
    stored was the string `'[]'`, a non-empty string is truthy, so neither an
    `or` fallback nor `.get(k, default)` catches it. It must be `json.loads`-ed
    first and only then checked for emptiness, and the 6 suites backfilled if it
    is empty — the model layer is the last line of defence, making "empty"
    illegal at the type level.
    """
    with pytest.raises(ValidationError):
        _queue_task(env_list=[])


def test_env_list_must_be_a_list_not_a_json_string() -> None:
    """`env_list` is an array, **not a JSON string**.

    Iterating a string directly splits it into single characters, which once made
    a task be misreported as "invalid env names". The fallback in the worker's
    `parse_env_list` is a leftover from that incident, not a design — the backend
    always gives an array.
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


# --- the four checks at the scoring entry point (incident: samples=-5 plus a
#     single suite dethroning the champion outright) ---


def test_bool_cannot_masquerade_as_a_score() -> None:
    """`{"score": true}` must be rejected — **both locks have to be there**.

    In Python `isinstance(True, int)` is True, so the check "score is a number"
    misses it if it is written as `isinstance(x, (int, float))`. And pydantic in
    lax mode converts `true` into `1.0` first (measured), so the model has to use
    `StrictFloat` *and* the raw JSON still has to go through `check_env_scores`.
    """
    with pytest.raises(ValidationError):
        s.EnvScore.model_validate({"env_name": "e", "score": True, "samples": 1})
    with pytest.raises(ValidationError):
        s.EnvScore.model_validate({"env_name": "e", "score": 0.5, "samples": True})
    with pytest.raises(s.ContractError) as exc:
        s.check_env_scores([{"env_name": "e", "score": True, "samples": 1}])
    assert exc.value.code == s.CODE_INVALID_SCORE


def test_strict_score_still_accepts_json_integers() -> None:
    """`StrictFloat` must not hit the integers the worker really does send.

    Measured: pydantic's strict mode does let int→float through; the worker's
    `total_score: 0` / `score: 1` must be accepted as they are, otherwise the
    scoring path breaks outright.
    """
    assert s.EnvScore.model_validate(
        {"env_name": "e", "score": 1, "samples": 100}
    ).score == pytest.approx(1.0)


def test_check_env_scores_rejects_nan_out_of_range_and_bad_samples() -> None:
    """NaN / out of range / negative samples all carry the stable code
    `INVALID_SCORE`.

    NaN must be **caught separately**: any comparison with it is False, so
    `0 <= x <= 1` misses it. The historical price was
    `{"score": 99.0, "samples": -5}` plus submitting only 1 suite → 200 →
    dethroning the champion outright and taking 7% of the weight.
    """
    # `check_env_scores` takes the **raw JSON**, so its legal inputs include
    # shapes like "not even a list" — typing it as object is deliberate, not
    # laziness.
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
    """A zero success rate is a **valid score**, not a failure. The meaning of
    `success` is "the protocol ran to completion"."""
    s.check_env_scores([{"env_name": "e", "score": 0.0, "samples": 500}])


def test_required_envs_gate_is_six_not_four() -> None:
    """The required suites are **6, not 4**.

    The worker's `--benchmark libero` profile produces only 4 base envs: a 6-env
    gate rejects it, a 4-env gate **accepts** it — and the mean over 4 suites is
    not comparable with the mean over 6, so mixing them into the same
    leaderboard is handing out free points. That is the entire reason this check
    exists.
    """
    assert len(REQUIRED_ENVS) == 6
    s.check_required_envs(_full_env_scores())
    with pytest.raises(s.ContractError) as exc:
        s.check_required_envs(_full_env_scores()[:4])
    assert exc.value.code == s.CODE_MISSING_ENVS
    # The missing names have to go into the message, otherwise the miner cannot
    # know what they failed to submit.
    assert "libero" in str(exc.value)


def test_required_envs_gate_tolerates_garbage_shapes() -> None:
    """An illegal shape must not raise `TypeError` here — this function only
    answers "is the set complete or not"."""
    with pytest.raises(s.ContractError):
        s.check_required_envs("nope")
    with pytest.raises(s.ContractError):
        s.check_required_envs([None, 1, "x"])


def test_score_submission_keeps_total_score_verbatim() -> None:
    """`total_score` is **stored verbatim and must not be recomputed**.

    The worker computes it weighted by the profile weights (the
    `PLUS_SUITE_TASK_COUNTS` of `libero_plus` are not equal weights), and the
    cross-check compares digit by digit with `abs_tol=1e-9`. The moment the
    backend recomputes it the two values differ → the cross-check fails forever →
    every timeout / 5xx leads to another repeated POST.
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
    """`base_suite` / `perturbation` must be **echoed back verbatim**.

    The worker's `remote_score_matches` compares them entry by entry. If they get
    dropped as undeclared extra fields, the local side has a value while the
    remote side is None → the cross-check fails → a repeated POST.
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
    """An evaluator adding a new field **must not** be the button that destroys
    GPU hours.

    A 4xx means `permanent` → `abandoned` for the worker → a complete evaluation
    result is thrown away (8 hours of GPU time). So extra must be ignore, and
    must not be changed to forbid.
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
    """When the terminal-state guard fires the answer is **200 plus
    `ignored=True`**, neither a 4xx nor a 5xx.

    A 5xx makes the worker retry with unbounded backoff, staying stuck on a task
    that will never be valid again and never picking up new work; 200 is the only
    answer that makes it "record this as submitted and move on".
    """
    accepted = s.ScoreAccepted(
        task_id="task_x", ignored=True, message="superseded, discarded"
    )
    assert accepted.ok is True
    assert accepted.ignored is True


# --- progress reporting (the ZCY-158 vocabulary plus incident ⑧, where keys
#     were cherry-picked out of detail) ---


@pytest.mark.parametrize(
    "word",
    [
        "downloading",
        "prechecking",
        "precheck",  # the spelling in the frontend `QueueProgressStage`
        "evaluating",  # the worker's internal word
        "running",  # the word used by the public doc SUBNET_OVERVIEW.md §6
        "RUNNING",  # the old implementation did .strip().lower()
        "  running  ",
        "benchmark_running",  # the stored form in the database
    ],
)
def test_progress_accepts_every_partys_spelling(word: str) -> None:
    """All four parties' vocabularies are accepted — accepting one fewer means a
    400.

    And `_report_progress` is best-effort (it swallows `BackendError` and only
    logs a warning), so **the 400 is not discovered by anyone** and the progress
    bar just disappears (this actually happened on 2026-08-14).
    """
    update = s.ProgressUpdate.from_payload({"task_id": "t", "stage": word})
    assert update.stage in ALL_STAGES


def test_progress_status_wins_over_stage() -> None:
    """Both `status` and `stage` are accepted, and **`status` wins** (the
    behaviour of existing callers is unchanged)."""
    update = s.ProgressUpdate.from_payload(
        {"task_id": "t", "status": "downloading", "stage": "running"}
    )
    assert update.stage == "downloading"
    # An empty `status` must not displace `stage` — that is precisely where
    # `Unknown status: ""` came from.
    fallback = s.ProgressUpdate.from_payload(
        {"task_id": "t", "status": "  ", "stage": "running"}
    )
    assert fallback.stage == "running"


def test_progress_rejects_unknown_and_missing_stage() -> None:
    """An unknown / empty stage → `INVALID_STAGE`, **not a default empty
    string**.

    The consequence of relaxing this to a default empty string is: a mistyped
    stage lands in the database silently, the frontend renders no progress bar,
    and nobody at all finds out.
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
    """The worker's `_PROGRESS_STAGE_MAP` has `done` / `failed`; the backend
    vocabulary does not.

    Today's `worker.py` only uses three words, so it has not been triggered.
    **This is a time bomb**: the day an evaluator reports a terminal state it is
    a 400. This test pins down "today it really is a 400"; changing that
    behaviour means changing the worker along with it.
    """
    for word in ("done", "failed"):
        with pytest.raises(s.ContractError):
            s.ProgressUpdate.from_payload({"task_id": "t", "stage": word})


def test_progress_from_payload_reraises_non_contract_errors() -> None:
    """A validation failure that is not a stage problem must be raised as it is,
    and must not be disguised as `INVALID_STAGE`."""
    with pytest.raises(ValidationError):
        s.ProgressUpdate.from_payload("not-a-dict")  # type: ignore[arg-type]


def test_progress_model_normalizes_even_when_fastapi_parses_it() -> None:
    """Normalization happens in the **model layer**, not in the route.

    Whichever of the two paths (`/api/benchmark-progress` and
    `/api/v1/benchmark/progress`) receives it first, and whether it is handed
    straight to FastAPI or goes through `from_payload`, it is impossible for each
    of them to keep its own copy and then drift.
    """
    direct = s.ProgressUpdate.model_validate({"task_id": "t", "stage": "evaluating"})
    assert direct.stage == "running"
    # Idempotent: input that is already a canonical word passes through
    # unchanged.
    assert s.ProgressUpdate.model_validate(direct.model_dump()).stage == "running"


def test_progress_detail_is_passed_through_whole() -> None:
    """The `detail` object is **stored whole and verbatim** — the guard for
    incident ⑧.

    The 2026-08-14 deployment changed it to cherry-pick only the two top-level
    keys `progress` / `current_env`, while the worker does not send those two
    fields at all, so the database forever held
    `{"progress": null, "current_env": null}`. The queue page went from
    `7/16 SUITES / libero_goal_lan` to a bare `EVALUATING`, and the backend could
    no longer answer "how far has the task got".
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
    """`detail` is not an object / is None / is missing → fall back to the flat
    top-level form, **without raising**."""
    assert s.extract_progress_detail({"detail": "garbage", "suites_done": 2}) == {
        "suites_done": 2
    }
    assert s.extract_progress_detail({"detail": None, "episodes_total": 100}) == {
        "episodes_total": 100
    }
    # `detail` wins, and keys that only exist at the top level are merged in.
    assert s.extract_progress_detail(
        {"detail": {"suites_done": 9}, "suites_done": 1, "episodes_done": 40}
    ) == {"suites_done": 9, "episodes_done": 40}


def test_progress_detail_empty_is_an_empty_object() -> None:
    """No progress information at all → an **empty object `{}`**; never again
    produce the empty shell `{"progress":null,...}`."""
    assert s.extract_progress_detail({"task_id": "t"}) == {}


def test_progress_response_cannot_disagree_with_itself() -> None:
    """The response gives both `status` and `stage`, and they **cannot
    disagree**.

    The frontend type is called `stage` while old callers read `status`; giving
    only one of them always leaves one side with `undefined` — which is exactly
    the shape of ZCY-158. `status` is a computed_field, so disagreement is
    unrepresentable at the type level.
    """
    accepted = s.ProgressAccepted(task_id="t", stage="running")
    dumped = accepted.model_dump()
    assert dumped["status"] == dumped["stage"] == "running"
    assert dumped["success"] is True
    # Prying the two apart by hand does not work either: `status` is not an
    # assignable field, so passing it in is ignored.
    forced = s.ProgressAccepted(task_id="t", stage="running", status="downloading")  # type: ignore[call-arg]
    assert forced.status == "running"


# --- persistence cross-check / read path ---


def test_submission_record_keeps_both_hotkey_spellings() -> None:
    """`hotkey` and `miner_hotkey` are both written, and **neither may be
    deleted** (the worker recognises both)."""
    record = _submission_record()
    assert record.hotkey == record.miner_hotkey


def test_submission_record_result_is_none_when_not_scored_yet() -> None:
    """While the evaluation is not finished, `result` is `null`.

    The database holds `{}` or `""`, and the exit point normalizes it to `null` —
    so the worker's cross-check returns False, and that is the **correct** result:
    nothing really was persisted.
    ⚠️ Conversely, defaults **must not** be added to `success` / `total_score` so
    that `{}` also parses successfully; that would be conjuring a
    `total_score=0.0` out of nowhere.
    """
    assert _submission_record().result is None
    with pytest.raises(ValidationError):
        s.ScoreSubmission.model_validate({})


def test_read_path_tolerates_historical_dirty_scores() -> None:
    """The read path carries **no range constraint** — the production database
    really does contain rows like `score=99.0`.

    That is how the 2026-08-14 one got in. Adding `le=1.0` to the read model
    would turn "reading one historical dirty row" into a 500, and a 5xx is the
    "repeat the POST" button for the worker. The range check stays on
    `check_env_scores()` on the write path: one guard, standing at the boundary.
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
    # The same data going through the write path must be rejected.
    with pytest.raises(s.ContractError):
        s.check_env_scores([{"env_name": "e", "score": 99.0, "samples": -5}])


def test_worker_status_words_are_not_what_the_backend_writes() -> None:
    """🔴 Pins down the **confirmed silent failure**: the two vocabularies do not
    match today.

    The worker's persistence cross-check only accepts `done` / `scored` /
    `failed`, while the backend writes `evaluated` / `eval_failed`, and since
    0002 `done` has been forbidden by a CHECK constraint. It follows that the
    cross-check is **always False** → every timeout or 5xx on POST /score goes
    through the full retry path, and there is no alert anywhere along that chain.

    This test **does not claim which side is right**, it claims "this is really
    the case today" — once spec 07 §10 Q2 is settled, what changes is this test
    and the wiring of `worker_status_alias`.
    """
    assert s.WORKER_ACCEPTED_STATUSES & ALL_STATUSES == frozenset()


def test_worker_status_alias_is_defined_but_deliberately_unwired() -> None:
    """The conversion function is in place, carries a TODO, but **no model calls
    it**.

    Keeping it out in the open instead of quietly wiring it into a query: the
    lesson of ZCY-158 is that the translation table was hidden inside the
    consumer (the worker's `_PROGRESS_STAGE_MAP` is still there today), so nobody
    knew the two sides did not actually match.
    """
    assert s.worker_status_alias("evaluated") == "done"
    assert s.worker_status_alias("eval_failed") == "failed"
    # A word outside the table is returned unchanged — legality is judged by
    # `ALL_STATUSES`, it is not this function's business.
    assert s.worker_status_alias("superseded") == "superseded"
    assert s.worker_status_alias("whatever") == "whatever"
    # Not wired up: the detail response still emits the canonical word straight
    # from the database.
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


# --- leaderboard / rounds ---


def test_leaderboard_row_carries_round_num() -> None:
    """`round_num` is the key production has and the new skeleton dropped —
    delete it and whoever reads it gets undefined."""
    assert _leaderboard_row().round_num == 1


def test_champion_score_shape_matches_leaderboard_rank1() -> None:
    """`champion.score` is a bare float and must be able to equal the
    `score.mean` of rank 1 on the leaderboard.

    Cross-endpoint consistency assertion 2 (spec 04 §5). Only the type shape is
    pinned down here — equality of the values has to be asserted in the backend's
    integration tests.
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
    """There is no rounds table, so `started_at` / `ends_at` cannot be made up →
    always `null`.

    **Do not fake them with local time.**
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
    """An empty database is `{"round": null}` plus 200.

    **Not** `{"error": "no rounds found"}` plus 200 (expressing failure with a
    200 has already forced the frontend to normalize at the boundary), **and not**
    a 404 either.
    """
    assert s.CurrentRoundResponse().model_dump() == {"round": None}


def test_score_std_is_none_not_zero_for_single_trial() -> None:
    """For a submission that has only been run once, `std` is `None`, **not 0** —
    0 would be read as "zero variance"."""
    assert s.ScoreStat(mean=0.5).std is None


# --- "no value" must not disguise itself as 0 / the empty string ---
#
# This group guards one and the same thing: `0` and `""` are both **legal
# values**, and using them as the sentinel value for "missing" means the consumer
# can never again tell "there is none" apart from "the value happens to be this".
# The consumers of these particular fields are miners and external auditors — the
# consequence of not being able to tell them apart is that the cross-check fails
# silently, and no party receives an error.


def _queue_status_task(**overrides: Any) -> s.QueueStatusTask:
    payload: dict[str, Any] = {
        "task_id": "task_abc_r1_v1",
        "hotkey": "5FQxZ",
        "uid": 7,
        "eval_status": STATUS_PENDING,
        "burn_status": "confirmed",
        "commit_block": 1234,
        "burn_block": 1230,
        "hf_repo_id": "x/y",
        "hf_commit": "a" * 40,
        "round_num": 1,
    }
    payload.update(overrides)
    return s.QueueStatusTask.model_validate(payload)


def _history_item(**overrides: Any) -> s.SubmissionHistoryItem:
    payload: dict[str, Any] = {
        "id": 1,
        "task_id": "task_abc_r1_v1",
        "uid": 7,
        "hotkey": "5FQxZ",
        "round_num": 1,
        "hf_repo_id": "x/y",
        "hf_commit": "a" * 40,
        "commit_block": 1234,
        "commit_block_timestamp": 1755000000,
        "burn_tx_hash": "0x" + "b" * 64,
        "burn_block": 1230,
        "burn_status": "confirmed",
        "block_hash": "0x" + "c" * 64,
        "eval_status": "evaluated",
    }
    payload.update(overrides)
    return s.SubmissionHistoryItem.model_validate(payload)


def test_seed_triple_is_null_when_no_seed_was_ever_assigned() -> None:
    """No seed was ever dispatched → `seed` / `drand_random` / `drand_round` are
    **all three `null` together**.

    `drand_round=0` is the sharpest of them: the official drand API returns
    **200** for `/public/0`, with the content of the latest round of the day (an
    alias of `latest`, measured 2026-08-19). An auditor querying it does not get
    a 404, they get today's beacon, and then `verify_seed()` is necessarily False
    — and they have no way to tell whether we cheated or the data is missing.
    This package's `seed.drand_round_url()` already raises for `<= 0`, so schemas
    emitting a 0 would mean one and the same package contradicting itself.

    `seed=0` is subtler: 0 is a legal output of `derive_seed()` (1/2³²), so it
    cannot be ruled out by guessing. In the 2026-08-19 production copy all 20
    rows with `seed=0` are "no seed was ever dispatched", and 11 of them have
    already been scored and appeared on the leaderboard — they are not
    reproducible, and nothing in the response shows it.

    All three must go together: changing only `drand_round` would emit a response
    where one field says there is none while the one next to it says there is.
    """
    item = _history_item()
    assert item.seed is None
    assert item.drand_random is None
    assert item.drand_round is None
    assert s.EvalEnvironment().seed is None
    # 0 is still a representable **real value** — which is exactly why it cannot
    # double as a sentinel value, and why `seed` cannot get a `gt=0` the way
    # `drand_round` does (that would reject a real seed).
    # But it must **bring its companions along**: the triple is the only way to
    # guard `seed`.
    assert _history_item(seed=0, drand_random="ab", drand_round=1).seed == 0


def test_seed_alone_is_rejected_because_zero_cannot_be_told_apart() -> None:
    """Giving `seed` without its companions → **rejected**. This is the only
    place where `seed=0` can be stopped.

    `drand_round` can carry `gt=0` and `drand_random` can carry `min_length=1`,
    while `seed` can carry neither (0 is a legal output of `derive_seed()`). So
    what it relies on is **consistency**: the inputs of `derive_seed` are
    block_hash + round + drand_random, so the time a seed really was dispatched
    all three fields necessarily have values at once.

    What this stops is exactly the shape of those 20 rows in production:
    `{seed: 0, drand_random: null, drand_round: null}`. Without this check they
    would serialize out quietly, and an auditor querying drand with
    `drand_round: 0` would get the latest round of the day (`/public/0` is an
    alias of `latest`).
    """
    for kwargs in (
        {"seed": 0},
        {"seed": 5, "drand_round": 7},
        {"drand_random": "ab"},
    ):
        with pytest.raises(ValidationError, match="incomplete seed triple"):
            _history_item(**kwargs)


def test_revision_is_null_when_the_commit_is_unknown() -> None:
    """`hf_commit` cannot be found → `revision` is `null`, **not `""`**.

    The only source of `revision` is the 40-character commit SHA (the CLI's
    `preflight.py` enforces this before going on chain), so `""` has never been a
    legal value. And in HF's URL semantics `""` means "the default branch" —
    `.../tree/{revision}` silently lands on main, so what the auditor checks is
    not the weights of the run that was scored.
    """
    assert s.ModelRef(name="pi0.5", hf_repo="x/y").revision is None
    assert (
        s.Baseline(
            model_name="pi0.5", hf_repo="x/y", score=s.ScoreStat(mean=0.5)
        ).revision
        is None
    )


def test_queue_task_round_num_cannot_be_omitted() -> None:
    """The `round_num` of a queue row is **required** — "which round is unknown"
    is not a legal state.

    This one is the opposite of the previous few: it should not have a `None`
    default, it should have no default at all. There is no round 0, and `0` would
    be taken as a real round number by the frontend and by a miner's curl and
    used as a filter, silently fetching back an empty list. The production column
    is `NOT NULL`, the backend always fills it, and 0 of the 119 rows are 0.
    """
    assert _queue_status_task().round_num == 1
    with pytest.raises(ValidationError):
        _queue_status_task(round_num=None)
    assert s.QueueStatusTask.model_fields["round_num"].is_required()


# --- probes ---


def test_liveness_status_is_a_constant() -> None:
    """The `status` of `/healthz` is always `"ok"`; any other value is
    unrepresentable."""
    assert s.LivenessResponse(round=1, netuid=80).status == "ok"
    with pytest.raises(ValidationError):
        s.LivenessResponse(round=1, netuid=80, status="degraded")  # type: ignore[arg-type]


def test_readiness_body_shape_is_identical_for_200_and_503() -> None:
    """Ready and not ready have **the same shape**; a caller should not have to
    prepare a second parser for the failure case."""
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


# --- structural constraints ---


def test_models_are_frozen() -> None:
    """Once a response model is built it must not be modified.

    The historical problem of this repository is precisely fields being patched
    by hand at the exit point (the `row.pop("status")` kind).
    """
    with pytest.raises(ValidationError):
        _leaderboard_row().rank = 2


def test_weights_are_fractions_not_u16_integers() -> None:
    """The values of `/api/weights` are **floats in 0~1**, not u16 integers.

    The new skeleton's `legacy.py:244` writes `dict[str, int]` — the moment real
    data is wired in, Pydantic trying to satisfy `int` with `0.9` gives a 500, an
    external validator's `fetch_weights()` swallows the exception into `{}` → no
    `set_weights` can be sent → **emissions across the whole network come to a
    halt, with only a single warning line**. The u16 normalization happens in the
    caller (`validator.normalize_weights`).
    """
    weights: s.Weights = {"5HTwty": 0.9, "5FQxZ": 0.07, "5DoaV8": 0.02, "5Gpih1": 0.01}
    assert all(isinstance(v, float) for v in weights.values())
    assert sum(weights.values()) == pytest.approx(1.0)
    assert len(weights) <= 4


def test_leaderboard_generated_at_is_the_only_moving_field() -> None:
    """Invariant 4: two requests over the same data are field-by-field equal
    except for `generated_at`.

    What is pinned down here is the **shape** (only this one field carries a
    server timestamp); the real idempotency assertion lives in the backend.
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
    """The miner-facing modules **must not** drag pydantic in.

    This is the entire point of putting pydantic into the `[schemas]`
    optional-dependency: a miner installs this package only to derive seeds and
    decode commitments, and should not have to compile a pydantic-core wheel on a
    GPU machine. The promise holds because "`__init__.py` re-exports nothing" —
    this test pins that down.

    A subprocess is mandatory: the top of this file already does
    `from openroboto_protocol import schemas`, so pydantic has long been in
    `sys.modules` of the current interpreter, and asserting in-process would be
    fooling ourselves.
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
    assert result.stdout.strip() == "[]", f"pydantic got pulled in: {result.stdout}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Response envelopes (ADR 02)
# ─────────────────────────────────────────────────────────────────────────────
#
# What this group pins down are the four rules of **the envelope itself**, not
# the fields of some endpoint. Three of them are already unrepresentable at the
# type level (see that comment in `schemas.py`); what happens here is the
# regression guard: it goes red when someone adds `Meta.page` back, gives
# `retryable` a default, or wraps the probes in an envelope while they are at it.


def _meta() -> s.Meta:
    return s.Meta(request_id="a3f81dbf1c2e")


def _page_meta() -> s.PageMeta:
    return s.PageMeta(total=7, limit=50, offset=0, has_more=False)


def _miner() -> s.MinerRef:
    return s.MinerRef(hotkey="5FQxZBhriyAvXXXX", display_name="5FQxZBhriyAv")


def test_success_response_always_has_data_and_never_error() -> None:
    """A success response **always** has `data` and **never** has `error`.

    `code: 0` meaning success is a convention you cannot see without reading the
    documentation; one-of `data` / `error` is a **structural** distinction. Here
    even the state "both present" cannot be constructed — `Envelope` simply has
    no `error` field, and forcing one in gets dropped by `Contract`'s
    `extra=ignore`.
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
    """An error response is the other way round. `retryable` has **no default**;
    it has to be thought about explicitly, once.

    Defaulting to `False` means choosing the "not retryable" side on behalf of
    everyone who forgot to think about it — and the price of that side is the
    worker treating one flaky chain RPC call as a permanent failure, destroying
    an 8-hour evaluation result, with no refund of the TAO the miner burned.
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
    """`fields` lives **only on the 422 subclass**; on an ordinary error the key
    does not even appear.

    This is the landed shape of ADR 02 §8 open question ② (2026-08-18). It goes
    through a subclass rather than `fields: … | None = None`: an optional field
    relies on every exit point remembering `exclude_none`, and missing one emits
    an extra `"fields": null`, and forgetting once is silent — the same reason
    `meta.page` goes through the `ListMeta` subclass.

    This also pins down that **the base class was not modified along the way**:
    `ValidationErrorBody` inherits from `ErrorBody`, and the key-set assertion
    below is the inherited expansion, so one missing field on the base class goes
    red right here.
    """
    plain = s.ErrorEnvelope(
        error=s.ErrorBody(code="NOT_FOUND", message="no", retryable=False),
        meta=_meta(),
    ).model_dump(mode="json")
    assert "fields" not in plain["error"]

    invalid = s.ValidationErrorEnvelope(
        error=s.ValidationErrorBody(
            code="VALIDATION_ERROR",
            message="request body validation failed (1 field)",
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

    # `fields` is required — a 422 that forgot to carry it cannot be
    # constructed, and "a 422 with no per-field information" is the entire
    # reason this subclass exists.
    with pytest.raises(ValidationError):
        s.ValidationErrorBody(  # type: ignore[call-arg]
            code="VALIDATION_ERROR", message="x", retryable=False
        )


def test_request_id_is_on_every_response_including_errors() -> None:
    """`meta.request_id` always exists. **Especially on error responses** — that
    is precisely the one that has to be looked up.

    It is a required field, and without it the whole response cannot be
    constructed. Giving it a default means allowing "the response that cannot be
    looked up" to exist, while it is the only thing a user can paste back when
    something goes wrong.
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
    """`meta.page` exists **only on list endpoints**; on a single-object endpoint
    the key does not even appear.

    Not by way of `exclude_none` (that requires every route to remember to write
    `response_model_exclude_none=True`, and missing one emits `"page": null`, and
    silently at that), but because the declared type of `Envelope.meta`, `Meta`,
    **simply does not have this field**. The second part below proves it: even
    forcing a `ListMeta` into a single-object envelope, serialization still
    follows the declared type.
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
    """The shape of `app/repositories/pagination.py:Page` (a dataclass, items is a
    tuple)."""

    items: tuple[int, ...]
    total: int
    limit: int
    offset: int


class _ApiPage(BaseModel):
    """The shape of `app/api/schemas.py:Page` (a pydantic model, items is a
    list)."""

    items: list[int]
    total: int
    limit: int
    offset: int


@pytest.mark.parametrize(
    ("count", "total", "offset", "expected"),
    [
        (50, 137, 0, True),  # first page, more to come
        (37, 137, 100, False),  # last page, exactly exhausted
        (0, 0, 0, False),  # an empty list is not "there is a next page"
        (50, 50, 0, False),  # one page holds everything
    ],
)
def test_has_more_is_computed_in_exactly_one_place(
    count: int, total: int, offset: int, expected: bool
) -> None:
    """`has_more` is computed by `PageMeta.of()`; callers must not assemble the
    expression themselves.

    Copying `offset + len(items) < total` into 8 list endpoints means that
    getting one of them wrong silently drops rows while paging — and dropping
    rows while paging raises an error for no party at all; the miner just turns
    up asking "my submission is not on the dashboard" (the original shape of
    ZCY-162).

    Both kinds of `Page` have to be feedable directly: the repository-layer one
    is a dataclass and the API-layer one is a pydantic model; `PageLike` is a
    structural type, so neither of them has to change.
    """
    items = tuple(range(count))
    repo = _RepoPage(items=items, total=total, limit=50, offset=offset)
    api = _ApiPage(items=list(items), total=total, limit=50, offset=offset)
    assert s.PageMeta.of(repo) == s.PageMeta.of(api)
    assert s.PageMeta.of(repo).has_more is expected


def test_probes_are_never_enveloped() -> None:
    """The probes are **the only exception**: `/healthz` and `/readyz` are not
    wrapped in an envelope, and the same goes for `/metrics`.

    Their consumers are PM2 / the load balancer / Prometheus, and with an
    envelope they cannot parse it at all — the consequence of an unparseable
    health check is **traffic being pulled or the process being restarted over
    and over**, which is even more urgent than a wrong field. `/metrics` has no
    model in the protocol package (it is Prometheus text format as `text/plain`,
    see the backend's `app/core/metrics.py`), so this test can only pin down the
    two probes.
    """
    for probe in (s.LivenessResponse, s.ReadinessResponse):
        assert _serialized_keys(probe) & {"data", "meta", "error"} == set()
    assert _serialized_keys(s.LivenessResponse) == {"round", "netuid", "status"}


def test_envelope_generics_survive_into_openapi() -> None:
    """`response_model=Envelope[LeaderboardRow]` must generate a **concrete**
    OpenAPI schema.

    If the generic degrades into `data: object`, then in the types generated for
    the frontend and the worker `data` is `any` — which throws away the reason
    this package exists (the field contract living in the types).
    """
    single = s.Envelope[s.LeaderboardRow].model_json_schema(mode="serialization")
    assert single["title"] == "Envelope[LeaderboardRow]"
    assert single["properties"]["data"] == {"$ref": "#/$defs/LeaderboardRow"}
    assert "LeaderboardRow" in single["$defs"]

    listed = s.ListEnvelope[s.ScanRejection].model_json_schema(mode="serialization")
    assert listed["properties"]["data"]["type"] == "array"
    assert listed["properties"]["data"]["items"] == {"$ref": "#/$defs/ScanRejection"}


def test_envelopes_are_frozen_like_every_other_response() -> None:
    """An envelope is a response model too and must not be modified once built —
    patching fields by hand at the exit point is the historical problem of this
    repository."""
    with pytest.raises(ValidationError):
        _meta().request_id = "another"


# ─────────────────────────────────────────────────────────────────────────────
# 🔴 后端阶段 1 把哨兵值换成了 null —— 契约必须跟得上
# ─────────────────────────────────────────────────────────────────────────────

#: 一行真实响应的字段形状，取自 `api-dev.openroboto.ai` 2026-08-21 实测。
#: hotkey / 仓库名 / task_id 已替换，其余保持原样 —— **尤其是那些 `None`**。
_REAL_HISTORY_ROW = {
    "id": 1,
    "task_id": "<redacted>",
    "uid": 23,
    "hotkey": "<redacted>",
    "round_num": 1,
    "hf_repo_id": "<redacted>",
    "hf_commit": "ba782170658f3ea41d1950af49aa200877ec630f",
    "commit_block": 7830000,
    "commit_block_timestamp": 1787300000,
    "burn_tx_hash": (
        "0xa3586af1a559d2f9d3a31c27691f6ee77d88335bd7b51060e8cbf229c1183605"
    ),
    "burn_block": 7829990,
    "burn_status": "confirmed",
    "block_hash": (
        "0x71ef6fa31929e790a06b183c0163c03eb42069c8a72ad211b03baa5c1f134c03"
    ),
    "eval_status": "pending",
    "env_list": ["libero_spatial"],
    "model_hash": ("02e50f7d7d26d3298a500f2b9ccc3e0c8d1a9e6cceadf9ae545c4fcc1cee466a"),
    # 🔴 后端真的会发这些 null。此前契约把它们声明成 `str = ""`。
    "result": None,
    "detail": None,
    "reject_reason": None,
    "avg_score": None,
    "stage": None,
    "reason": None,
}


def test_a_real_response_row_parses() -> None:
    """🔴 **这条是拿真实响应喂出来的，不是手写的。**

    2026-08-21，CLI 的第一次真实端到端跑在最后一步炸了：

        2 validation errors for ListEnvelope[SubmissionHistoryItem]
        data.0.model_hash  Input should be a valid string, input_value=None
        data.0.stage       Input should be a valid string, input_value=None

    那时 burn 已经付过、模型已经传上 HF —— **代价是真金白银的那一步之后才发现
    契约对不上**。根因是后端阶段 1 把「没有值」的列全部换成了 SQL NULL，
    而契约这边三个字段还停在 `str = ""`。

    手写的用例挡不住这类：写的人按契约构造输入，于是永远自洽。所以这一行
    直接取自实测响应，**尤其保留了那些 `None`**。
    """
    item = s.SubmissionHistoryItem.model_validate(_REAL_HISTORY_ROW)

    assert item.model_hash is not None
    assert item.stage is None
    assert item.reject_reason is None


def test_the_fields_phase_one_made_nullable_are_nullable() -> None:
    """逐个钉住，别再漏。

    `model_hash` / `stage` / `reject_reason` 三个是同一次改造的产物；
    漏掉任何一个的表现都一样：矿工烧完钱，最后一步解析失败。
    """
    for field in ("model_hash", "stage", "reject_reason"):
        annotation = s.SubmissionHistoryItem.model_fields[field].annotation
        assert "None" in str(annotation), (
            f"{field} 声明成 {annotation} —— 后端会发 null，"
            f"而这条路径上矿工已经付过 burn"
        )
