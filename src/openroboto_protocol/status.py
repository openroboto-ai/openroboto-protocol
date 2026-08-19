"""Task status and stage vocabularies — the only copy in the subnet.

**Why there must be only one**: in the 2026-08-14 incident the same thing had four
spellings — the worker internally called it `evaluating`, the public docs called it
`running`, the backend only recognized the `status` field, and the frontend types called
it `stage`. The result was that whoever wrote a worker against the docs got
`400 Unknown status`, and the queue page's progress bar disappeared entirely.
The "fix" back then was not to unify the vocabulary but to add a hand-written
translation table inside the worker (`_PROGRESS_STAGE_MAP` in
`benchmark_worker/backend_client.py`). This module is the real answer to that
translation table: there is only one vocabulary, and packages installing the same
version number cannot disagree.

**The two vocabularies are not the same thing, and mixing them is the incident itself**:

- **status**: which step the submission has reached. The lifecycle status; for the
  values see `ALL_STATUSES`.
- **stage**: what the worker is doing after claiming the task. The progress detail; for
  the values see `ALL_STAGES`, meaningful only while status is `evaluating`.

`evaluating` is a **status**; `running` is a **stage**. They describe the same span of
time, but they are not the same vocabulary, and either side written with the other
side's word is judged illegal by that other side.

⚠️ What is discussed here is the **vocabulary**, not the field name carrying it, and
certainly not the database column name. The response field carrying the lifecycle status
**differs per endpoint** — `SubmissionRecord` calls it `status`, the other four models
call it `eval_status`. Which endpoint is which is decided by `STATUS_VALUED_FIELDS` in
`schemas.py` (that table is checked entry by entry by `tests/test_schemas.py`, so it
cannot drift away from the code). **Do not keep a second copy here.**

**Live facts** (2026-08-17, curl `GET /api/v1/submissions/history?limit=500`, 117 rows):

- `eval_status` has appeared as: `evaluated` 65 / `superseded` 32 / `eval_failed` 13 /
  `rejected` 7; the summary of `GET /api/v1/queue/status` additionally has `pending` /
  `evaluating`.
- `stage` has appeared as: `running` 61 / `""` 47 / `downloading` 8 / `prechecking` 1.
  **`evaluating` has never appeared** — the canonical public stage word is `running`,
  and this is the basis of that ruling.

**What this module is not responsible for**: how the status is persisted (which table,
which column, which column is authoritative), who is allowed to change the status, how
timeouts are judged. Those are the backend's business — and moreover the authoritative
column is **the opposite** before and after the data migration (`eval_status` before,
`status` after), so writing it into this package would make an unrecoverable version
number promise something another repository can change at any time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

# ─────────────────────────────────────────────────────────────────────────────
# Submission status (lifecycle)
# ─────────────────────────────────────────────────────────────────────────────

# The chain scanner has just seen this commitment and has not validated anything yet.
# The default value at row creation (the DEFAULT in schema.py).
STATUS_RECEIVED: Final[str] = "received"

# Verifying the on-chain burn transaction. ⚠️ A transient value: it is left within one
# scanning cycle, and no row should sit here for long.
STATUS_BURN_CHECKING: Final[str] = "burn_checking"

# The burn check passed, waiting for a seed to be dispatched. Also a transient value.
STATUS_BURN_PASSED: Final[str] = "burn_passed"

# Queued for evaluation, waiting for a worker to claim it.
STATUS_PENDING: Final[str] = "pending"

# Seed dispatch failed (drand could not be reached). **Retryable**, not terminal — the
# chain scanner retries once at the end of every round, and on success it goes back to
# pending. While drand is unavailable it is better to be stuck than to degrade into
# deriving the seed from block_hash alone, otherwise historical evaluations are not
# reproducible (spec §5).
STATUS_SEED_FAILED: Final[str] = "seed_failed"

# A worker has claimed it and is running. Only during this time is the stage field
# meaningful.
STATUS_EVALUATING: Final[str] = "evaluating"

# Scored successfully; the scores go into eval_scores and take part in ranking.
STATUS_EVALUATED: Final[str] = "evaluated"

# Evaluation failed (the worker errored / the model would not run). Terminal, no retry.
STATUS_EVAL_FAILED: Final[str] = "eval_failed"

# Rejected (bad burn / bad HF repo structure / duplicate submission / round mismatch).
STATUS_REJECTED: Final[str] = "rejected"

# A new version exists for the same (hotkey, round), so this one was pushed out.
# ⚠️ The old `protocol/status.py`'s ALL_STATUSES **missed it**, and `is_terminal()`
# returned False for it — there were zero consumers at the time so nothing broke; it is
# added here (wrap-up item 7 of incident-20260814-context.md).
STATUS_SUPERSEDED: Final[str] = "superseded"


# Terminal states: they do not move forward any more.
# Note the difference from FROZEN_STATUSES — terminal says "the process has ended",
# frozen says "the DB rejects any write".
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {STATUS_EVALUATED, STATUS_EVAL_FAILED, STATUS_REJECTED, STATUS_SUPERSEDED}
)

# Frozen states: **any** late write must be rejected, rewriting the same value included.
#
# This is the guard for incident ⑤ of 2026-08-14, and the cost is counted in GPU hours:
# a worker had an already-superseded old task sitting in its local queue, finished it
# and posted the score, which resurrected that row into evaluated → the row falls back
# inside the predicate of `idx_sub_hotkey_round_commit` (that index excludes
# rejected/superseded) → it hits the unique constraint → the scoring endpoint returns
# 500 and hours of the worker's GPU time are wasted; and even without the collision, a
# version that had been pushed out took part in the ranking again.
#
# In production this landed as two SQL predicates
# `eval_status NOT IN ('superseded', 'rejected')` (prototype/backend/database.py:898 and
# :1099) plus the scoring endpoint discarding the whole thing
# (api/handlers/benchmark.py:182, returning 200 rather than an error code so the worker
# does not retry forever).
FROZEN_STATUSES: Final[frozenset[str]] = frozenset({STATUS_REJECTED, STATUS_SUPERSEDED})


# Legal status transitions. **Written as data, not as ifs scattered around** — the state
# machine itself has to be testable.
#
# Every edge has a source in production code; edges without a source are not written in
# (better too few than guessed):
#   received      → burn_checking            verify_submission.py:560
#   burn_checking → burn_passed              verify_submission.py:569
#   burn_passed   → pending                  database.py enqueue_eval
#   burn_passed   → seed_failed              verify_submission.py:627 (no drand)
#   seed_failed   → pending                  scanner_loop.py:_retry_seed_failed
#   pending       → evaluating               database.py:update_task_progress
#                                            (CASE WHEN eval_status='pending', one-way)
#   pending       → evaluated / eval_failed  A real historical path: before the
#                                            fix, update_task_progress wrote only
#                                            the stage and did not advance the
#                                            status, so the whole DB had 0 rows of
#                                            evaluating while dozens of evaluated
#                                            rows landed straight from pending
#                                            (2026-08-19 copy: 66 evaluated).
#                                            **Deleting this edge declares live
#                                            history illegal.**
#   pending       → superseded               submission_db.py:supersede_pending —
#                                            its WHERE has only one clause,
#                                            eval_status='pending'
#   evaluating    → evaluated / eval_failed  database.py:update_submission_status
#   * → rejected                             the scanner side may reject at any
#                                            step (verify_submission.py has 8)
_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    STATUS_RECEIVED: frozenset({STATUS_BURN_CHECKING, STATUS_REJECTED}),
    STATUS_BURN_CHECKING: frozenset({STATUS_BURN_PASSED, STATUS_REJECTED}),
    STATUS_BURN_PASSED: frozenset(
        {STATUS_PENDING, STATUS_SEED_FAILED, STATUS_REJECTED}
    ),
    STATUS_SEED_FAILED: frozenset({STATUS_PENDING, STATUS_REJECTED}),
    STATUS_PENDING: frozenset(
        {
            STATUS_EVALUATING,
            STATUS_EVALUATED,
            STATUS_EVAL_FAILED,
            STATUS_REJECTED,
            STATUS_SUPERSEDED,
        }
    ),
    STATUS_EVALUATING: frozenset(
        {STATUS_EVALUATED, STATUS_EVAL_FAILED, STATUS_REJECTED}
    ),
    # Terminal states have no outgoing edges. Spec invariant 7: one-way, no going back.
    STATUS_EVALUATED: frozenset(),
    STATUS_EVAL_FAILED: frozenset(),
    STATUS_REJECTED: frozenset(),
    STATUS_SUPERSEDED: frozenset(),
}

#: The state machine itself: `{current status: set of statuses it may move to}`.
#: Read-only; consumers must not modify it.
STATUS_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(_TRANSITIONS)

#: Every legal status. **Same source as the transition table** (it is exactly its key
#: set), so "in the table but not in the vocabulary" cannot happen.
ALL_STATUSES: Final[frozenset[str]] = frozenset(STATUS_TRANSITIONS)


def is_terminal(status: str) -> bool:
    """Whether this status is terminal (the process has ended, it goes no further).

    ⚠️ One difference from the function of the same name in the old
    `backend/protocol/status.py`: there `superseded` returned False (the vocabulary
    itself missed it). That old function had zero consumers at the time, so here it is
    corrected against the facts.
    """
    return status in TERMINAL_STATUSES


def can_transition(current: str, new: str) -> bool:
    """Whether the status change `current → new` is legal. An unknown status is always
    False (fail-closed).

    Rewriting the same value, `current == new`, is treated as legal — after the scoring
    POST times out the worker re-posts the same scores (`backend_client.py` first calls
    `fetch_submission` to check and then decides whether to retry), and storing that is
    one `evaluated → evaluated`. Frozen states are the exception: for rejected /
    superseded even rewriting the same value must be blocked, which is exactly how
    production's SQL predicates are written.
    """
    if current not in STATUS_TRANSITIONS or new not in STATUS_TRANSITIONS:
        return False
    if current == new:
        return current not in FROZEN_STATUSES
    return new in STATUS_TRANSITIONS[current]


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation stage (progress detail)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Stage:
    """The three spellings of one evaluation stage, bound into a single record.

    Writing them apart is ZCY-158: three names scattered across three repositories,
    change one and miss two, and the mismatch does not raise — the data just goes
    quietly wrong. Bound together, "say running to the outside, store benchmark_running
    in the DB, the worker calls it evaluating" are either all three right or all three
    wrong; being only half wrong is impossible.
    """

    #: The canonical public word. This is what the worker should send, what the API
    #: should return, and what the frontend should render against.
    #: It is exactly the word production's `GET /api/v1/submissions/history` returns in
    #: the `stage` field (measured 2026-08-17).
    wire: str
    #: The historical stored value (with the `benchmark_` prefix). What it looks like
    #: before the exit translation; you meet it when reading old data.
    #: ⚠️ **The two spellings coexist**, it is not "all old data has the prefix": in the
    #: 2026-08-19 production copy there are 38 rows of `running`, 24 rows of
    #: `benchmark_running`, 8 rows of `downloading` (bare) and 2 rows of
    #: `benchmark_prechecking` (prefixed). So the entry point must accept both — that is
    #: exactly why `_STAGE_LOOKUP` puts wire / stored / aliases side by side.
    #: **Do not** emit the prefixed form to the frontend — the frontend's ACTIVE_STAGES
    #: has no prefixed spelling, and what it does not recognize it does not render.
    stored: str
    #: The synonyms also accepted on the input side. Taking them is purely additive:
    #: callers written against the public docs or the frontend types used to get a 400
    #: because of this (actually happened on 2026-08-14).
    aliases: tuple[str, ...] = ()


STAGE_DOWNLOADING: Final[str] = "downloading"
STAGE_PRECHECKING: Final[str] = "prechecking"
STAGE_RUNNING: Final[str] = "running"
STAGE_CLAIMED: Final[str] = "claimed"

#: The stage vocabulary. The order is the worker's actual execution order.
STAGES: Final[tuple[Stage, ...]] = (
    # `claimed` = the worker took the task but has not started downloading, so it comes
    # first.
    #
    # Added on 2026-08-19. Before that this package had only three stages, while
    # **production accepts a fourth**: the whitelist of
    # `prototype-prod/backend/api/handlers/benchmark.py::handle_status_update` is
    # `{benchmark_downloading, benchmark_prechecking, benchmark_running,
    # benchmark_claimed}`, and anything not in it gets `INVALID_STATUS`.
    #
    # ⚠️ There are two pieces of evidence and they point in opposite directions; the
    # decision follows "what the code accepts": in the 08-18 production copy `claimed`
    # appears **0 times in all four columns** `stage` / `status` / `eval_status` /
    # `submission_history.eval_status` — it has never been stored. But the consequence
    # of missing this entry is not "one useless extra word"; it is that when a worker
    # reports `claimed` we judge it illegal while production accepts it — two sides
    # different answers for the same input, which is exactly what this package exists to
    # eliminate.
    Stage(wire=STAGE_CLAIMED, stored="benchmark_claimed"),
    Stage(wire=STAGE_DOWNLOADING, stored="benchmark_downloading"),
    # `precheck` is the spelling in the frontend types (QueueProgressStage in
    # web/src/api/types.ts).
    Stage(
        wire=STAGE_PRECHECKING,
        stored="benchmark_prechecking",
        aliases=("precheck",),
    ),
    # `evaluating` is the worker's internal spelling (it is what run_eval.py writes into
    # the progress file), and also the only input word the old backend recognized.
    # **The canonical public word is running**: across the 117 live records, stage has
    # appeared as running / downloading / prechecking and never as evaluating.
    # The worker-side `_PROGRESS_STAGE_MAP` is doing exactly this translation and can be
    # deleted once it depends on this package.
    Stage(wire=STAGE_RUNNING, stored="benchmark_running", aliases=("evaluating",)),
)

#: Every legal stage word (in canonical public form). Same source as STAGES.
ALL_STAGES: Final[frozenset[str]] = frozenset(s.wire for s in STAGES)

_STAGE_LOOKUP: Final[dict[str, str]] = {
    word: stage.wire
    for stage in STAGES
    for word in (stage.wire, stage.stored, *stage.aliases)
}


def normalize_stage(word: str) -> str | None:
    """Any side's stage spelling → the canonical public word. Returns None when it is
    not recognized, and the caller decides how to reject it.

    Case and leading/trailing whitespace are normalized first the way the production
    entry point does it (`.strip().lower()`).
    """
    return _STAGE_LOOKUP.get(word.strip().lower())


# ─────────────────────────────────────────────────────────────────────────────
# Legacy status words
# ─────────────────────────────────────────────────────────────────────────────

#: Old status word → current status word.
#:
#: These words are still alive in production data today (measured on the 2026-08-19
#: copy: `done` 37 / `failed` 4 / `confirmed` 1 / `enqueued` 17); they are the four
#: spellings of the same thing from before the vocabulary was unified.
#:
#: The shape that goes wrong is not "there are old words", it is **two vocabularies
#: mixed into the same response**: the frontend reads
#: `submission.status || submission.eval_status`, and the one it reads first happens to
#: be the un-normalized one — on 2026-08-14, 33 of the 95 rows on the queue page showed
#: wrong status. So normalization must happen in **one** place.
#:
#: **`ALL_STATUSES` is the single source of truth.** This table is only for reading old
#: data; do not use it to produce new values.
#: Which storage location the old words live in today, and up to which step they are
#: normalized, is the backend's business and not within this module's promises.
LEGACY_STATUS_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "enqueued": STATUS_PENDING,
        "waiting": STATUS_EVALUATING,
        "benchmark_downloading": STATUS_EVALUATING,
        "benchmark_prechecking": STATUS_EVALUATING,
        "benchmark_running": STATUS_EVALUATING,
        "benchmark_done": STATUS_EVALUATED,
        "benchmark_failed": STATUS_EVAL_FAILED,
        "done": STATUS_EVALUATED,
        "failed": STATUS_EVAL_FAILED,
    }
)


def normalize_status(status: str) -> str:
    """Old status word → current status word. Words not in the table are returned
    verbatim (the same behaviour as the old implementation).

    Returning them verbatim is deliberate: what the caller has may be an unknown new
    word, and this function should not judge it illegal on their behalf — legality is
    judged by `ALL_STATUSES`.
    """
    return LEGACY_STATUS_ALIASES.get(status, status)
