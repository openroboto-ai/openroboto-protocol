"""Contract tests for status.py.

Every assertion here comes either from a live measurement or from a path in
production code with a traceable source. Comments that say "live" are read-only
measurements taken against https://api.openroboto.ai on 2026-08-17.
"""

from __future__ import annotations

import dataclasses

import pytest

from openroboto_protocol import status as S

# ── The vocabulary itself ─────────────────────────────────────────────────


def test_all_statuses_is_the_transition_table_keys() -> None:
    """The full status set and the transition table share one source — it is
    impossible to have "in the table but not in the vocabulary"."""
    assert S.ALL_STATUSES == set(S.STATUS_TRANSITIONS)


def test_transition_targets_are_all_known_statuses() -> None:
    """The transition table must not point at a status outside the vocabulary (a
    single mistyped letter shows up right here)."""
    for src, targets in S.STATUS_TRANSITIONS.items():
        assert targets <= S.ALL_STATUSES, f"{src} points at an unknown status"


def test_production_observed_statuses_are_all_legal() -> None:
    """The eval_status values that have appeared in live
    `GET /api/v1/submissions/history?limit=500` are 4 in total
    (evaluated 65 / superseded 32 / eval_failed 13 / rejected 7), and the summary
    of `GET /api/v1/queue/status` additionally has pending / evaluating.
    Missing any one of them from the vocabulary would turn real live data into an
    "illegal status".
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
    """The ALL_STATUSES of the old `backend/protocol/status.py` was missing it,
    while there are 32 rows of it in production."""
    assert S.STATUS_SUPERSEDED in S.ALL_STATUSES
    assert S.is_terminal(S.STATUS_SUPERSEDED)


def test_transition_table_is_read_only() -> None:
    """A shared contract must not be patched in place by a consumer."""
    with pytest.raises(TypeError):
        S.STATUS_TRANSITIONS["pending"] = frozenset()  # type: ignore[index]


# ── Terminal / frozen states ──────────────────────────────────────────────


@pytest.mark.parametrize("st", ["evaluated", "eval_failed", "rejected", "superseded"])
def test_terminal_states_have_no_outgoing_edges(st: str) -> None:
    """spec invariant 7: one-way, no going back."""
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
    """The seed_failed produced when drand cannot be fetched is retried back to
    pending by the next chain-scanning round."""
    assert not S.is_terminal(S.STATUS_SEED_FAILED)
    assert S.can_transition(S.STATUS_SEED_FAILED, S.STATUS_PENDING)


def test_frozen_is_a_strict_subset_of_terminal() -> None:
    """The frozen states (the DB refuses any write) are a subset of the terminal
    states, not the same thing."""
    assert S.FROZEN_STATUSES < S.TERMINAL_STATUSES
    assert S.FROZEN_STATUSES == {"rejected", "superseded"}


# ── The state machine ─────────────────────────────────────────────────────


def test_spec_invariant_7_happy_path() -> None:
    """pending → evaluating → evaluated / eval_failed / rejected."""
    assert S.can_transition(S.STATUS_PENDING, S.STATUS_EVALUATING)
    for terminal in (S.STATUS_EVALUATED, S.STATUS_EVAL_FAILED, S.STATUS_REJECTED):
        assert S.can_transition(S.STATUS_EVALUATING, terminal)


def test_state_machine_never_goes_backwards() -> None:
    """Going backwards is illegal without exception — this is the core of
    incident ⑤ on 2026-08-14."""
    assert not S.can_transition(S.STATUS_EVALUATING, S.STATUS_PENDING)
    assert not S.can_transition(S.STATUS_EVALUATED, S.STATUS_EVALUATING)
    assert not S.can_transition(S.STATUS_PENDING, S.STATUS_RECEIVED)


def test_superseded_cannot_be_revived_by_a_late_score() -> None:
    """Live evidence: id=79 of uid 175 had already been superseded while the
    worker was still running, and once it finished scoring it wanted to write it
    as evaluated. Reviving it would hit the idx_sub_hotkey_round_commit unique
    constraint → the scoring endpoint 500s and the worker's hours of GPU time are
    wasted; and even if it did not hit the constraint, a superseded version would
    have re-entered the ranking.
    """
    for late in (S.STATUS_EVALUATED, S.STATUS_EVAL_FAILED, S.STATUS_EVALUATING):
        assert not S.can_transition(S.STATUS_SUPERSEDED, late)
        assert not S.can_transition(S.STATUS_REJECTED, late)


def test_pending_to_terminal_directly_is_legal() -> None:
    """A real historical path: before the fix, update_task_progress only wrote the
    stage and never advanced the status, so there are 0 rows of evaluating in the
    whole database and all 65 live evaluated rows landed directly from pending.
    Judging that illegal would be declaring live history illegal.
    """
    assert S.can_transition(S.STATUS_PENDING, S.STATUS_EVALUATED)
    assert S.can_transition(S.STATUS_PENDING, S.STATUS_EVAL_FAILED)


def test_supersede_only_from_pending() -> None:
    """The WHERE of `supersede_pending` has exactly one clause:
    eval_status = 'pending'."""
    assert S.can_transition(S.STATUS_PENDING, S.STATUS_SUPERSEDED)
    assert not S.can_transition(S.STATUS_EVALUATING, S.STATUS_SUPERSEDED)


def test_reject_is_reachable_from_every_non_terminal_state() -> None:
    """Any step on the chain-scanning side may end in rejection (burn not valid /
    HF structure / duplicate / round mismatch)."""
    for src in S.ALL_STATUSES - S.TERMINAL_STATUSES:
        assert S.can_transition(src, S.STATUS_REJECTED), src


def test_idempotent_rewrite_is_allowed_except_when_frozen() -> None:
    """When the worker's scoring POST times out it resubmits the same score, and
    persisting it is an evaluated → evaluated transition. But the frozen states
    must block even a same-value rewrite (that is exactly how the production SQL
    predicate is written).
    """
    assert S.can_transition(S.STATUS_EVALUATED, S.STATUS_EVALUATED)
    assert S.can_transition(S.STATUS_PENDING, S.STATUS_PENDING)
    assert not S.can_transition(S.STATUS_SUPERSEDED, S.STATUS_SUPERSEDED)
    assert not S.can_transition(S.STATUS_REJECTED, S.STATUS_REJECTED)


def test_unknown_status_is_rejected_fail_closed() -> None:
    """An unknown word is always False; no "treat it as whatever it looks like"
    guessing."""
    assert not S.can_transition("banana", S.STATUS_PENDING)
    assert not S.can_transition(S.STATUS_PENDING, "banana")
    assert not S.can_transition("", "")
    # The spellings from the old vocabulary are not legal statuses either — run
    # them through normalize_status first, then judge.
    assert not S.can_transition("done", "failed")


def test_scan_phase_chain() -> None:
    assert S.can_transition(S.STATUS_RECEIVED, S.STATUS_BURN_CHECKING)
    assert S.can_transition(S.STATUS_BURN_CHECKING, S.STATUS_BURN_PASSED)
    assert S.can_transition(S.STATUS_BURN_PASSED, S.STATUS_PENDING)
    assert S.can_transition(S.STATUS_BURN_PASSED, S.STATUS_SEED_FAILED)
    # no seed should be handed out before the burn check has passed
    assert not S.can_transition(S.STATUS_RECEIVED, S.STATUS_PENDING)


# ── The stage vocabulary ──────────────────────────────────────────────────


def test_wire_stage_vocabulary_matches_production() -> None:
    """The vocabulary = the four that production **accepts**, not the three that
    production has **stored**.

    `claimed` was added on 2026-08-19. The two pieces of evidence point in
    opposite directions, and the ruling was made on "what the code accepts":

    - What has been stored: in the 08-18 production copy `stage` only holds
      running 38 / benchmark_running 24 / "" 47 / downloading 8 /
      benchmark_prechecking 2, and `claimed` occurs **0 times in all four
      columns** `stage`, `status`, `eval_status` and
      `submission_history.eval_status`.
    - What is accepted: the allowlist of production
      `backend/api/handlers/benchmark.py::handle_status_update` is
      `{benchmark_downloading, benchmark_prechecking, benchmark_running,
      benchmark_claimed}`, and **anything not in it is `INVALID_STATUS`**.

    The latter wins. The consequence of leaving this one out is not "one useless
    extra word", it is that when a worker reports `claimed` we judge it an
    unknown word — while production would accept it. Two sides giving different
    answers for the same input is exactly what this package exists to eliminate.

    **evaluating has never appeared** — the canonical outward word is running,
    and this is the basis for settling the four-party vocabulary dispute.
    """
    assert S.ALL_STAGES == {"downloading", "prechecking", "running", "claimed"}
    assert "evaluating" not in S.ALL_STAGES


def test_worker_internal_word_maps_to_the_wire_word() -> None:
    """Internally the worker calls it evaluating (that is what run_eval.py writes
    into the progress file), and outwards it must be running. This one line
    replaces `_PROGRESS_STAGE_MAP`.
    """
    assert S.normalize_stage("evaluating") == S.STAGE_RUNNING
    assert S.normalize_stage("running") == S.STAGE_RUNNING


def test_frontend_and_legacy_words_are_accepted() -> None:
    """Callers written against the public documentation or the frontend types
    have received a 400 because of this (it actually happened on
    2026-08-14)."""
    assert S.normalize_stage("precheck") == S.STAGE_PRECHECKING
    assert S.normalize_stage("benchmark_running") == S.STAGE_RUNNING
    assert S.normalize_stage("benchmark_downloading") == S.STAGE_DOWNLOADING
    assert S.normalize_stage("benchmark_prechecking") == S.STAGE_PRECHECKING


def test_normalize_stage_strips_and_lowercases() -> None:
    """What the production entry point does is exactly `.strip().lower()`."""
    assert S.normalize_stage("  RUNNING \n") == S.STAGE_RUNNING


def test_normalize_stage_rejects_unknown() -> None:
    """`scoring` exists only in the frontend vocabulary, no backend path produces
    it; the empty string means "no stage".

    ⚠️ `claimed` used to be in this test (asserting that it was rejected). It was
    moved out on 2026-08-19: the production `handle_status_update` allowlist
    accepts `benchmark_claimed`, so judging it illegal would mean giving the
    opposite answer to production for the same input. Its assertion now lives in
    `test_wire_stage_vocabulary_matches_production`.
    """
    assert S.normalize_stage("scoring") is None
    assert S.normalize_stage("") is None
    assert S.normalize_stage("claimed") == S.STAGE_CLAIMED


def test_stage_records_are_frozen() -> None:
    """The three names are bound into one record and cannot be changed — a
    mismatch is impossible at the type level."""
    stage = S.STAGES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        stage.wire = "nope"  # type: ignore[misc]


def test_stage_stored_form_is_the_prefixed_one() -> None:
    """The database stores the benchmark_ prefix and the exit point must
    translate it; without the translation the frontend renders no progress
    bar."""
    assert [s.stored for s in S.STAGES] == [
        "benchmark_claimed",
        "benchmark_downloading",
        "benchmark_prechecking",
        "benchmark_running",
    ]


def test_stage_order_is_the_worker_execution_order() -> None:
    # `claimed` (task taken, download not started yet) comes first — the order is
    # the worker's actual execution order.
    assert [s.wire for s in S.STAGES] == [
        "claimed",
        "downloading",
        "prechecking",
        "running",
    ]


# ── Legacy status words ───────────────────────────────────────────────────


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
    """These old words are still alive in production data today (2026-08-19 copy:
    done 37 / failed 4 / confirmed 1 / enqueued 17), and the normalisation must
    happen in exactly one place."""
    assert S.normalize_status(legacy) == unified
    assert unified in S.ALL_STATUSES


def test_normalize_status_passes_unknown_through() -> None:
    """Consistent with the old implementation: anything not in the table is
    returned unchanged, and legality is left to ALL_STATUSES to judge."""
    assert S.normalize_status("evaluated") == "evaluated"
    assert S.normalize_status("banana") == "banana"


def test_legacy_alias_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        S.LEGACY_STATUS_ALIASES["done"] = "banana"  # type: ignore[index]
