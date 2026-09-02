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


def test_the_vocabulary_is_larger_than_what_a_column_accepts() -> None:
    """Ten words are legal to *read*, eight are legal to *write* — and the two
    numbers are pinned here because prose that carried them got them wrong (this
    module's docstring said "eight" for both while `ALL_STATUSES` held ten, and
    nothing was watching).

    The gap is the whole reason the two names exist: a consumer that validates a
    to-be-written status against `ALL_STATUSES` passes `burn_checking`, and the
    INSERT then violates `ck_submissions_status` — a 500 after the request was
    accepted, not a rejection the caller can act on.
    """
    assert len(S.ALL_STATUSES) == 10
    assert len(S.STORABLE_STATUSES) == 8
    assert S.STORABLE_STATUSES < S.ALL_STATUSES


def test_storable_statuses_are_the_check_constraint_word_for_word() -> None:
    """The whitelist of `ck_submissions_status` in the backend's
    `0001_target_schema.sql`, copied verbatim. When the backend deletes its own
    `SUBMISSION_STATUSES` and imports this set, this test is what the deletion
    rests on.
    """
    assert S.STORABLE_STATUSES == {
        "received",
        "pending",
        "evaluating",
        "evaluated",
        "eval_failed",
        "rejected",
        "seed_failed",
        "superseded",
    }


def test_the_two_transient_statuses_are_the_only_difference() -> None:
    """`burn_checking` / `burn_passed` name the two steps of the burn check, and
    the backend records that check in a separate `burn_status` column — so the
    lifecycle column goes `received` → `pending` / `seed_failed` and never holds
    either word.

    They stay in the vocabulary because the scanner really does pass through them
    (`verify_submission.py:560` / `:569`) and the public API reference documents
    both: deleting them would make `can_transition` call the scanner's own steps
    illegal. Legal to read, never legal to write — which is exactly what having
    two sets says.
    """
    assert S.TRANSIENT_STATUSES == {"burn_checking", "burn_passed"}
    assert S.TRANSIENT_STATUSES & S.STORABLE_STATUSES == set()
    # Real steps, not orphan words: both sit on the path out of `received`.
    assert S.can_transition(S.STATUS_RECEIVED, S.STATUS_BURN_CHECKING)
    assert S.can_transition(S.STATUS_BURN_CHECKING, S.STATUS_BURN_PASSED)


def test_every_terminal_status_can_be_stored() -> None:
    """Where a submission comes to rest is where it stays in the table. A terminal
    word that no column accepts would leave finished rows with nothing to be."""
    assert S.TERMINAL_STATUSES <= S.STORABLE_STATUSES


def test_normalizing_an_old_word_yields_a_storable_one() -> None:
    """Reading old data is only half of it — the normalized word gets written back.
    Every target of the alias table must therefore be storable, not merely legal."""
    for old, new in S.LEGACY_STATUS_ALIASES.items():
        assert new in S.STORABLE_STATUSES, old


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
    pending by the next chain-scanning pass."""
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
    HF structure / duplicate / wrong season)."""
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
    assert S.ALL_STAGES == {
        "downloading",
        "prechecking",
        "running",
        "claimed",
        # 2026-09-01. Not observed in production storage either — they are added
        # for the same reason `claimed` was: the entry point must accept what the
        # worker is about to send, or the first report is a 400 and the retry that
        # follows is silent.
        "queued",
        "stalled",
    }
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
        # 🔴 **No prefix on these two, on purpose.** `benchmark_` marks "the
        # benchmark is doing something with this row"; both of these mean the
        # opposite — the worker has let go. Prefixing them would make
        # `stage.startswith("benchmark_")` — a shape that reads naturally and
        # will get written — mean the reverse of what it says.
        "queued",
        "stalled",
    ]


def test_stage_order_is_the_worker_execution_order() -> None:
    # `claimed` (task taken, download not started yet) comes first — the order is
    # the worker's actual execution order.
    #
    # ⚠️ Only the first four are a progression. `queued` and `stalled` are exits
    # reachable from any of them, so they sit after the sequence rather than in
    # it; reading the tuple as "these six happen in order" would be wrong.
    assert [s.wire for s in S.STAGES] == [
        "claimed",
        "downloading",
        "prechecking",
        "running",
        "queued",
        "stalled",
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


# ─────────────────────────────────────────────────────────────────────────────
# Real-track vocabularies
# ─────────────────────────────────────────────────────────────────────────────


def test_season_status_values_are_pinned() -> None:
    """Word for word, in the order a season passes through them.

    `awaiting_confirmation` is not in the PRD's five words. It is the cooling
    period plus the manual gate — the settlement record is written and no α has
    moved — and it is here because `openroboto-backend` needs the state and the
    only alternative is to overload a word that means something else.

    The word is `paying`, **not** `paying_out`: `paying_out` and `paid_out` share
    the `_out` suffix and differ in the middle, and they mean "money is leaving
    the wallet daily" versus "the season is closed". Two states that must never be
    mistaken for each other do not get near-identical spellings.
    """
    assert S.SEASON_STATUS_VALUES == (
        "awaiting_eval",
        "awaiting_settlement",
        "awaiting_confirmation",
        "paying",
        "paid_out",
        "burned",
    )
    assert S.SEASON_STATUS_VALUES == tuple(s.value for s in S.SeasonStatus)
    assert "paying_out" not in S.SEASON_STATUS_VALUES


def test_invalid_reason_values_are_pinned() -> None:
    """Exactly the word list of `ck_submissions_invalid_reason` in the backend's
    0004 migration. The `hf_` prefix is part of the code: the next family of
    reasons (payment amount, wrong coldkey, wrong payer) must not collide with a
    bare `unauthorized`."""
    assert S.INVALID_REASON_VALUES == (
        "hf_forbidden",
        "hf_access_revoked",
        "hf_repo_not_found",
        "hf_revision_not_found",
        "hf_files_incomplete",
    )
    assert S.INVALID_REASON_VALUES == tuple(r.value for r in S.InvalidReason)


def test_the_real_track_adds_no_submission_status() -> None:
    """🔴 Real-track submissions live in the same `submissions` table, keyed by
    `competition_id`, so they use the same lifecycle words as everything else.
    Two vocabularies on one column is the 2026-08-14 incident itself.

    `registered` and `invalid` were considered and dropped: they map onto
    `received` and `rejected` (plus an `InvalidReason`).
    """
    new_words = set(S.SEASON_STATUS_VALUES) | set(S.INVALID_REASON_VALUES)
    assert new_words & S.ALL_STATUSES == set()
    assert new_words & S.ALL_STAGES == set()
    assert {"registered", "invalid"} & new_words == set()


def test_the_two_real_track_vocabularies_do_not_overlap() -> None:
    """A season word and a disqualification word are answers to different
    questions; one string that could be either is a bug waiting to happen."""
    assert set(S.SEASON_STATUS_VALUES) & set(S.INVALID_REASON_VALUES) == set()


def test_no_appeal_vocabulary_exists() -> None:
    """⛔ There is no appeals process (spec 10 §2.5). A word for it in the
    protocol package would be an invitation to implement one."""
    assert {"disputed", "appealed"} & set(S.SEASON_STATUS_VALUES) == set()
    assert {"disputed", "appealed"} & set(S.INVALID_REASON_VALUES) == set()


def test_episode_failure_codes_are_not_status_words() -> None:
    """`hw_failed` / `model_error` classify **one episode**, not a submission —
    they decide who pays for a re-run, and they live in `schemas.EpisodeFailure`.
    Putting them here would make "one episode's arm jammed" and "this entry is
    finished" the same kind of thing."""
    assert {"hw_failed", "model_error"} & set(S.SEASON_STATUS_VALUES) == set()
    assert {"hw_failed", "model_error"} & set(S.INVALID_REASON_VALUES) == set()
    assert {"hw_failed", "model_error"} & S.ALL_STATUSES == set()


def test_module_exports_are_pinned() -> None:
    """`__all__` is the public surface the version number promises."""
    assert S.__all__ == [
        "ALL_STAGES",
        "ALL_STATUSES",
        "FROZEN_STATUSES",
        "INVALID_REASON_VALUES",
        "LEGACY_STATUS_ALIASES",
        "SEASON_STATUS_VALUES",
        "STAGES",
        "STAGE_CLAIMED",
        "STAGE_DOWNLOADING",
        "STAGE_PRECHECKING",
        "STAGE_RUNNING",
        "STATUS_BURN_CHECKING",
        "STATUS_BURN_PASSED",
        "STATUS_EVALUATED",
        "STATUS_EVALUATING",
        "STATUS_EVAL_FAILED",
        "STATUS_PENDING",
        "STATUS_RECEIVED",
        "STATUS_REJECTED",
        "STATUS_SEED_FAILED",
        "STATUS_SUPERSEDED",
        "STATUS_TRANSITIONS",
        "STORABLE_STATUSES",
        "TERMINAL_STATUSES",
        "TRANSIENT_STATUSES",
        "InvalidReason",
        "SeasonStatus",
        "Stage",
        "can_transition",
        "is_terminal",
        "normalize_stage",
        "normalize_status",
    ]
    assert all(hasattr(S, name) for name in S.__all__)


def test_the_two_exit_stages_are_not_verdicts() -> None:
    """🔴 `queued` and `stalled` say what the **worker** did, not what the model is.

    They were added on 2026-09-01 after two paid miners sat at `prechecking` for
    four hours while the worker crash-looped every two minutes. The tempting fix
    was "after N retries, mark it eval_failed" — and that is wrong, because N
    failures on one machine is evidence about *that machine*. Infrastructure
    crashing is our fault and the fee is already burned; a verdict there spends
    someone's TAO on a conclusion nobody reached.

    So neither word may leak into the status vocabulary, where the terminal
    decisions live. This test is what makes that separation cost something to
    undo.
    """
    assert S.STAGE_QUEUED not in S.ALL_STATUSES
    assert S.STAGE_STALLED not in S.ALL_STATUSES
    assert not S.is_terminal(S.STATUS_PENDING)


def test_a_stalled_task_is_still_pending() -> None:
    """A worker that gives up does not move the row out of the queue.

    `stalled` is a *display* fact — "nobody is working on this and nobody will
    until a human looks". The row stays `pending`, so any fix that starts
    dispatching again picks it up with no migration and no repair script.
    """
    assert S.STAGE_STALLED in S.ALL_STAGES
    assert S.STATUS_PENDING in S.ALL_STATUSES
