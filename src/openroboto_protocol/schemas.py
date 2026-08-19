"""Request / response models for every API endpoint — the one copy of the field
contract.

**Why this must exist**: "field contracts kept by verbal agreement" is this project's
most expensive historical problem. Three consequences that already happened:

1. ZCY-158 — the stage vocabulary of progress reports disagreed across four parties,
   and a worker written against the public docs got a 400.
   The "fix" back then was to add a hand-written translation table inside the worker
   (`_PROGRESS_STAGE_MAP` in `benchmark_worker/backend_client.py`), **still there
   today**.
2. Incident ⑧ — two top-level keys were hand-picked out of the `detail` object of a
   progress report and every other field was lost; the queue page's progress bar
   disappeared entirely, and the backend has been unable to answer "which step is this
   task on" ever since.
3. The frontend `web`'s last commit is called *Tolerate rebuilt-backend field renames*
   — a consumer is writing a compatibility layer for our renames.

Once both sides install the same version number, **hand-picking fields is forbidden**:
the response is the shape declared here, and one field more or one field less means
changing this file.

## What this module promises

- The **field names, nesting levels and optionality** of every endpoint. The provenance
  of each field (measured live / line number in production code) is written in the
  comments, and the comment is the basis of the ruling.
- Three "unrepresentable" guards: an empty `env_list`, leaderboard-position words mixed
  into lifecycle words, and `status`/`stage` disagreeing in a progress response. These
  are shapes that have really bitten people in production.
- The **normalization** shared by both sides: the progress entry vocabulary and the
  `detail` extraction (`ProgressUpdate.from_payload`).
  The worker-side `_PROGRESS_STAGE_MAP` can be deleted once it depends on this package.

## What this module is not responsible for

HTTP status codes, auth scopes, SQL, pagination implementation. A failed check raises
`ContractError` carrying a **stable code**, and the backend decides which status code
it maps to — because for a worker a 4xx is the "destroy one 8-hour evaluation result"
button and a 5xx is the "write the DB twice" button, so which branch gets which code
can only be nailed down by the backend against the contract card (the error
classification table in spec 07 §0.3).

## Why pydantic, and only in this one module

The main package's `dependencies` is empty; pydantic goes through the `[schemas]`
optional-dependency (see that comment in `pyproject.toml`). A miner installs this
package only to derive seeds and should not be forced to compile a pydantic-core wheel
on a GPU machine; `__init__.py` re-exports nothing, so `import openroboto_protocol` can
never trigger `import pydantic`.

The **single hard reason** for choosing pydantic over a stdlib dataclass (measured
2026-08-17 with pydantic 2.13.4): the dataclass version can only put validation in
`__post_init__`, and by then the framework has already converted the JSON values once —
in lax mode `{"score": true}` first becomes `1.0`, so "a bool is not a number" (the 1st
check of production's `_validate_env_scores`) **cannot be stopped** on the automatic
parsing path. The historical cost was `{"score": 99.0, "samples": -5}` plus only 1
suite submitted → 200 → straight to dethroning the champion and taking 7% of emissions.
With real models we can put `StrictFloat` / `StrictInt` on the fields and make this
unrepresentable at the **type level**.
Measured on the same batch of values: `StrictFloat` rejects `true` and `"1.0"` but
still accepts the JSON integer `1` (a worker sending `total_score: 0` is not hit by
mistake).

## Strictness only on the write path; no value-range constraints on the read path

This is not laziness, it is a considered trade-off:

- **Write path** (`score` / `samples` of `EnvScore`) uses `Strict*`. These numbers are
  real on-chain emissions 40 minutes later at the earliest, so better to reject with a
  4xx.
- **Read path** (loading historical rows from the DB back into models) adds **no `ge` /
  `le` / `allow_inf_nan=False`**. Rows like `score=99.0` **really do exist** in the
  production DB (that is exactly how the 2026-08-14 one got in), and adding value-range
  constraints to the read models turns "reading one dirty historical row" into a 500 —
  and for a worker a 5xx is the "POST again" button (spec 07 §0.3).
  The range check stays in `check_env_scores()`: it takes the **raw JSON**, runs before
  the DB write, and carries the stable code `INVALID_SCORE`. One guard, on the boundary.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import (
    Annotated,
    Any,
    Final,
    Generic,
    Literal,
    Protocol,
    TypeVar,
    get_args,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    ValidationError,
    computed_field,
    model_validator,
)

from .constants import REQUIRED_ENVS
from .status import normalize_stage

# ─────────────────────────────────────────────────────────────────────────────
# Contract violations
# ─────────────────────────────────────────────────────────────────────────────


class ContractError(ValueError):
    """The request does not satisfy the contract. `code` is a **stable machine code**;
    the wording may change, the code may not.

    Deliberately carries no HTTP status code: the correct status code for the same
    violation differs per endpoint, and the cost of picking the wrong one is the miner's
    GPU hours (4xx → worker `abandoned`, discarding the whole evaluation result).
    The mapping table is in the backend's contract card, not here.

    It inherits `ValueError` so it can be raised directly inside pydantic validators —
    pydantic wraps it into a `ValidationError`, and the original object can still be
    retrieved from `err.errors()[0]["ctx"]["error"]`.
    But **the preferred way to call is explicitly `check_*()` / `from_payload()`**: on
    that path `.code` does not get wrapped away, which is what lets the backend map it
    to the status code nailed down in the contract card.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        #: Stable error code, SCREAMING_SNAKE_CASE, append-only and never changed.
        self.code = code


#: An illegal score in `env_scores` (type / NaN / out of range / illegal samples).
CODE_INVALID_SCORE: Final[str] = "INVALID_SCORE"
#: `success=true` but the 6 required suites were not all submitted.
CODE_MISSING_ENVS: Final[str] = "MISSING_ENVS"
#: The stage word of a progress report is not in the controlled vocabulary.
CODE_INVALID_STAGE: Final[str] = "INVALID_STAGE"


class Contract(BaseModel):
    """Base class of every model in this module.

    `frozen=True`: a response model should not be changed after it is built. A mutable
    response object means "who changed this field" can only be answered by reading the
    whole call chain — and this repository's historical problem is exactly people
    hand-editing fields at the exit (the `row.pop("status")` family).

    `extra` keeps pydantic's default of **ignore**, and **must not be changed to
    forbid**: spec 07 explicitly requires "a payload carrying a new field the backend
    does not recognize → 200, just ignore it".
    For a worker 4xx = abandon = destroy one 8-hour evaluation result, and "the
    evaluation party added a field" should not be that button.
    """

    model_config = ConfigDict(frozen=True)


# ─────────────────────────────────────────────────────────────────────────────
# Response envelope (ADR 02, overturning "no response envelope")
# ─────────────────────────────────────────────────────────────────────────────
#
# The shape (decided by `../openroboto-backend/docs/adr/02-统一响应信封.md`, not
# re-discussed here):
#
#     object  {"data": {...},  "meta": {request_id, generated_at}}
#     list    {"data": [...],  "meta": {request_id, generated_at, page: {...}}}
#     error   {"error": {code, message, retryable}, "meta": {request_id, generated_at}}
#
# Four rules, **three of them unrepresentable at the type level**, relying on no runtime
# validation at all:
#
# 1. A success always has `data` and never has `error`; an error is the reverse — the
#    two envelopes are two classes, each having only its own field, and `Contract`'s
#    `extra=ignore` throws away whichever extra one is passed in.
# 2. `meta.request_id` is on every response (errors included) — a required field, and
#    without it you cannot construct one.
# 3. `meta.page` **exists only on list endpoints** — the declared type of
#    `Envelope.meta` is `Meta`, and that class **has no `page` field at all**; pydantic
#    serializes by the declared type, so even if someone forces a `ListMeta` in there,
#    a single-object response will not grow that key.
#    ⚠️ This one is deliberately **not** implemented as `page: PageMeta | None = None`
#    plus `exclude_none`: that requires every route to remember
#    `response_model_exclude_none=True`, and missing one emits an extra `"page": null`.
#    Forgetting once is silent; the type level is not.
# 4. `data` holds business fields only; meta information such as `total` /
#    `generated_at` always goes into `meta`.
#    This one **cannot be enforced at the type level** (`T` is any model); it is
#    guarded by ADR 02 and code review.
#
# The probes (`/healthz` `/readyz`) and `/metrics` are the **only exception: no
# envelope**. Their consumers are PM2 / the load balancer / Prometheus, and with an
# envelope they simply cannot parse it.
# So `LivenessResponse` / `ReadinessResponse` are bare models, and
# `tests/test_schemas.py::test_probes_are_never_enveloped` pins this explicitly.

T = TypeVar("T")


class Meta(Contract):
    """The meta information carried by every response. **Success, error, empty list —
    not one missing.**

    `request_id` is required: it already runs through the whole chain (the contextvar in
    the backend's `core/logging.py` plus the `X-Request-ID` response header), so when
    something goes wrong the user can just paste it over and the logs can be found.
    Giving it a default value means allowing "the response you cannot look up" to exist,
    and that is exactly the one you most need to look up.

    `generated_at` **is on error responses too** (the example in ADR 02 omitted it): one
    meta, one parser; a caller does not need a second schema for the error branch.
    """

    #: The value of the backend's `get_request_id()`, the same value as the
    #: `X-Request-ID` response header.
    request_id: str
    #: Server-side UTC instant. **The only field allowed to vary between calls** (the
    #: carrier of leaderboard invariant 4: business data is idempotent field by field,
    #: and only after the timestamp moved here can `data` really be compared byte for
    #: byte).
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PageLike(Protocol):
    """The structural type `PageMeta.of()` takes — both of the backend's `Page` types
    **already** satisfy it.

    - `Page[T]` in `app/repositories/pagination.py` (frozen dataclass, `items` is a
      tuple) — the return value of every repository list method;
    - `Page[T]` in `app/api/schemas.py` (pydantic model, `items` is a list).

    Written as a structural type instead of importing one of them: the protocol package
    has zero runtime dependencies and should not know the backend's layering either.
    All members are declared as read-only properties, which both dataclass fields and
    pydantic fields satisfy.
    """

    @property
    def items(self) -> Sequence[Any]: ...
    @property
    def total(self) -> int: ...
    @property
    def limit(self) -> int: ...
    @property
    def offset(self) -> int: ...


class PageMeta(Contract):
    """Pagination meta. **Only hangs off `ListEnvelope.meta.page`, never enters
    `data`.**

    Production today has five custom shapes such as `{total, rows}` /
    `{success, submissions, total, limit, offset}`, with the pagination numbers and the
    business fields mixed into the same level — which is why the frontend writes one
    parser per endpoint.

    `total` is the **total after filtering**, not the number of rows on this page. The
    new skeleton once wrote `total=len(page)`: the frontend computes the page count from
    it, so the page number was forever 1.
    """

    total: int
    limit: int
    offset: int
    #: There is a next page. **Do not let callers compute `offset + len(data) < total`
    #: themselves** — that expression gets copied 8 times across 8 endpoints, and
    #: getting one of them wrong silently loses rows while paging.
    has_more: bool

    @classmethod
    def of(cls, page: PageLike) -> PageMeta:
        """Compute the pagination meta from one page of query results.

        **There is only this one copy of the `has_more` formula.**
        """
        return cls(
            total=page.total,
            limit=page.limit,
            offset=page.offset,
            has_more=page.offset + len(page.items) < page.total,
        )


class ListMeta(Meta):
    """The meta of list endpoints: exactly one field more than `Meta` — `page` — and it
    is **required**.

    Required on purpose: without it the frontend cannot compute page numbers, and
    "forgot to carry the pagination information" cannot be seen in the response body —
    it just looks like a short list.
    """

    page: PageMeta


class Envelope(Contract, Generic[T]):
    """Single-object success response. Use `response_model=Envelope[LeaderboardRow]`
    directly.

    `data` is a **single object**. Lists use `ListEnvelope` — it has one more required
    `meta.page`, so "a list endpoint forgot to give pagination information" does not get
    past the type level.
    """

    data: T
    meta: Meta


class ListEnvelope(Contract, Generic[T]):
    """List success response.

    An empty list is `data: []` plus `page.total = 0`, **not a 404 and not `null`**.
    """

    data: list[T]
    meta: ListMeta


class ErrorBody(Contract):
    """Error detail.

    **No `data` field: success and failure are structurally two different types.**
    """

    #: Stable machine code (the same set as `ContractError.code` / the backend's
    #: `AppError.code`). Clients are only allowed to branch on this.
    code: str
    #: For humans. The wording will change and it will be translated, so branching on it
    #: is **forbidden**. Must not contain server-internal paths.
    message: str
    #: **No default value, it must be given explicitly.** The cost of guessing this
    #: boolean wrong is asymmetric: mark `True` one time too few and the worker treats
    #: one piece of infrastructure flakiness as a permanent failure, destroys an 8-hour
    #: evaluation result, and the TAO the miner burned is not refunded. Defaulting to
    #: `False` picks that side for everyone who forgot to think about it.
    retryable: bool


class ErrorEnvelope(Contract):
    """Error response. **No `data`**, but `meta.request_id` is still there — reporting a
    problem runs on it.
    """

    error: ErrorBody
    meta: Meta


class ValidationErrorBody(ErrorBody):
    """422 only: exactly one field more than `ErrorBody` — `fields` — and it is
    **required**.

    The landed shape of ADR 02 §8 open question ② (2026-08-18). That question was:
    `detail` today is a list `[{loc, msg, type}, …]` while `ErrorBody.message` is a
    single string, so per-field errors have no place to live. The two candidates were
    "add an optional field" and "merge it into one line of text".

    Adding a field won, but **not as `fields: … | None = None`** — that requires every
    route to remember `response_model_exclude_none=True`, and missing one emits an extra
    `"fields": null`, while forgetting once is silent (the same reason made `meta.page`
    a subclass too, see `ListMeta`).
    With a subclass, "a 422 that forgot to carry per-field information" cannot be
    constructed at the type level, all other error codes do not even grow this key, and
    the already-decided shape of `ErrorBody` is not touched by a single byte.

    "Merge it into one line of text" was not chosen: 422 is the **only error code of
    them all whose content is structure**, and flattening structure into prose throws
    machine readability away exactly where it is needed most.

    ⚠️ `fields` has **only `loc` / `msg` / `type`, no `input`**. By default pydantic
    puts the received value into `input`: when a worker sends `NaN` (Python's
    `json.dumps` emits that literal by default) that field cannot be serialized, so a
    "clean 422" turns into an uncaught exception plus a 500 and the person who sent the
    bad data sees no reason at all; on top of that the submission body contains the
    hotkey and the full evaluation result, so echoing it copies the payload into the
    logs and the response.
    """

    #: `[{"loc": "body.env_scores", "msg": "…", "type": "missing"}, …]`
    #:
    #: ⚠️ **One difference** from the bare shape's `detail`: over there `loc` is the
    #: array `["body", "env_scores"]`, here it is joined into a dotted path. The same
    #: amount of information, but it can be displayed as received, and the type of the
    #: whole `fields` stays contained (all strings) — the array spelling has type
    #: `dict[str, list[str] | str]`, and every consumer has to write one narrowing for
    #: one key.
    #: That is the only difference between the two sides; migration notes §2 lists them
    #: one by one.
    fields: list[dict[str, str]]


class ValidationErrorEnvelope(Contract):
    """The error response of 422. `error` is a `ValidationErrorBody`, the rest is the
    same as `ErrorEnvelope`.
    """

    error: ValidationErrorBody
    meta: Meta


# ─────────────────────────────────────────────────────────────────────────────
# Time and shared fragments
# ─────────────────────────────────────────────────────────────────────────────
#
# **Every datetime field is ISO8601 with a timezone (ending in `+00:00`).**
# Before 0002, `submitted_at` / `created_at` / `updated_at` were still TEXT columns in
# production and already had two formats (25 characters / 32 characters with
# microseconds); the repository layer must normalize before filling the models — ranking
# replay sorts by time, and on TEXT that is only **accidentally** correct via ISO string
# comparison; drop a T or write the timezone differently and it is silently wrong.
#
# **`AwareDatetime` is deliberately not used** here to force a timezone: the read path,
# same reason as the previous section — one naive historical timestamp should not turn
# the whole leaderboard into a 500. Normalization is the repository layer's
# responsibility; here only the requirement is written down.
#
# `commit_block_timestamp` is the exception: in the DB it is a `bigint` (Unix seconds)
# and production serves it as an integer, so it stays an integer. Two time encodings in
# the same response is an accomplished fact, and changing it has no upside.
#
# The three time semantics must never stand in for one another:
#   on-chain time   submissions.submitted_at / commit_block_timestamp
#                   ← queueing and ordering decisions recognize only this
#   evaluation time eval_scores.evaluated_at   ← the basis of ranking order
#   local time      created_at / updated_at    ← audit display only


class MinerRef(Contract):
    """A miner's public identity."""

    hotkey: str
    #: `hotkey[:12]`, **not** the nickname the user set (measured live:
    #: `"5FQxZBhriyAv"`).
    display_name: str


class ModelRef(Contract):
    name: str
    hf_repo: str
    #: The `hf_commit` of the task that produced the score (invariant 6).
    #: **Falling back to another submission of that miner is forbidden** — that is the
    #: shape of incident C.
    #:
    #: ⚠️ **Not found is `null`, not an empty string.** The only source is the 40-digit
    #: `hf_commit` (the CLI's `preflight.py` validates it before it goes on chain), so
    #: `""` has never been a legal revision; it can only mean "the backend did not find
    #: it". And in HuggingFace's URL semantics `""` means "use the default branch" —
    #: building `.../tree/{revision}` with it silently jumps to main, and what the
    #: auditor checks is then not the commit that produced the score. `null` leaves "no
    #: value" nowhere to hide.
    #: `None` = no commit pinned. **An empty string will not do**: when the frontend
    #: builds `huggingface.co/{repo}/tree/{revision}`, `""` silently lands on the
    #: default branch (it looks fine, but points at a different piece of code), while
    #: `None` is at least a loud 404.
    revision: Annotated[str, Field(min_length=1)] | None = None


class ScoreStat(Contract):
    """`std` is `None` on a submission that has only been run once, **not 0**."""

    mean: float
    std: float | None = None
    #: ⚠️ The meaning is undecided (the leaderboard gives 1, `/submissions/{id}` gives
    #: 6, and the frontend displays both). See spec 04 §9 Q4; do not pick a meaning for
    #: it in the implementation before that is ruled on.
    trials: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Rejection reasons (shared by the four public endpoints)
# ─────────────────────────────────────────────────────────────────────────────

#: The controlled vocabulary of `Reason.code`. **Append-only, never changed**, and
#: treated on a par with the OpenAPI schema.
#:
#: Today the same thing has three readings: `rejected` reads `reject_reason`,
#: `eval_failed` reads `result.error`, and `superseded` has **nowhere to read from** —
#: which is why the frontend hardcodes `unavailable` at `QueuePage.tsx:438` (ZCY-162).
#: This table is the antidote to that hardcoded string.
#:
#: Written as a `Literal` rather than validated against a frozenset: in OpenAPI it comes
#: out as an enum directly, so the types generated by consumers carry this table and
#: they do not have to read the docs.
ReasonCode = Literal[
    "BURN_INSUFFICIENT",
    "BURN_TX_NOT_FOUND",
    "BURN_TX_REPLAY",
    "BURN_TX_TOO_OLD",
    "ROUND_MISMATCH",
    "HF_STRUCTURE_INVALID",
    "PLAGIARISM_DETECTED",
    "EVAL_PRECHECK_FAILED",
    "EVAL_CRASHED",
    "SEED_UNAVAILABLE",
    "SUPERSEDED",
    "INFRA_ERROR",
]

#: The values of `Reason.source`: they tell a person which step to investigate.
ReasonSource = Literal["scan", "eval", "supersede"]

#: The set form of the vocabulary, for callers and tests that need set operations.
#: **Same source as the `Literal`**, so "in the annotation but not in the vocabulary"
#: cannot happen.
REASON_CODES: Final[frozenset[str]] = frozenset(get_args(ReasonCode))
REASON_SOURCES: Final[frozenset[str]] = frozenset(get_args(ReasonSource))


class Reason(Contract):
    """The reason for a non-success terminal state. The old fields (`reject_reason` /
    `result.error`) are kept as they were; this is an addition.

    Contract: when `eval_status` is terminal and not `evaluated`, `reason` **must not be
    null**, and `superseded` must have one too (`code="SUPERSEDED"`). It is `null` for
    `evaluated` / `pending` / `evaluating`.
    """

    #: Machine code. Clients are only allowed to branch on this.
    code: ReasonCode
    #: For humans. The wording will change and it will be translated, so clients
    #: branching on it is **forbidden**.
    #: **Must not contain server-internal paths** — the
    #: `/data2/fs_home/cod/subnet-ws/validator/…` inside production's `result.error` is
    #: an existing leak, to be cut during the migration.
    message: str
    #: The only boolean a miner / the CLI needs to branch on. **An infrastructure
    #: failure is not a business rejection**: things like
    #: `burn_error: failed_to_create_subtensor` must be `True`, otherwise the TAO the
    #: miner burned is thrown away by one flaky chain RPC call.
    retryable: bool
    source: ReasonSource


# ─────────────────────────────────────────────────────────────────────────────
# worker contract group — GET /api/v1/benchmark/queue
# ─────────────────────────────────────────────────────────────────────────────


class QueueTask(Contract):
    """One task dispatched to a GPU worker.

    The field set aligns word for word with the 15 keys of production
    `handlers/benchmark.py:290-305`. On the worker side it is read by
    `benchmark_worker/backend_client.py:fetch_queue()`.
    **Deleting any one of them is a breaking change**, and the other party is on someone
    else's GPU machine where we cannot reach them.
    """

    task_id: str
    #: The worker knows `miner_uid`, the DB column name is `uid` — the exit sends only
    #: `miner_uid` (production `t.get("uid",0) or t.get("miner_uid",0)`: the double read
    #: is at the entry, not at the exit).
    miner_uid: int
    miner_hotkey: str
    hf_repo_id: str
    hf_commit: str
    round_num: int
    #: The public face of the "seed derivation" red line. The worker's
    #: `select_init_seed()` reads it directly; the three below are what a miner needs to
    #: reproduce the seed derivation independently, and **not one of them may be
    #: dropped**.
    seed: int
    block_hash: str
    drand_random: str
    drand_round: int
    #: **It must never be an empty array** — `min_length=1` makes it unrepresentable at
    #: the model level.
    #: What the DB stores is the string `'[]'`, and a non-empty string is truthy, so
    #: neither an `or` fallback nor `.get(k, default)` catches it — you must
    #: `json.loads` first and then check for emptiness, backfilling the 6 suites when
    #: empty. In the reverse order it does nothing: on 2026-08-14 the submissions of uid
    #: 221/231 entered the queue and the worker spun idle, stuck exactly here.
    #: It also **must not be a JSON string**: iterating a string directly splits it into
    #: single characters, which once made tasks be misreported as "invalid env names"
    #: (the fallback in the worker's `scoring.parse_env_list` is incident residue, not
    #: design).
    env_list: Annotated[list[str], Field(min_length=1)]
    #: Only `pending` is dispatched (invariant 7). The values come from
    #: `status.ALL_STATUSES`.
    #: ⚠️ Deliberately **not written as a `Literal`**: when a status word outside the
    #: vocabulary shows up in the DB, the correct reaction is that this row is not
    #: dispatched plus an alert, not the whole queue endpoint returning 500 and starving
    #: every worker.
    eval_status: str
    #: On-chain submission time; **queue order recognizes only this**
    #: (`ORDER BY submitted_at ASC`).
    #: Sorting by `created_at` makes the miner pay for a backend failure: after the
    #: 2026-08-14 rescan the queueing basis for uid 221 went from the on-chain 16:56 to
    #: the insertion time 19:36, and it fell straight behind (incident E).
    submitted_at: datetime | None = None
    #: Local insertion time. **Must not be used for sorting / queue-order decisions**;
    #: audit only.
    created_at: datetime | None = None
    #: `hf_commit[:8] or "v0"`. The worker does not read it and the frontend does not
    #: read it; whether the private `validator` repo reads it cannot be verified, so
    #: deletion waits for the evaluation party to confirm (spec 07 §10 Q12), and until
    #: then it is kept as-is.
    task_version: str = "v0"


class QueueResponse(Contract):
    """`{"queue_size": N, "tasks": [...]}`.

    This is **not** the response envelope, it is the shape of the queue resource itself
    (a queue has a size). The worker reads `data["tasks"]` and does not read
    `queue_size`, but do not delete either — adding is safe, changing the shape is not.
    An empty queue is `{"queue_size": 0, "tasks": []}`, not a 404 and not `null`.
    """

    queue_size: int
    tasks: list[QueueTask] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# worker contract group — POST /api/v1/benchmark/task/{task_id}/score
# ─────────────────────────────────────────────────────────────────────────────


class EnvScore(Contract):
    """The score of one suite. **These numbers are real on-chain emissions 40 minutes
    later at the earliest.**

    `base_suite` / `perturbation` must be declared explicitly and **echoed back
    verbatim** — the worker's `remote_score_matches` (`worker.py:592-595`) compares them
    entry by entry, and if Pydantic drops them as undeclared extra fields then local has
    a value while remote is None → the check fails forever → every timeout means POSTing
    again.
    """

    env_name: str
    #: `StrictFloat` rather than `float`: in lax mode `true` is silently converted into
    #: `1.0` (measured), while "a bool is not a number" is the 1st check of production's
    #: `_validate_env_scores`.
    #: JSON integers (`0` / `1`) are still accepted — measured, strict mode lets
    #: int→float through.
    #: **The value range (0~1) is not checked here**, see the last section of the module
    #: docstring; on the write path `check_env_scores()` stops it before parsing.
    score: StrictFloat
    #: Same as above, `StrictInt` rejects `true`. The ≥ 0 check is likewise left to
    #: `check_env_scores()`.
    samples: StrictInt
    duration_sec: float | None = None
    error: str | None = None
    #: The worker sends these when the profile is not `libero_pro_custom_1`.
    base_suite: str | None = None
    perturbation: str | None = None


class ScoreSubmission(Contract):
    """The request body of POST /score = **the shape of the `result` column after it is
    stored** (production stores the body verbatim).

    Two behaviours that must not change; change them and the worker's storage check
    fails forever → every timeout/5xx means POSTing again:

    - **`total_score` is stored verbatim, recomputing it is forbidden.** The worker
      computes it weighted by the profile weights (`libero_plus`'s
      `PLUS_SUITE_TASK_COUNTS` is not equal-weight), so the moment the backend
      recomputes it the two values differ, and the check uses `abs_tol=1e-9`
      (`worker.py:568`). Production's patched `evaluator.py:1370` stores it verbatim ✅,
      `prototype`'s `handle_score:229` recomputes ❌; by ruling order ① production wins.
    - **`env_scores` is echoed back entry by entry, verbatim** (including `base_suite` /
      `perturbation`).

    New fields we do not recognize are always **ignored** (`Contract` keeps pydantic's
    default extra=ignore), never a 422: the evaluation party adding a field should not
    become the button that destroys GPU hours.

    ⚠️ **When reading it back**: `result` in the DB may be `{}` or `""` (not evaluated
    yet). Normalize that case to `None` at the exit; **do not** give `success` /
    `total_score` default values so that `{}` also parses successfully — that would
    conjure a `total_score=0.0` out of nothing.
    """

    #: The semantics are "**the benchmark protocol was executed completely**", not
    #: "success rate > 0".
    #: A plain `bool` is used here rather than `StrictBool`: a bool posing as a number
    #: is the attack surface, while the reverse (`1` posing as `true`) neither changes
    #: the semantics nor has a historical incident.
    success: bool
    #: See the class docstring. Usually 0.0 when `success=false`, still stored verbatim.
    total_score: float
    #: Required when `success=true`, and all 6 suites must be there
    #: (`check_required_envs`).
    env_scores: list[EnvScore] = Field(default_factory=list)
    #: Required when `success=false`.
    error: str | None = None
    #: End-to-end wall clock.
    duration_sec: float | None = None
    #: Identity fields: the backend takes the values in the DB as authoritative
    #: (production `sub.get("hotkey", miner_hotkey)`), and the body is only a fallback.
    #: But the worker's storage check compares them one by one, so they **must be
    #: readable back verbatim**.
    miner_hotkey: str | None = None
    hf_repo_id: str | None = None
    hf_commit: str | None = None
    #: When ≤ 0 the backend falls back to the DB's `round_num`; it does not write 0.
    round_num: int | None = None
    #: `libero` / `libero_pro` / `libero_pro_custom_1` / `libero_plus`.
    #: The worker's check is "missing is tolerable, present must match" — today the
    #: backend does not store it at all, so the check always takes the "old backend,
    #: missing, tolerate" branch. Which profile production actually runs is to be
    #: confirmed (spec 07 §10 Q8).
    benchmark: str | None = None
    #: The seed carried by the queue entry, sent back as confirmation; miners use it to
    #: reproduce the initial state independently.
    #: The public face of the "seed derivation" red line.
    init_seed: int | None = None
    expected_trials_per_task: int | None = None
    #: What production actually sends is `[]` (`prepare_submit_payload()` clears it
    #: deliberately): a full LIBERO-100 has 130 entries, and the backend writing them to
    #: the DB one by one synchronously exceeds the Cloudflare proxy timeout, **reliably
    #: returning 524**.
    per_task_scores: list[dict[str, Any]] = Field(default_factory=list)


class ScoreAccepted(Contract):
    """The response of POST /score. **The worker does not read the response body at
    all**, which makes this shape the lowest-risk one in this group.

    The high-risk part is the status code: when the task is already `superseded` /
    `rejected` it must be **200 plus `ignored=True`** — a 4xx makes the worker drop the
    result (which does not matter), but a 5xx makes it back off and retry forever, stuck
    on this task that will never be valid again and never picking up new work. 200 is
    the only answer that makes it "record this as submitted and carry on".
    """

    task_id: str
    ok: bool = True
    #: The terminal-state guard fired: the whole thing is discarded (no `eval_scores`
    #: write, no status change, no ranking trigger).
    ignored: bool = False
    message: str | None = None


def check_env_scores(env_scores: object) -> None:
    """The four checks at the scoring entry point. They **must** run before the DB
    write, so the task keeps its status and the worker can retry.

    Copied from production `evaluator.py` /
    `handlers/benchmark.py:88 _validate_env_scores`:

    1. `score` must be a number, and **a `bool` does not count**
       (in Python `isinstance(True, int)` is True);
    2. NaN is caught separately — NaN compares False against any number, so
       `0 <= x <= 1` lets it slip through;
    3. `0 <= score <= 1`;
    4. `samples` must be a **non-negative integer** (excluding bool as well).

    ⚠️ **It takes raw JSON, not an `EnvScore`.** Two reasons, neither of them
    fastidiousness:

    - Checks 1 and 2 only hold on values that have **not been converted by the framework
      first**. `EnvScore`'s `StrictFloat` can already stop a bool, but that is the
      second lock, not the first — any path that bypasses the model and reads the body
      directly (writing an audit log first, for example) still needs this one.
    - The value-range checks 3 and 4 are **deliberately not put into the model**: the
      read path has to be able to load historical dirty data such as `score=99.0` back
      without a 500 (see the module docstring).

    The historical cost: `{"score": 99.0, "samples": -5}` plus only 1 suite submitted →
    200 → straight to dethroning the champion and taking 7% of the weight.
    """
    if not isinstance(env_scores, list) or not env_scores:
        raise ContractError(CODE_INVALID_SCORE, "env_scores must be a non-empty list")
    for item in env_scores:
        if not isinstance(item, dict):
            raise ContractError(
                CODE_INVALID_SCORE,
                f"env_score entry must be an object, got {type(item).__name__}",
            )
        env = item.get("env_name", "")
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ContractError(
                CODE_INVALID_SCORE,
                f"invalid score type for {env}: {type(score).__name__}",
            )
        if math.isnan(score):
            raise ContractError(CODE_INVALID_SCORE, f"NaN score for {env}")
        if not 0.0 <= score <= 1.0:
            raise ContractError(
                CODE_INVALID_SCORE, f"score out of range for {env}: {score}"
            )
        samples = item.get("samples", 0)
        if isinstance(samples, bool) or not isinstance(samples, int) or samples < 0:
            raise ContractError(
                CODE_INVALID_SCORE, f"invalid samples for {env}: {samples!r}"
            )


def check_required_envs(env_scores: object) -> None:
    """All 6 required suites must be present; missing one rejects the whole thing.

    **The number is 6, not 4.** The worker's `--benchmark libero` profile produces only
    4 base envs (`profiles.py:14 PRO_BASE_SUITES`): production's 6-env gate rejects it,
    while a 4-env gate would **accept** it — and the mean of 4 suites is not comparable
    with the mean of 6, so mixing them into the same leaderboard is a giveaway. That is
    the entire reason this check exists. The vocabulary comes from
    `constants.REQUIRED_ENVS`, the same source as `LIBERO_TASK_SUITES`.

    A 4-suite result will never become a legal 6-suite result, so retrying is pointless
    — the backend should map this to a 4xx (the worker will go `abandoned`), and that is
    correct.
    """
    names: set[str] = set()
    if isinstance(env_scores, list):
        for item in env_scores:
            if isinstance(item, dict):
                names.add(str(item.get("env_name", "")))
    missing = REQUIRED_ENVS - names
    if missing:
        raise ContractError(
            CODE_MISSING_ENVS, f"missing required envs: {', '.join(sorted(missing))}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# worker contract group — POST /api/benchmark-progress
#                        · POST /api/v1/benchmark/progress
# ─────────────────────────────────────────────────────────────────────────────

#: The canonical public stage words. **Same source as `status.ALL_STAGES`**;
#: `tests/test_schemas.py` pins the two to be equal — this module must not have a second
#: stage vocabulary.
EvalStage = Literal["claimed", "downloading", "prechecking", "running"]

#: The keys that may show up in the progress detail a worker reports. The frontend's
#: `QueueProgressDetail` (`web/src/api/types.ts`) draws the progress bar from exactly
#: these.
#:
#: ⚠️ This tuple is **only used to fold the flattened top-level spelling into
#: `detail`**, it is **not a whitelist** — the `detail` object is passed through whole
#: and verbatim, see `extract_progress_detail`.
PROGRESS_DETAIL_KEYS: Final[tuple[str, ...]] = (
    "suites_done",
    "suites_total",
    "current_suite",
    "last_completed_suite",
    "episodes_done",
    "episodes_total",
    "progress",
    "current_env",
)


def extract_progress_detail(body: dict[str, Any]) -> dict[str, Any]:
    """Extract the progress detail. **The `detail` object is stored whole and verbatim;
    the top level only fills in what is missing.**

    The guard for incident ⑧. The old production code was
    `detail = body.get("detail", {})`, passed through verbatim; the 2026-08-14
    deployment changed it to pick only the two **top-level** keys `progress` /
    `current_env` — and the worker does not send those two top-level fields at all, so
    what went into the DB was forever `{"progress": null, "current_env": null}`.
    Measured comparison: before the deployment the queue page showed
    `7/16 SUITES / libero_goal_lan`, after it only a bare `EVALUATING` was left, **and
    the backend could therefore no longer answer "which step is this task on and how
    much longer".**

    Four verified behaviours (the 8 cases in
    `prototype/tests/test_progress_detail.py`): `detail` is an object → stored whole and
    verbatim; the flattened spelling is supported at the same time, with `detail` taking
    priority; `detail` is not an object / is `None` / is missing → fall back to the top
    level, **without raising**; no progress information at all → the empty object `{}`,
    never again producing an empty shell like
    `{"progress":null,"current_env":null}`.
    """
    raw = body.get("detail")
    detail: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    for key in PROGRESS_DETAIL_KEYS:
        if detail.get(key) is None and body.get(key) is not None:
            detail[key] = body[key]
    return detail


class ProgressUpdate(Contract):
    """The progress report request body. The worker sends only the four fields
    `task_id` / `stage` / `detail` / `worker_id`.

    The normalization (vocabulary aliases plus `detail` extraction) is written as a
    `model_validator(mode="before")`, so **both paths share the same copy**: whichever
    of `/api/benchmark-progress` and `/api/v1/benchmark/progress` takes it first, it is
    impossible for them to each copy their own and then drift apart (spec 07 §5: in the
    new skeleton the two route function bodies are copies of each other right now).

    The backend has two ways to wire it up, both safe:

    - hand `body: ProgressUpdate` straight to FastAPI — an unknown stage goes 422;
    - take the raw dict and then call `ProgressUpdate.from_payload(body)` — an unknown
      stage raises `ContractError(INVALID_STAGE)`, which can be mapped to the 400 nailed
      down in the contract card.
      Progress reporting is best-effort (the worker swallows errors and only logs a
      warning), so **a 400 loses no evaluation result** and using a 4xx here is safe.
    """

    task_id: str
    #: The already-normalized canonical public word. The `benchmark_` prefix is added
    #: only when storing; **neither the response nor this field carries the prefix** —
    #: the frontend's `ACTIVE_STAGES` does not recognize the prefixed spelling.
    stage: EvalStage
    #: **An open dict, deliberately not narrowed.** Picking keys is incident ⑧.
    detail: dict[str, Any] = Field(default_factory=dict)
    worker_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        """Normalize whichever side's spelling into the canonical form.

        - **Both `status` and `stage` are accepted, `status` wins.** The backend has
          always read `status`, while the frontend types (`QueueProgress`) and the
          public docs both call it `stage`, so whoever wrote against those got
          `Unknown status: ""` (actually happened on 2026-08-14). What the worker sends
          today is `stage`.
        - The stage word is normalized through `status.normalize_stage()` (case and
          leading/trailing whitespace are strip/lower-ed first): the documented word
          `running`, the frontend word `precheck`, the worker-internal word
          `evaluating` and the stored form `benchmark_*` in the DB are all accepted —
          accepting one less is a 400, and `_report_progress` is best-effort so the 400
          gets silently swallowed and nobody finds out.
        - Unknown / empty stage → `ContractError(INVALID_STAGE)`. This matches
          production (the old implementation returned 400 for both unknown and empty
          stages); **do not relax it into defaulting to an empty string**: a mistyped
          stage then lands in the DB silently, the frontend cannot render the progress
          bar, and nobody at all will notice.

        Normalization is idempotent: input that is already the canonical word passes
        through unchanged, so a direct construction such as
        `ProgressUpdate(task_id=..., stage="running")` takes the same path.
        """
        if not isinstance(data, dict):
            return data
        body: dict[str, Any] = data
        word = body.get("status")
        if not isinstance(word, str) or not word.strip():
            word = body.get("stage")
        wire = normalize_stage(word) if isinstance(word, str) else None
        if wire is None:
            raise ContractError(CODE_INVALID_STAGE, f"unknown stage: {word!r}")
        return {
            "task_id": str(body.get("task_id", "")),
            "stage": wire,
            "detail": extract_progress_detail(body),
            "worker_id": str(body.get("worker_id", "")),
        }

    @classmethod
    def from_payload(cls, body: dict[str, Any]) -> ProgressUpdate:
        """Parse from raw JSON; **a violation raises `ContractError`, not
        `ValidationError`**.

        The backend needs `.code == "INVALID_STAGE"` to map to a 400, and going through
        pydantic's `ValidationError` buries it inside `ctx`. The normalization itself is
        in `_normalize`; this only restores the exception to the one carrying the stable
        code.
        """
        try:
            return cls.model_validate(body)
        except ValidationError as exc:
            for err in exc.errors():
                inner = err.get("ctx", {}).get("error")
                if isinstance(inner, ContractError):
                    raise inner from exc
            raise


class ProgressAccepted(Contract):
    """The progress report response. The worker does not read it, the frontend reads
    `stage`, old callers read `status`.

    **Both keys are given and hold the same value** — this is exactly the shape of
    ZCY-158: one thing with two field names, and giving only one of them always leaves
    one side with `undefined`.

    `status` is made a `computed_field` rather than a second field: the two disagreeing
    is therefore **unrepresentable at the type level**, so no validation is needed to
    prevent it (and there is no "who bypassed that validation" either).
    """

    task_id: str
    stage: EvalStage
    success: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> EvalStage:
        """Always identical to `stage`. Old callers read this key; deleting it is
        forbidden.
        """
        return self.stage


# ─────────────────────────────────────────────────────────────────────────────
# worker contract group — GET /api/submission/{task_id}
# ─────────────────────────────────────────────────────────────────────────────


class SubmissionRecord(Contract):
    """The worker uses it to decide "did that 8-hour evaluation result actually get
    stored". **The highest-risk response in this group.**

    `worker.py:530 remote_score_matches` checks it field by field, and if any one item
    is not satisfied it rules "not stored" → after backing off it **POSTs the same
    scores again** → hits the terminal-state guard / the unique index → 500 → checks
    again → retries again.

    🔴 **A confirmed silent failure**: the worker only accepts
    `status ∈ {"done","scored","failed"}`, while the backend now writes `evaluated` /
    `eval_failed`, and since 0002 `done` has been forbidden by a CHECK.
    That `unified_to_legacy_score()` in `protocol/status.py` writes the conversion but
    is called from nowhere. The inference is that the check is **currently always
    False**, and there is no alert anywhere on this path (the worker only logs a
    warning, and that log is on the evaluation party's machine). **No conversion is done
    here, deliberately** — both fixes (the backend converting to `done` at the exit /
    asking the evaluation party to accept `evaluated`) need a human ruling, see spec 07
    §10 Q2.
    ⚠️ When this lands, **keep a conversion function and mark it TODO**; do not hide the
    vocabulary disagreement inside a query.

    When not found it returns **200 plus `{}`**, not a 404: for the worker a 404 is
    permanent and would let it take "the task does not exist" as a reason to abandon a
    possibly valid result.
    """

    task_id: str
    #: ⚠️ See the class docstring. The values today are the words in
    #: `status.ALL_STATUSES`.
    #: Same as `QueueTask.eval_status`, deliberately not written as a `Literal` — a 5xx
    #: is the "write the DB twice" button.
    status: str
    #: `hotkey` and `miner_hotkey` are both written: the worker accepts either, and
    #: production rows call it `hotkey`. **Neither of them may be deleted.**
    hotkey: str
    miner_hotkey: str
    hf_repo_id: str
    hf_commit: str
    round_num: int
    #: The stored evaluation result = the body as it was at POST time. `None` when not
    #: evaluated yet (the DB holds `{}` or `""` — normalized to `null` at the exit, so
    #: the worker's check returns False, which is the **correct** result: it really was
    #: not stored).
    #: The worker accepts either encoding (dict or JSON string); the exit always gives
    #: an object.
    result: ScoreSubmission | None = None
    reason: Reason | None = None


#: The only three words the worker's storage check accepts (`worker.py:553`).
#: This is **only a record** here, no conversion is done — see the TODO in the function
#: below.
WORKER_ACCEPTED_STATUSES: Final[frozenset[str]] = frozenset(
    {"done", "scored", "failed"}
)


def worker_status_alias(status: str) -> str:
    """Canonical status word → the old word the worker knows. **⚠️ Nothing calls this
    today, and that is deliberate.**

    TODO(wire up after the ruling): which set of words the `status` of
    `GET /api/submission/{task_id}` actually emits is a **blocking open question**
    (spec 07 §10 Q2), and both roads need a human ruling:

    (a) The backend calls this function at that endpoint's exit and emits
        `evaluated → done`. That is a fix for the worker and a breaking change for
        everyone else reading this endpoint.
    (b) Ask the evaluation party to add `evaluated` / `eval_failed` on the worker side.
        That needs their cooperation, and `SCOPE.md` states "we do not decide their
        integration schedule for them".

    Before the ruling, `SubmissionRecord.status` **emits the canonical word from the DB
    verbatim**. This function sits here instead of being wired secretly into a query so
    that the vocabulary disagreement stays out in the open — the lesson of ZCY-158 is
    that the translation table got hidden inside the consumer (the worker's
    `_PROGRESS_STAGE_MAP` is still there today), so nobody knew the two sides did not
    actually match.

    Background (inferred, not measured): the worker's check is **currently always
    False**. The backend now writes `evaluated` / `eval_failed`, and since 0002 `done`
    has been forbidden by a CHECK. So every POST /score timeout or 5xx walks the full
    retry path, and there is no alert anywhere on this path — the worker only calls
    `logger.warning`, and that log is on the evaluation party's machine.

    Status words not in the table are **returned verbatim**, the same way
    `status.normalize_status()` does it: this function is not responsible for judging
    legality, that is `ALL_STATUSES`'s job.
    """
    return {"evaluated": "done", "eval_failed": "failed"}.get(status, status)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/benchmark/meta
# ─────────────────────────────────────────────────────────────────────────────


class BenchmarkSpec(Contract):
    """⚠️ `tasks_per_round` / `trials_per_task` are **hardcoded constants for public
    display, and are already inconsistent with what the worker actually runs** (the
    worker profile has `expected_task_count=160`, and `--num-trials` is given by ops).
    They only feed the frontend display and are not on the money path; changing the
    values requires aligning the frontend and the evaluation party at the same time, see
    spec 07 §10 Q9 — until then **do not change the values**.
    """

    suite: str
    tasks_per_round: int
    trials_per_task: int
    sim_engine: str
    timestep_ms: int
    control: str
    observations: str


class BenchmarkMeta(Contract):
    """Pure static constants, zero DB access, **genuinely anonymous** (measured: even
    carrying an arbitrary wrong key still gets 200).

    The `-H "X-API-Key: ***"` written in the docs at `api_reference_en.md:381` is a
    documentation error; by ruling order ① production behaviour is authoritative.
    """

    name: str
    version: str
    phase: str
    #: **A manually maintained release time**, not a record update time — keep it a
    #: string literal, do not wire it to `now()`.
    updated_at: str
    maintainer: str
    spec: BenchmarkSpec


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/queue/status
# ─────────────────────────────────────────────────────────────────────────────


class QueueSummary(Contract):
    """Whole-table status counts.

    ⚠️ Two measured facts:

    - **The `superseded` bucket is new; without it the total can never match
      history's**: live, history has 117 rows and queue tasks 85, and the 32 missing
      ones are exactly the superseded ones.
    - **The counting must use `+=`, it must not use assignment.** The mechanism of
      ZCY-130: `GROUP BY` produced two rows, `done` and `evaluated`, both mapping to
      `evaluated`, and the one arriving later **overwrote** the earlier one,
      undercounting by 45 rows — with not a single line of log. The CHECK in 0002 put it
      to sleep, but **the contract does not rely on the CHECK as a backstop**.

    `received` / `burn_checking` / `burn_passed` / `seed_failed` have **no bucket**
    today (the live summary has only these 5 statuses) — they land in `unknown`. That is
    intentional: on a status word outside the buckets, log `logger.error` and count it
    into `unknown`; **discarding it silently is exactly the lesson of ZCY-130**.
    """

    pending: int = 0
    evaluating: int = 0
    evaluated: int = 0
    eval_failed: int = 0
    rejected: int = 0
    superseded: int = 0
    #: Where status words outside the buckets go. Always 0 is normal; non-zero means the
    #: state machine missed a word and it should alert.
    unknown: int = 0
    total: int = 0


#: The field names in `QueueSummary` that represent status buckets. Their correspondence
#: with `status.ALL_STATUSES` is pinned by `tests/test_schemas.py` — one bucket more or
#: one name changed turns red there.
QUEUE_SUMMARY_BUCKETS: Final[tuple[str, ...]] = (
    "pending",
    "evaluating",
    "evaluated",
    "eval_failed",
    "rejected",
    "superseded",
)


class QueueStatusTask(Contract):
    """One task in the queue (for the frontend / a miner's curl).

    ⚠️ The field name is **`eval_status`**, not `status`: what production returns is
    `eval_status`, and the miner docs and their curl commands are written against it.
    Renaming has zero upside, and the risk is miners' scripts silently getting
    `undefined`. (What the **column** in the DB is called is another matter, and is not
    yet confirmed — spec 06 §7 Q1.)
    """

    task_id: str
    hotkey: str
    uid: int
    eval_status: str
    burn_status: str
    #: The on-chain block. A sort key; rows with `commit_block = 0` (measured, they
    #: exist) must fall back to `commit_block_timestamp`, and **must not fall back to
    #: `created_at`**.
    commit_block: int
    burn_block: int
    hf_repo_id: str
    hf_commit: str
    submitted_at: datetime | None = None
    #: Already selected in the SQL but not put into the live response; the contract
    #: requires filling it in.
    #:
    #: ⚠️ **No default value, required.** Every task in the queue **necessarily belongs
    #: to some round** — "we do not know which round" is not a legal state, and
    #: `round_num=0` is even less so: round 0 does not exist, and 0 would be taken by
    #: the frontend and by miners' curl commands as a real round to filter on, silently
    #: fetching back an empty list. The production column is `NOT NULL`, the backend
    #: always fills it, and in the 2026-08-19 copy 0 of 119 rows are 0 — this default
    #: value could not fire on a single row, and keeping it would only make "forgot to
    #: fill it in" representable.
    round_num: int
    reason: Reason | None = None
    #: The progress bar data. The contract card calls it "progress", but in history the
    #: same data is called `detail` — one thing with two names is exactly what this file
    #: exists to eliminate, so it is uniformly called `detail`.
    stage: str | None = None
    detail: dict[str, Any] | None = None
    #: ⚠️ The two below **only appear when `eval_status == "pending"`, and as a "missing
    #: key" rather than as `null`** (measured live; the frontend's `types.ts:146` is
    #: written as optional).
    #: Serialization must use `exclude_none` (write `response_model_exclude_none=True`
    #: on the FastAPI route), otherwise non-pending rows grow two extra `null` keys.
    queue_position: int | None = None
    #: ⚠️ **The unit is undecided**: the new skeleton calls it `EVAL_SECONDS`, the old
    #: implementation calls it `EVAL_TIME_PER_TASK_MIN`; both values are 90, one says
    #: seconds and the other says minutes, a factor of 60 apart. Meanwhile a single
    #: evaluation measured live reaches `duration_sec` 6111 seconds ≈ 102 minutes, so
    #: **neither constant is right**. See spec 06 §7 Q8; do not pick a unit for it in
    #: the implementation before that is ruled on.
    evaltime: int | None = None


class QueueStatusResponse(Contract):
    """`{summary, tasks}`. An empty queue is summary all zeros plus `tasks: []`, not a
    404 and not `{}`.
    """

    summary: QueueSummary
    tasks: list[QueueStatusTask] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/submissions/history
# ─────────────────────────────────────────────────────────────────────────────


class SubmissionHistoryItem(Contract):
    """One submission. **The real data source** of the queue page and the leaderboard
    page (queue/status is just a status map).

    The whitelist comes from the 33 keys measured live, minus 5, plus `reason`:

    - Dropped `legacy_task_id` / `repo_hash` / `hotkey_tag` / `worker_id`: internal
      fields. `repo_hash` is the **model fingerprint of the plagiarism judgement**, and
      publishing it hands over the basis of that judgement.
    - Dropped `eval_detail`: `detail` is already its parsed version, and the two
      duplicates took up a large chunk of the response body.
    - **Never return `status`**: the frontend's `normalizeHistoryStatus()` is written as
      `submission.status || submission.eval_status || …`, i.e. it **reads `status`
      first** — with two status keys in the same response, the one read first happens to
      be the un-normalized one, and that is how 33 of 95 rows showed the wrong status
      (measured on the 2026-08-19 copy: the two production sources disagree on 80 rows).
      This response is allowed **one** status key only. The old implementation only
      escaped by doing `row.pop("status", None)` at the exit; here it is guaranteed by
      "the model does not have that field", and pinned by `tests/test_schemas.py`.

    Three fields **not to delete on a whim**: `result` (the frontend's
    `normalizeHistoryStatus` reads `result?.success` **before** it reads the status
    word, so deleting it crashes status rendering outright), `stage` (the only data
    source of the progress bar) and `detail` (`suites_done/suites_total`; the whole file
    `test_progress_detail.py` exists to guard it).
    """

    id: int
    task_id: str
    uid: int
    hotkey: str
    round_num: int
    hf_repo_id: str
    hf_commit: str
    #: The on-chain block and the **on-chain time (Unix seconds, integer)**. The paging
    #: order must be a total order (`commit_block DESC, id DESC`); a single-key sort
    #: duplicates or drops rows when values tie.
    commit_block: int
    commit_block_timestamp: int
    burn_tx_hash: str
    burn_block: int
    burn_status: str
    block_hash: str
    eval_status: str
    #: An array, **not a JSON string** (0002 already converted the column to jsonb).
    env_list: list[str] = Field(default_factory=list)
    burn_amount_tao: float | None = None
    #: The stored evaluation result, verbatim. `{}` / `""` in the DB are normalized to
    #: `null` at the exit.
    result: ScoreSubmission | None = None
    #: The progress detail (the parsed version of `eval_detail`). An open dict, see
    #: `extract_progress_detail`.
    detail: dict[str, Any] | None = None
    #: Kept as-is — reading it is what the miner docs teach. `reason` is an addition,
    #: not a replacement.
    reject_reason: str = ""
    #: The three pieces of the seed dispatch. ⚠️ **Never having dispatched a seed is
    #: `null`, not 0 and not an empty string**, and the three fields must be present
    #: together or absent together — otherwise we emit a response like
    #: `{seed: null, drand_random: "", drand_round: 0}` where one field says "there is
    #: none" and its neighbour says "there is one".
    #:
    #: Why 0 will not do — `drand_round` is the sharpest of them: drand's official API
    #: returns **HTTP 200** for `/public/0`, and the content is **the latest round of
    #: that day** (an alias of `latest`, measured 2026-08-19). An auditor taking the
    #: `drand_round: 0` we published and looking it up gets no error and no 404; they
    #: get a beacon from today, and then `verify_seed()` is necessarily False — and they
    #: have no way to judge whether we cheated or the data is missing. This package's
    #: `seed.drand_round_url()` already `raise`s outright for `<= 0`, so emitting 0 here
    #: as a default value would contradict that.
    #:
    #: `seed` is the same thing and even more insidious: 0 is a **legal output** of
    #: `derive_seed()` (`int.from_bytes(sha256[-4:])`, probability 1/2³²), so "0 must be
    #: fake" cannot tell them apart. In the 2026-08-19 production copy all 20 rows with
    #: `seed=0` are "never dispatched a seed", and 11 of them have already been scored
    #: and entered the leaderboard — their evaluation results are not reproducible, and
    #: the response does not show it.
    #:
    #: 🔴 **The constraint is added only to the two where it can be; `seed`
    #: deliberately has none.**
    #: Removing the default value only changes "what is given when the field is
    #: omitted", it **does not reject an explicitly passed 0** — and the party that
    #: actually put 0 on the wire is production (the backend repository layer once wrote
    #: `seed=m.seed or 0`, actively erasing NULL into 0). So this has to be a
    #: **constraint**, not a default value, otherwise everything this whole comment
    #: describes goes uncaught while merely looking as if it were caught.
    #:
    #: `seed` cannot take `gt=0`: 0 is a **legal output** of `derive_seed()`
    #: (`int.from_bytes(sha256[-4:])` has 0 in its range, probability 1/2³²). Adding a
    #: constraint to it means rejecting a real seed one day, and that submission would
    #: then never make it onto the leaderboard — more expensive than what it guards
    #: against. `seed` relies on the triple check below instead: a 0 appearing on its
    #: own while the other two are None is rejected.
    seed: int | None = None
    drand_random: Annotated[str, Field(min_length=1)] | None = None
    drand_round: Annotated[int, Field(gt=0)] | None = None
    #: ⚠️ The plagiarism fingerprint. Whether it should stay public awaits a product
    #: judgement (spec 06 §7 Q5); before that ruling keep the live status quo (return
    #: it), and **do not delete it on a whim, nor add `repo_hash` on a whim**.
    model_hash: str = ""
    #: Measured live: always `null` while `result.total_score` has a value — two score
    #: fields, one true and one false.
    avg_score: float | None = None
    stage: str = ""
    submitted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def _seed_triple_is_all_or_nothing(self) -> SubmissionHistoryItem:
        """The seed triple must be **present together or absent together**.

        This is `seed`'s only way of being guarded — it cannot take `gt=0` the way
        `drand_round` can (0 is a legal output of `derive_seed()`), so the only way to
        judge it is its consistency with its companions.

        What it stops is this shape: `{seed: 0, drand_round: null, drand_random: null}`.
        Looking at `seed` alone you cannot tell "the seed really is 0" from "no seed was
        ever dispatched", but together with the other two you can: **the time a seed
        really was dispatched, all three fields necessarily have values at once** (the
        inputs of `derive_seed` are exactly block_hash + round + drand_random, and with
        any one of them missing it cannot be computed).

        In the 2026-08-19 production copy all 20 rows with `seed=0` have this shape, and
        11 of them have already been scored and entered the leaderboard — their
        evaluation results are not reproducible, and the response does not show it. This
        check makes those 11 blow up at the moment of serialization, instead of being
        found only when an auditor goes and looks drand up.
        """
        present = [
            self.seed is not None,
            self.drand_random is not None,
            self.drand_round is not None,
        ]
        if any(present) and not all(present):
            missing = [
                name
                for name, ok in zip(
                    ("seed", "drand_random", "drand_round"), present, strict=True
                )
                if not ok
            ]
            raise ValueError(
                f"incomplete seed triple: {missing} missing. "
                "The fields that are set claim a seed was derived; the ones that "
                "are absent claim it was not. derive_seed() needs all three of "
                "block_hash, round and drand_random, so this record cannot be "
                "reproduced."
            )
        return self


class SubmissionHistoryResponse(Contract):
    """`{success, submissions, total, limit, offset}`.

    The historical `success` wrapper **stays untouched** (the frontend's `types.ts:182`
    is already written hard against it), but **it is not added to any new endpoint any
    more**. `limit` / `offset` are being filled in (`scan-rejections` already has them,
    and the wrappers of the three list endpoints should be the same; the frontend
    already marks them optional, so adding them breaks nothing).

    ⚠️ `total` is the **total after filtering**, not the number of rows on this page.
    The new skeleton's `total=len(page)` is wrong: the frontend computes the page count
    from `total`, and the row count of this page makes the page number forever 1.
    """

    submissions: list[SubmissionHistoryItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
    success: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/submissions/{submission_id} (including /score.json)
# ─────────────────────────────────────────────────────────────────────────────


class PerTaskScore(Contract):
    """⚠️ **`task_id` here is an `env_name`** (`libero_spatial` etc.), not the
    submission's task_id.

    Within the same response `task_id` has two meanings. The field name is kept (the
    frontend is already written against it), but this sentence must go into the OpenAPI
    description.
    """

    task_id: str
    success_rate: float
    #: The episode count (measured 2000 / 500). **A different dimension** from the outer
    #: `ScoreStat.trials` (the suite count, measured 6); both being called trials is an
    #: accomplished fact.
    trials: int


class EvalEnvironment(Contract):
    env_hash: str = ""
    #: ⚠️ The old implementation hardcoded `"mujoco-3.2.1"`, which turns into a lie as
    #: the evaluation environment is upgraded.
    #: Either take it from metadata or delete the field — not ruled on, so keep the
    #: status quo and do not hardcode it a second time.
    sim: str = ""
    eval_commit: str = ""
    #: ⚠️ The same thing as `SubmissionHistoryItem.seed`: never having dispatched a seed
    #: is `null`, not 0. This field is the auditor's input for reproducing the
    #: evaluation, and a `0` would be run as given and produce a different result
    #: without raising anything.
    #: The outer `SubmissionDetail.environment` is already `| None`, and only the inner
    #: one following suit makes it self-consistent.
    seed: int | None = None


class SubmissionArtifacts(Contract):
    score_json_url: str = ""
    logs_url: str = ""


class SubmissionDetail(Contract):
    """The detail of one submission. Three frontend pages pull it on demand, and the
    `/score.json` suffix returns a byte-for-byte identical response.

    **`eval_status` emits the canonical word from the DB, with no display-state
    mapping.** The old implementation's `_SM_STATUS` (`frontend.py:30-32`) mapped
    `eval_failed → "evaluating"` and `rejected → "evaluating"` — for a rejected
    submission, the detail endpoint told you it was "being evaluated".
    **This is the backend half of ZCY-150.**

    Not found → **404 `SUBMISSION_NOT_FOUND`**, not 200 plus `{}`: `{}` cannot be
    distinguished from "exists but has not finished running", so a miner cannot tell "I
    mistyped the ID" from "it has not been scored yet", and it also pollutes the
    15-second cache and the monitoring (the error rate is forever 0).
    """

    submission_id: str
    round_id: int
    miner: MinerRef
    model: ModelRef
    eval_status: str
    #: The `MAX(eval_scores.evaluated_at)` of the task that produced the score (the
    #: database is authoritative).
    #: **Do not take `result.timestamp`** — that is a timestamp the worker process wrote
    #: itself, and clock drift or a wrongly written timezone goes straight into the
    #: response.
    scored_at: datetime | None = None
    submitted_at: datetime | None = None
    #: `mean` is aggregated from `eval_scores`. When it disagrees with
    #: `result.total_score`, log `logger.error` and **take the DB as authoritative**
    #: (today nothing anywhere would notice the two not matching).
    score: ScoreStat | None = None
    per_task: list[PerTaskScore] = Field(default_factory=list)
    environment: EvalEnvironment | None = None
    artifacts: SubmissionArtifacts | None = None
    reason: Reason | None = None


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/scan-rejections
# ─────────────────────────────────────────────────────────────────────────────


class ScanRejection(Contract):
    """One rejection record. **"No API key required" was promised to miners in black and
    white**; hanging a key on it breaks that promise.

    Implementation-wise it = an alias view of
    `submissions WHERE eval_status='rejected'` (the `scan_rejections` table is no longer
    written, the chain scanner only logs). **Do not write a separate set of SQL for it**
    — the old implementation did, and as a result the `limit` validation, the ordering
    and the field whitelist each had to be fixed in three places.

    ⚠️ Production **does not return `id`** (the new skeleton added one); it is not added
    here either.
    """

    uid: int
    hotkey: str
    round_num: int
    hf_commit: str
    hf_repo_id: str
    commit_block: int
    burn_tx_hash: str
    burn_block: int
    #: Unix seconds, integer (coexisting with the ISO string `created_at` in the same
    #: response; two encodings is an accomplished fact).
    commit_block_timestamp: int
    task_id: str
    #: **An empty string is not allowed** — a miner who burned TAO must get a reason;
    #: a bare `"invalid"` / `"error"` is not allowed either.
    reject_reason: str
    created_at: datetime | None = None
    reason: Reason | None = None


class ScanRejectionsResponse(Contract):
    """The most complete wrapper among the four public list endpoints; **take it as the
    reference and unify the other two to it**.
    """

    rejections: list[ScanRejection] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
    success: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/leaderboard
# ─────────────────────────────────────────────────────────────────────────────

#: The values of the leaderboard `status`: **it carries the leaderboard-position role
#: only, one vocabulary.**
#:
#: `rank == 1 → "champion"`, `rank >= 2 → "scored"`. Rows that can enter the leaderboard
#: are by definition "all 6 environment scores present and evaluation finished", so the
#: lifecycle value is constant and carries zero information — while production, because
#: of one `LIMIT 1` without an `ORDER BY`, randomly mixes `evaluating` in, and the same
#: data gives a different word on two requests. The literals must not be changed: the
#: frontend union type and the CSS classes (`.bench-pill--champion`) are both written
#: hard against them, and this value is **rendered directly as user-visible text**.
#:
#: `challenger` / `eliminated` (the current values mocked by the new skeleton) must go:
#: they are not in the frontend union type, and `eliminated` does not even hold up
#: semantically — a miner whose challenge failed **is not in rows at all**
#: (King-of-the-Hill is not "ranked further down").
LeaderboardStatus = Literal["champion", "scored"]

#: The values of the round `status`: **it carries the round lifecycle only.**
#:
#: Production computes it as "the mapping of the eval_status of the latest submission of
#: that round", and the consequence is that the round status flips back and forth
#: following the last submission: measured 2026-08-17, `/rounds/current` returned
#: `settled` while at the same moment `submission_count=117` was still growing and the
#: champion had just changed at 09:33 — **a round that is still running declaring itself
#: settled to the outside world.** The round status is a property of the round, not a
#: property of some submission.
#: (There is no defensible definition for a third `scoring` state, see spec 04 §9 Q2; it
#: does not enter the vocabulary before that is ruled on.)
RoundStatus = Literal["live", "settled"]

#: The set forms, same source as the two `Literal`s above. Tests use them to assert that
#: **these two vocabularies have zero overlap with the lifecycle words** — "one
#: response, one vocabulary" is one of the reasons this module exists.
LEADERBOARD_STATUSES: Final[frozenset[str]] = frozenset(get_args(LeaderboardStatus))
ROUND_STATUSES: Final[frozenset[str]] = frozenset(get_args(RoundStatus))


class TasksPassed(Contract):
    """Counts **only the environment scores of the task that produced the score**:
    `passed = count(score >= 0.5)`, `total = 6`.

    Production counts the number of rows across **all attempts** of that miner in that
    round; measured, uid 218 shows `12/12` while other rows in the same table show `6/6`
    — two dimensions in one column. `total` must not fall back to 40 either (that is
    `benchmark/meta.tasks_per_round`, which is not the same thing as the 6 suites).
    """

    passed: int = 0
    total: int = 0


class LeaderboardAudit(Contract):
    #: `"/api/v1/submissions/{submission_id}/score.json"`, following the corrected
    #: `submission_id`.
    score_json_url: str = ""
    #: An empty string when there is none, **do not fill in a fake value**.
    logs_url: str = ""
    env_hash: str = ""


class LeaderboardRow(Contract):
    """One leaderboard row. **Every displayed field is taken from "the task that
    produced the score"** (invariant 6).

    Production fills `submission_id` with the task_id of "the latest submission" by
    hotkey, a defect of the same type as incident C: when a miner submitted 3 times and
    the 2nd one is what scored, what is displayed is the commit of the 3rd.
    """

    rank: int
    #: The round this row belongs to. Production has it and the new skeleton's model
    #: missed it — deleting it makes whoever reads it get undefined.
    round_num: int
    #: The `task_id` of the task that produced the score. **Falling back to building
    #: `task_{hotkey}_r{round}_v1` is forbidden**: the hardcoded `_v1` does not match
    #: the real attempt number, and the audit link then points at a submission that does
    #: not exist.
    submission_id: str
    miner_uid: int
    miner: MinerRef
    model: ModelRef
    score: ScoreStat
    delta_vs_base: float
    tasks_passed: TasksPassed
    #: Carries the leaderboard-position role only. A lifecycle word landing here is
    #: rejected outright by the `Literal`.
    status: LeaderboardStatus
    audit: LeaderboardAudit
    #: The **on-chain submission time** of the task that produced the score. What
    #: production fills in is `MAX(evaluated_at)` (the evaluation completion time), so
    #: the field name is lying — the frontend table header says `Submitted (UTC)`, while
    #: the same submission has another value under `/submissions/{id}` (measured 2 hours
    #: 16 minutes apart).
    submitted_at: datetime | None = None
    #: New: the `MAX(evaluated_at)` of the task that produced the score. It is added so
    #: that no information is lost once `submitted_at` is put back in its place (pure
    #: addition, old callers are unaffected).
    scored_at: datetime | None = None


class Baseline(Contract):
    """The baseline model. **Same source and same value** as `round.base_model` of
    `/rounds/current`.
    """

    model_name: str
    hf_repo: str
    score: ScoreStat
    #: ⚠️ The same thing as `ModelRef.revision`: nobody has ever pinned a commit for the
    #: baseline model, so that is "no value", not "the empty string as a value". `""`
    #: built into an HF URL jumps to the default branch — change the baseline model and
    #: the historical leaderboard quietly points at the new weights.
    #: `None` = no commit pinned. **An empty string will not do**: when the frontend
    #: builds `huggingface.co/{repo}/tree/{revision}`, `""` silently lands on the
    #: default branch (it looks fine, but points at a different piece of code), while
    #: `None` is at least a loud 404.
    revision: Annotated[str, Field(min_length=1)] | None = None


class LeaderboardResponse(Contract):
    """The top-level keys are exactly these 5. A round that does not exist returns empty
    rows, **not a 404**.
    """

    round_id: int
    #: Server-side UTC instant, **the only field allowed to vary between calls**
    #: (invariant 4: everything else is idempotent field by field).
    generated_at: datetime
    baseline: Baseline
    #: The total number of rows on that round's leaderboard (after filtering), unrelated
    #: to `limit` / `offset`.
    total: int = 0
    rows: list[LeaderboardRow] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/rounds/current · GET /api/v1/rounds
# ─────────────────────────────────────────────────────────────────────────────


class Champion(Contract):
    """The current champion = the projection of leaderboard rank 1. The whole object is
    `null` when nobody is on the leaderboard.
    """

    miner_hotkey: str
    miner_name: str
    model_name: str
    #: **Must equal the `score.mean` of `/leaderboard` rank 1** (cross-endpoint
    #: consistency assertion 2).
    score: float
    #: This round's leading score − the champion score of **the previous round (id-1,
    #: the earlier one)**. Production makes `prev` point at the newer round inside a
    #: DESC iteration, so every sign is flipped (frontend measured `-0.029`, should be
    #: `+0.026`).
    delta_vs_prev_champion: float | None = None
    #: The `MAX(evaluated_at)` of the task that produced the score.
    settled_at: datetime | None = None
    #: Always `True`; it means "incumbent", **not "how long they have held it"**.
    held: bool = True


class RoundSummaryEntry(Contract):
    """One entry in the round list."""

    id: int
    #: `f"Round {id:02d}"`, and `f"G{id:02d}"` when `id >= 100`. Do not change it, the
    #: frontend history table displays it directly.
    label: str
    status: RoundStatus
    champion: Champion | None = None


class RoundDetail(Contract):
    """The detail of the current round.

    `round.id` comes from `settings.CURRENT_ROUND` (whose only source is
    `backend.yaml`); **control.json is not read** (ADR 01).
    """

    id: int
    label: str
    status: RoundStatus
    network: str
    base_model: ModelRef
    #: The number of `submissions` rows in that round, **including rejected /
    #: superseded** (measured 117, while the leaderboard's `total=3` — the two numbers
    #: are not the same thing, and the frontend displays them separately).
    submission_count: int = 0
    #: There is no rounds table, so it cannot be made up → always `null`. **Do not pad
    #: it with local time.**
    started_at: datetime | None = None
    ends_at: datetime | None = None
    champion: Champion | None = None


class CurrentRoundResponse(Contract):
    """`{"round": {...}}`.

    This is a data structure, not an envelope, and the frontend is written hard against
    it — **do not "optimize" it into a bare object**.
    When the whole DB is empty it is `{"round": null}` plus 200, **not**
    `{"error": "no rounds found"}` plus 200 (using 200 to express failure has already
    forced the frontend to normalize at the boundary), and **not** a 404.
    """

    round: RoundDetail | None = None


class RoundsSummary(Contract):
    """⚠️ Both fields are **whole-set figures and do not vary with `limit`**. Production
    counts within the returned page, so changing `limit` changes the summary numbers.

    `cumulative_improvement = the champion score of the latest settled round − the
    champion score of the earliest round`, and **a forward improvement is a positive
    number**; with fewer than 2 rounds it is `0.0`. Production writes
    `scores[-1] - scores[0]` while the sequence is DESC, which equals "earliest −
    latest", so the sign is flipped just the same (frontend measured `-0.122`, should be
    `+0.122`).
    """

    rounds_settled: int = 0
    cumulative_improvement: float = 0.0


class RoundHistoryResponse(Contract):
    """`{summary, rounds, total}`, with `rounds` in **descending** order by `id`.

    `rounds` must contain **the current round, even with zero submissions** — production
    only takes `SELECT DISTINCT round_num FROM submissions`, so a freshly opened round
    with nobody having submitted yet does not appear, while `/rounds/current` does have
    it: the two endpoints contradict each other.
    """

    summary: RoundsSummary
    rounds: list[RoundSummaryEntry] = Field(default_factory=list)
    total: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/weights — the exit where the money leaves
# ─────────────────────────────────────────────────────────────────────────────

#: `{hotkey: share}`, and **the share is a 0~1 float, not a u16 integer**.
#:
#: Measured live (2026-08-17):
#: `{burn_hotkey: 0.9, …: 0.07, …: 0.02, …: 0.01}`, summing to 1.
#: The u16 normalization happens **in the caller**:
#: `openroboto-cli/validator.py:normalize_weights()` converts it into `[0, 65535]` and
#: then calls `set_weights` (on-chain snapshot 122:
#: `0.9→58981, 0.07→4587, 0.02→1310, 0.01→655`).
#:
#: 🔴 The only reason this alias exists: the new skeleton's `legacy.py:244` wrote the
#: response model as `dict[str, int]`. The moment it is connected to real data, Pydantic
#: taking `0.9` to satisfy `int` raises `ResponseValidationError` → 500 → the external
#: validator's `fetch_weights()` swallows the exception into `{}` →
#: `normalize_weights({}, uids)` returns an empty list → **set_weights cannot be sent,
#: emissions across the whole subnet stall, and there is only one warning line in the
#: logs.**
#: The key set is ⊆ `{burn_hotkey} ∪ the top three of the leaderboard`, with length ≤ 4
#: (invariant 5).
Weights = dict[str, float]


# ─────────────────────────────────────────────────────────────────────────────
# Ops probes — GET /healthz · GET /readyz
# ─────────────────────────────────────────────────────────────────────────────


class LivenessResponse(Contract):
    """The liveness probe. **It touches no external dependency.**

    Mix a DB check in → the DB hiccups → the probe fails → the process manager decides
    the process is dead → restart → the connection pool is rebuilt → another hiccup →
    another restart, and the flakiness is amplified into a crash loop (the `owner`
    process crashed 74 times in 5 days with nobody noticing). A DB problem should
    **take the instance out of traffic** (`/readyz` 503), not restart the process.

    The key set is exactly these three. ⚠️ `round` / `netuid` are **an echo of this
    process's configuration**, not domain truth — consumers **must not** use them as the
    current round (that one is at `GET /api/v1/rounds/current`).
    They are kept so that even while it is running you can see at a glance which
    configuration the process actually loaded.

    The `timestamp` the old `/health` returned (naive UTC, no timezone suffix) **must
    not be added back**: a probe does not need a timestamp, and carrying one with
    unclear semantics is exactly the entry point of "on-chain time vs local time mixed
    up".
    """

    round: int
    netuid: int
    status: Literal["ok"] = "ok"


class ReadinessCheck(Contract):
    """`detail` **only has a value on failure, and contains no connection string** —
    exception details go to the logs, the response uses fixed wording.
    """

    ok: bool
    detail: str | None = None


class ReadinessResponse(Contract):
    """The readiness probe. Ready → 200 / any item not ok → 503, and **the body shape is
    completely identical in both cases** (a caller should not have to prepare a second
    parser for failure); it also does not go through the unified error handler.

    `migration` compares the alembic version and is a direct product of the 2026-08-14
    index drift incident: production's `idx_submission_pending` was built on the
    abandoned `status` column while the code was making room by `eval_status`, and
    `CREATE INDEX IF NOT EXISTS` **silently skips** an index with the same name, so it
    was mismatched for a whole day, until 9 miners could not insert submissions.

    ⚠️ The key set is exactly these four (pinned by the contract stability test). So
    "the expected version cannot be obtained" **cannot be expressed by adding a
    top-level `expected_head` key** — it has to show up in `migration.ok` /
    `migration.detail`. Today's implementation skips the comparison when it cannot be
    obtained and leaves `migration.ok` as `True`: **this check, added because of an
    incident, switches itself off exactly when trouble is most likely, and the response
    does not show it at all.**
    """

    ready: bool
    database: ReadinessCheck
    migration: ReadinessCheck
    alembic_version: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Vocabulary self-check
# ─────────────────────────────────────────────────────────────────────────────

#: The fields whose value range comes from `status.ALL_STATUSES` (`model, field name`).
#:
#: These fields are **deliberately annotated as `str` rather than `Literal`**: they hold
#: lifecycle status words read out of the DB, and when a value outside the vocabulary
#: shows up the correct reaction is an alert plus degrading that one row, **not the
#: whole endpoint returning 500** — for a worker a 5xx is the "write the DB twice"
#: button (spec 07 §0.3).
#: `tests/test_schemas.py` checks this table one by one: the field really exists and
#: really is a `str`, and the other three vocabularies in this module (leaderboard
#: position / round / stage) have zero overlap with the lifecycle words, preventing
#: anyone from quietly introducing a fifth vocabulary on some model.
STATUS_VALUED_FIELDS: Final[tuple[tuple[type[Contract], str], ...]] = (
    (QueueTask, "eval_status"),
    (QueueStatusTask, "eval_status"),
    (SubmissionHistoryItem, "eval_status"),
    (SubmissionDetail, "eval_status"),
    (SubmissionRecord, "status"),
)

__all__ = [
    "CODE_INVALID_SCORE",
    "CODE_INVALID_STAGE",
    "CODE_MISSING_ENVS",
    "LEADERBOARD_STATUSES",
    "PROGRESS_DETAIL_KEYS",
    "QUEUE_SUMMARY_BUCKETS",
    "REASON_CODES",
    "REASON_SOURCES",
    "ROUND_STATUSES",
    "STATUS_VALUED_FIELDS",
    "WORKER_ACCEPTED_STATUSES",
    "Baseline",
    "BenchmarkMeta",
    "BenchmarkSpec",
    "Champion",
    "Contract",
    "ContractError",
    "CurrentRoundResponse",
    "EnvScore",
    "Envelope",
    "ErrorBody",
    "ErrorEnvelope",
    "EvalEnvironment",
    "EvalStage",
    "LeaderboardAudit",
    "LeaderboardResponse",
    "LeaderboardRow",
    "LeaderboardStatus",
    "ListEnvelope",
    "ListMeta",
    "LivenessResponse",
    "Meta",
    "MinerRef",
    "ModelRef",
    "PageLike",
    "PageMeta",
    "PerTaskScore",
    "ProgressAccepted",
    "ProgressUpdate",
    "QueueResponse",
    "QueueStatusResponse",
    "QueueStatusTask",
    "QueueSummary",
    "QueueTask",
    "ReadinessCheck",
    "ReadinessResponse",
    "Reason",
    "ReasonCode",
    "ReasonSource",
    "RoundDetail",
    "RoundHistoryResponse",
    "RoundStatus",
    "RoundSummaryEntry",
    "RoundsSummary",
    "ScanRejection",
    "ScanRejectionsResponse",
    "ScoreAccepted",
    "ScoreStat",
    "ScoreSubmission",
    "SubmissionArtifacts",
    "SubmissionDetail",
    "SubmissionHistoryItem",
    "SubmissionHistoryResponse",
    "SubmissionRecord",
    "TasksPassed",
    "ValidationErrorBody",
    "ValidationErrorEnvelope",
    "Weights",
    "check_env_scores",
    "check_required_envs",
    "extract_progress_detail",
    "worker_status_alias",
]
