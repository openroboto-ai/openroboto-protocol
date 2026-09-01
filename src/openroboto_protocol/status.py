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
  values see `ALL_STATUSES` (every word this package knows) and
  `STORABLE_STATUSES` (the subset a `status` column will accept).
- **stage**: what the worker is doing after claiming the task. The progress detail; for
  the values see `ALL_STAGES`, meaningful only while status is `evaluating`.

`evaluating` is a **status**; `running` is a **stage**. They describe the same span of
time, but they are not the same vocabulary, and either side written with the other
side's word is judged illegal by that other side.

The real track adds two more vocabularies at the bottom of this file — `SeasonStatus`
(how far one season has got) and `InvalidReason` (why a submission was disqualified).
It deliberately adds **no** submission status: real-track submissions live in the same
table and use the same lifecycle words as everything else.

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

The one storage fact that *is* here is `STORABLE_STATUSES`: "the word I am about to
write will be refused by the column" is not an implementation detail, it is a fact both
sides have to agree on before the write, and disagreeing about it costs a 500 rather
than a validation error.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

#: The public surface of this module (AGENTS.md §1②: without it there is no line
#: between `patch` and `major`).
__all__ = [
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

#: Every status word this package knows. **Same source as the transition table** (it is
#: exactly its key set), so "in the table but not in the vocabulary" cannot happen.
#:
#: This is the vocabulary for **reading**: deciding whether a word that arrived from
#: somewhere is legal, running the state machine, judging a historical row. Before
#: **writing** a status, use `STORABLE_STATUSES` — this set is deliberately larger.
ALL_STATUSES: Final[frozenset[str]] = frozenset(STATUS_TRANSITIONS)

#: The subset a submission's status column will actually accept — the vocabulary for
#: **writing**.
#:
#: Word for word the whitelist of `ck_submissions_status`
#: (`openroboto-backend/app/alembic/versions/0001_target_schema.sql`). A word outside it
#: does not fail validation, it violates the CHECK at INSERT time — i.e. a 500 after the
#: request was already accepted. Which is exactly what a consumer that checks a
#: to-be-written status against `ALL_STATUSES` will produce, because `ALL_STATUSES` says
#: yes to two words no column holds.
#:
#: 🔴 **Written out rather than derived from `TRANSIENT_STATUSES`**, so that the default
#: for a newly added status is "not storable": forgetting to list a new word here makes
#: the write fail *before* it reaches the database, while deriving it would make the new
#: word silently storable and move the failure to the CHECK. Fail closed on the side
#: that produces a diagnosable error.
#:
#: ⚠️ Today this is still a copy of a hand-written constraint, kept honest by
#: `tests/test_status.py`. It stops being a copy when the backend deletes its own
#: `SUBMISSION_STATUSES` and imports this one (`AGENTS.md` §1②, the 1.0 checklist).
STORABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        STATUS_RECEIVED,
        STATUS_PENDING,
        STATUS_SEED_FAILED,
        STATUS_EVALUATING,
        STATUS_EVALUATED,
        STATUS_EVAL_FAILED,
        STATUS_REJECTED,
        STATUS_SUPERSEDED,
    }
)

#: In the vocabulary, never in the column: `burn_checking` and `burn_passed`.
#:
#: They name the two steps of the burn check, and the backend records **that** check in
#: a separate `burn_status` column, so the lifecycle column goes straight from
#: `received` to `pending` / `seed_failed` and never holds either word. They stay in the
#: vocabulary because they are real: the chain scanner passes through both
#: (`verify_submission.py:560` / `:569`), the public API reference documents both, and
#: `received → burn_checking → burn_passed → pending` is the honest shape of the scan.
#: Dropping them would make `can_transition` call the scanner's own steps illegal, and
#: would make old rows and old docs unreadable — which is the failure this package
#: exists to prevent, pointed the other way.
#:
#: Derived, so the two sets above cannot disagree about what the difference is.
TRANSIENT_STATUSES: Final[frozenset[str]] = ALL_STATUSES - STORABLE_STATUSES


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

#: The worker gave the task back without finishing it. Added 2026-09-01.
#:
#: 🔴 This exists because its absence was silent. The worker's evaluation retry path
#: (`_schedule_clean_retry`) cleared its caches and re-queued locally **without telling
#: the backend anything** — no report, no attempt counter, no backoff. On 2026-09-01 two
#: paid miners sat at `prechecking` for four hours while the worker crash-looped every
#: two minutes, and from the outside that is indistinguishable from a healthy
#: evaluation. `stage` is the only field that could have said otherwise, and it had no
#: word for "I put it back".
#:
#: ⚠️ This is **not** a failure verdict. The task returns to the queue and someone will
#: pick it up again; nothing about the miner's model has been decided.
STAGE_QUEUED: Final[str] = "queued"

#: The worker has given up retrying and is waiting for a human. Added 2026-09-01.
#:
#: 🔴 **Deliberately not `eval_failed`.** N consecutive failures on one machine is
#: evidence about *that machine*, not about the model — infrastructure crashing is our
#: fault, and the miner's fee is already burned. Marking it failed spends their TAO on a
#: conclusion nobody reached. The discriminator that would justify a verdict is "it
#: fails where others succeed", which needs dispatch history across machines that the
#: backend does not keep today.
#:
#: So this word means exactly: stopped trying, nothing decided, come look.
STAGE_STALLED: Final[str] = "stalled"

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
    # ⚠️ These two carry **no** `benchmark_` prefix, unlike the four above. That prefix
    # marks "the benchmark is doing something with this row"; both of these mean the
    # opposite — the worker has let go. Giving them the prefix would make
    # `stage.startswith("benchmark_")` (a shape that reads naturally and will get
    # written) mean the reverse of what it says.
    Stage(wire=STAGE_QUEUED, stored=STAGE_QUEUED),
    Stage(wire=STAGE_STALLED, stored=STAGE_STALLED),
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


# ─────────────────────────────────────────────────────────────────────────────
# Real-track vocabularies (season progress · disqualification reasons)
# ─────────────────────────────────────────────────────────────────────────────
#
# 🔴 **Neither of these is a submission status.** Real-track submissions live in
# the same `submissions` table as simulation ones, told apart by
# `competition_id`, so they share the same `status` column and the same words
# above. "Only one vocabulary in the database" applies to the real track word for
# word; this module does not invent a second one.
#
# How the real track's own wording (spec 10 §2.5) lands on those words:
#
#     registered / awaiting evaluation  → received
#     evaluating                        → evaluating
#     finished                          → evaluated
#     disqualified                      → rejected, plus an InvalidReason
#
# `disqualified` does not become a status of its own because it is a **reason**,
# not a terminal state: the terminal state is `rejected`, and which of the five
# reasons applies is what a miner actually needs to be told. The same shape is
# already in production — `supersede_pending` writes `rejected` plus a reason
# prefix because the CHECK constraint has no `superseded`.
#
# ⚠️ One invariant this package cannot express, guarded on the backend side: a
# real-track row never enters `status='pending'`. That word is the simulation
# dispatcher's tenant key.


class SeasonStatus(StrEnum):
    """How far one **season** has got — the real track is a tournament run in
    seasons, so this describes the season, never a single submission (spec 10
    §4.3).

    🔴 It is **not** `competitions.status`, which is the row's lifecycle
    (`draft` / `active` / `archived`, ADR 03 §3.3), and it is not the phase
    derived from the season's five timestamps either. Storing this next to
    either of those would create two answers to the same question; it belongs on
    the settlement record.

    Written by the system, never by a miner or an operator.
    """

    AWAITING_EVAL = "awaiting_eval"
    """Submissions have closed and the entry list is locked; evaluation has not
    finished. Written when `submit_closes_at` passes (spec 10 §4.3)."""

    AWAITING_SETTLEMENT = "awaiting_settlement"
    """Every entry has been evaluated; the ranking and the qualification
    threshold have not been settled yet (spec 10 §4.3)."""

    AWAITING_CONFIRMATION = "awaiting_confirmation"
    """The settlement record exists — ranking, threshold, champion, qualified and
    disqualified lists are all written down — and **no α has moved yet**. Leaving
    this word is a human decision, and it is the last reversible moment of the
    season: the next word starts a daily transfer that cannot be recalled.

    Not one of the five words in the PRD. `openroboto-backend` added it (cooling
    period plus a manual gate, `.trellis/tasks/08-23-finalize-command`) and this
    package follows, because a state that exists in the code but not in the
    vocabulary has to be squeezed into a word that means something else — either
    `awaiting_settlement`, which is false the moment the record is written, or
    `paying`, which claims money is moving while a human is still deciding. The
    PRD listing five words was a description, not a budget.
    """

    PAYING = "paying"
    """Confirmed, and the payout plans exist but have not all been executed. It is
    the only word during which money is in flight, so a crash here is resumed
    from, not restarted (spec 10 §4.3).

    **Spelled `paying`, not `paying_out`**, and the tie-break is which spelling is
    harder to misread, not which repository wrote it first. The neighbouring word
    is `paid_out`: against it, `paying_out` is a near-anagram sharing the `_out`
    suffix and differing in the middle of the string, while the two words mean
    "α is leaving the wallet every day" and "the season is closed, nothing more
    will move". A pair that a tired reader, a `grep pay`, or an eye running down a
    status column can swap is the wrong pair when the difference is whether money
    is still in flight. `paying` / `paid_out` cannot be confused at a glance.
    (`openroboto-backend`'s task docs had already written `paying` in 20-odd
    places — that made the decision cheap, it is not what made it right.)
    """

    PAID_OUT = "paid_out"
    """Every payout plan has been executed in full. Terminal (spec 10 §4.3)."""

    BURNED = "burned"
    """Nobody met the qualification threshold, so the whole prize pool was
    burned. Terminal, and **not a failure** — it is the designed outcome of a
    season with no qualifying entry (spec 10 §4.3)."""


class InvalidReason(StrEnum):
    """Why a submission was disqualified — the stable code stored alongside
    `status='rejected'` (spec 10 §2.5, §3.2).

    All five are HuggingFace access findings, which is why they carry the `hf_`
    prefix: a reason vocabulary grows, and the next family (payment amount
    wrong, wrong coldkey, wrong payer) must not collide with a bare
    `unauthorized`.

    ⚠️ Not the same layer as `schemas.ReasonCode`: that one is the
    SCREAMING_SNAKE code the public API hands to a client inside `Reason`, this
    one is the value stored in the column. The mapping between them is the
    backend's job.

    Access is **re-checked until evaluation finishes**, so a submission can
    become invalid after having been accepted — that is what `access_revoked`
    exists for.
    """

    FORBIDDEN = "hf_forbidden"
    """The official evaluation account was never added as a collaborator, so the
    weights cannot be read at all."""

    ACCESS_REVOKED = "hf_access_revoked"
    """Access existed at submission time and was taken away before evaluation
    finished. A separate word from `hf_forbidden` on purpose: this one says the
    miner changed something after paying."""

    REPO_NOT_FOUND = "hf_repo_not_found"
    """No repository by that name — a typo in `hf_repo_id`, or it was
    deleted."""

    REVISION_NOT_FOUND = "hf_revision_not_found"
    """The repository exists but the `hf_commit` pinned on chain does not. The
    commit is what makes an evaluation reproducible, so falling back to the
    default branch is forbidden."""

    FILES_INCOMPLETE = "hf_files_incomplete"
    """The repository is reachable but does not contain a loadable checkpoint
    (see `model_format.py` for the required shape)."""


#: The value tuple of `SeasonStatus`, in the order a season passes through it.
#:
#: ⚠️ **Nothing enforces that a database agrees with this tuple.** `ck_settlement_status`
#: in `openroboto-backend` is a hand-typed word list, and hand-typing it is exactly how
#: `paying_out` and `paying` became two spellings of one state before either had been
#: run once. The order is part of the export so that a migration can stop typing them::
#:
#:     from openroboto_protocol.status import SEASON_STATUS_VALUES
#:
#:     words = ", ".join(f"'{v}'" for v in SEASON_STATUS_VALUES)
#:     op.execute(
#:         "ALTER TABLE season_settlements ADD CONSTRAINT ck_settlement_status "
#:         f"CHECK (status IN ({words}))"
#:     )
#:
#: That buys "the constraint was right on the day it was written" and no more — a
#: migration is frozen history, so a later release of this package can move the tuple
#: out from under a constraint that has already run. The part that keeps tracking has to
#: live on the consumer's side: a test that reads the constraint back out of
#: `pg_constraint` and compares it with this tuple. Until that test exists, agreement
#: here is a convention, not a guarantee, and this comment says so rather than
#: reassuring anyone.
SEASON_STATUS_VALUES: Final[tuple[str, ...]] = tuple(s.value for s in SeasonStatus)

#: The value tuple of `InvalidReason`. Same use — and the same caveat as above: it
#: matches the word list of `ck_submissions_invalid_reason` in the backend's 0004
#: migration today because both were typed from spec 10 §2.5, not because anything
#: compares them.
INVALID_REASON_VALUES: Final[tuple[str, ...]] = tuple(r.value for r in InvalidReason)
