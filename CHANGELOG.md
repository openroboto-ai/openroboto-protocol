# Changelog

Every entry answers one question before it lists anything: **does a consumer have
to change something, and what breaks if it does not?** The version number *is* the
contract version (README, "Versioning is a promise"), so a diff summary without
"who has to act" is not usable by the people who pin it.

Three kinds of reader are addressed separately where they differ:

- **Miners already running** — an old CLI, an old payload shape on chain. The
  interesting answer is almost always "nothing to do", and it has to be backed by
  a golden vector rather than by a sentence.
- **`openroboto-backend` / `openroboto-cli`** — they pin an exact version and
  import from it.
- **The evaluation worker and external validators** — they do **not** install this
  package. They are listed only when something they read over HTTP or on chain
  moves.

While the version is `0.x`, compatibility is not promised (README). Entries before
0.7.0 are reconstructed from the release commits; if this file and the commit ever
disagree, the commit is the authority.

## 0.12.0 — 2026-09-24

**openpi checkpoints are judged by the layout of the season's task set.** AXIS
seasons read the normalization stats from
`assets/axis-v0.1-task501-runtime-v1/norm_stats.json`, not the LIBERO path. Until
now the only openpi layout was LIBERO's, so a correctly built AXIS checkpoint drew
`non_canonical_norm_stats` — and `openroboto submit` refuses to pay on any warning.
Additive; nothing that passed or failed before changes unless a caller opts in.

### Miners already running: nothing to do

No payload, seed or fingerprint moved. The fix reaches miners through the CLI
release that pins this version.

### `openroboto-backend` / `openroboto-cli`

- `model_format.AXIS_LAYOUT`, `model_format.openpi_layout_for(benchmark)` and
  `model_format.AXIS_BENCHMARK_PREFIX` are new. `openpi_layout_for` returns the AXIS
  layout for any `axis_v*` task set and the LIBERO layout otherwise, including
  `None`.
- `check_checkpoint_layout(files, *, layout=LIBERO_LAYOUT, ...)` takes the layout.
  The default is the old behaviour.
- `schemas.Competition.benchmark: str | None` — the season's task set, as served
  by `/api/v1/competitions`. Before this it was dropped on parse.

### The evaluation worker and external validators

Nothing they read moved.

## 0.11.0 — 2026-09-02

**Every identifier that called a competition a "round" is renamed.** Breaking for
0.x consumers, and **nothing on chain or in a golden vector moved**: the payload
key is still the byte `r`, `derive_seed` produces the same uint32 for the same
three inputs, and all 122 golden vectors are green without one value being
edited.

### Miners already running: nothing to do

The commitment JSON is byte-for-byte what it was. `r` is still `r`, `cid` is
still `cid`, key order is unchanged, and a payload that used neither `cid` nor
`m` encodes exactly as it did at 0.6.0. Only Python attribute names moved.

### `openroboto-backend` / `openroboto-cli`: mechanical, and the compiler finds it

Every rename below is an attribute or parameter, so an unported call site is an
`AttributeError` or a `TypeError`, not a wrong value. There is no shape where the
old name silently keeps working.

| Was | Is | Where |
|---|---|---|
| `seed.derive_seed(block_hash, round_num, …)` | `seed.derive_seed(block_hash, competition_id, …)` | second parameter |
| `seed.verify_seed(…, round_num, …)` | `seed.verify_seed(…, competition_id, …)` | third parameter |
| `seed.SeedInputs.round_num` | `seed.SeedInputs.competition_id` | field |
| `commitment.CommitmentPayload.round_num` | `commitment.CommitmentPayload.claimed_competition_seq` | field, wire key `r` unchanged |
| `schemas.LeaderboardRow.round_num` | `schemas.LeaderboardRow.seq` | wire field |
| `schemas.LeaderboardResponse.round_id` | `schemas.LeaderboardResponse.competition` | wire field |
| `schemas.SubmissionDetail.round_id` | `schemas.SubmissionDetail.competition_id` | wire field |
| `schemas.LivenessResponse.round` | `schemas.LivenessResponse.competition` | wire field |

**Why `claimed_competition_seq` and not `claimed_competition_id`.** The payload
already carries `competition_id` for `cid`, which is the `competitions.id`
primary key. `r` is a different thing: self-reported by the miner, and an
**ordinal within the simulation track** — the backend resolves it as
`find_competition(track="sim", seq=r)`. Calling it an id would send the next
reader looking it up as a primary key. `competition_id` stays with `cid` because
that is what `cid` is.

Three of the four wire renames are the backend catching up with itself, not new
work: it renamed `LeaderboardRow.round_num` to `seq` on 2026-08-31, `/api/rank`'s
`round_num` to `seq` on 2026-09-01, and `/healthz`'s `round` key to `competition`
on 2026-09-01. `SubmissionDetail` is the one going the other way — it still emits
`round_id` and follows this package.

### Removed: the contract models for the retired `/rounds` endpoints

`GET /api/v1/rounds` and `GET /api/v1/rounds/current` have been 410 tombstones
since 2026-09-01 (an ordinal cannot locate a season — `(sim, 1)` and `(real, 1)`
share it — so the whole family was replaced by `/api/v1/competitions`). Their
models described nothing callable and are gone: `Champion`, `RoundStatus`,
`ROUND_STATUSES`, `RoundSummaryEntry`, `RoundDetail`, `CurrentRoundResponse`,
`RoundsSummary`, `RoundHistoryResponse`. Neither consumer repo imports any of
them.

### Deprecated, not removed: `worker_status_alias` / `WORKER_ACCEPTED_STATUSES`

`GET /api/submission/{task_id}` emits the eight storable words verbatim and does
no alias conversion (ruled 2026-09-02), which closes the TODO these carried. They
stay because `openroboto-backend`'s worker-contract parity test imports both to
assert the two vocabularies are disjoint; they go when that test does.

### Also in this release

- Every module now declares `__all__` — `constants`, `seed` and `model_hash` were
  the three without one — each pinned by a `test_public_surface_is_pinned` case.
  That completes the last of the four 1.0.0 preconditions, so **1.0.0 is now a
  decision rather than a blocker**; it is the owner's to make and is not made.
- The PyPI classifier drops to `Development Status :: 4 - Beta`, matching a
  README that says compatibility is not promised while the version is 0.x.
- Everything in this repository is written in English, including this file.

### The evaluation worker and external validators

`/api/rank` already sends `seq` and `/healthz` already sends `competition`; both
changed on the backend before this release. Nothing else they read moves.

## 0.10.0 — 2026-09-01

### Two new stages: `queued` / `stalled`

The worker can now say something when it lets go of a task. The four existing
`stage` words all mean "currently doing X"; none of them could express "I have let
go" — so the evaluation retry path (`_schedule_clean_retry`) cleared its cache, put
the task back at the tail of its local queue, and **reported nothing at all**.

What that cost on 2026-09-01: two **already paid** miner tasks sat in the queue
showing `prechecking` for over four hours while the worker crashed and restarted
every two minutes, burning GPU for nothing. From outside, that looked exactly like
a healthy evaluation in progress — `stage` is the only field that could have told
the truth, and it had no word for it.

| | Meaning | Stored as |
|---|---|---|
| `queued` | Handed back, waiting to be claimed again | `queued` (**no prefix**) |
| `stalled` | Not retrying any more, waiting for a human | `stalled` (**no prefix**) |

🔴 **Neither is a verdict.** They say what the worker did, not what the model is
like. In particular `stalled` **must not be written as `eval_failed`**: failing N
times on one machine is evidence about **that machine**, and broken infrastructure
is our fault while the miner's fee is already burned — ruling against them means
spending someone else's money on a conclusion nobody reached.

⚠️ **Neither carries the `benchmark_` prefix**, unlike the four above. That prefix
marks "the evaluation is working on this row", and these two mean the opposite.
Prefixing them would make `stage.startswith("benchmark_")` — a check that reads
naturally and that somebody will eventually write — say the opposite of what it
means.

⚠️ They are listed **after** the four in-progress words, but they are **not a fifth
and sixth step** — they are exits reachable from any step. Reading `STAGES` as "these
six things happen in order" is wrong.

### `QueueStatusTask` gains two fields: `stage_age_seconds` / `stage_stale`

**The two words above fix "the worker can say something"; these two fields fix "the
worker says nothing at all".** The latter is what actually happened on 09-01: the
process died right after reporting `prechecking` and never reported again. No
vocabulary can rescue a client that has stopped talking; only our side can downgrade
that cell on the grounds that it has not been heard from in a long time.

- `stage_age_seconds`: how long this `stage` has gone without moving. **Present only
  on `evaluating` rows**, `null` elsewhere (a terminal row's `stage` is meant to
  stand still, and giving it an ever-growing number would display "finished" as
  "stuck for longer and longer").
- `stage_stale`: `true` once that number crosses the backend's threshold.

🔴 **Neither is a verdict**, same as above. A stale `stage` does not mean the task
failed — the backend changes no status because of it. It has simply stopped
vouching for the freshness of a field it cannot verify.

⚠️ **The threshold stays server-side, deliberately.** Sending only the seconds and
letting every client decide ends with two pages calling the same task stuck at
different moments, and the one you have to explain to a miner is whichever was on
screen.

**Who has to act**: `openroboto-backend` only has these two fields after upgrading
to 0.10.0; without the upgrade the contract snapshot disagrees
(`test_task_key_set_matches_the_contract`). The frontend greys out on the boolean
and renders "updated N minutes ago" from the seconds.
**Miners and external validators: nothing to do** — both fields are new and
optional.

## 0.9.0 — 2026-08-27

**`round_num` is removed from six response models.** Breaking, on purpose, and the
on-chain encoding does not move: `commitment.py` is untouched and all 122 golden
vectors are green.

Gone from `QueueTask` · `ScoreSubmission` · `SubmissionRecord` · `QueueStatusTask` ·
`SubmissionHistoryItem` · `ScanRejection`.
**Still there on `LeaderboardRow`** — see below.

### Miners already running: nothing to do

Nothing on chain moved. The commitment payload still carries `r`; what changed is
that the backend no longer reads it for anything. `derive_seed` keeps its
signature — only the *name* of its second parameter is the season now, and for
every submission made so far that parameter has the same value it always had.

### The evaluation worker: one key disappears from two payloads

`GET /api/v1/benchmark/queue` no longer sends `round_num`, and
`POST .../task/{id}/score` no longer declares it. A worker that still sends it is
**not** rejected — `ScoreSubmission` keeps pydantic's `extra=ignore`, and the value
lands in the stored result like any other unknown key. A worker that *reads* it off
a queued task will get a `KeyError`; there is nothing to read it for, since which
season a task belongs to is `competition_id` and nothing else.

### External validators: `/api/rank` is unchanged

`LeaderboardRow.round_num` **stays**. That row is what `/api/rank` returns, and on
2026-08-18 twelve distinct IPs pulled it whose owners we have not identified. The
argument for deleting the other six — "a number nobody may branch on is worse than
no number" — does not reach a field we cannot see the consumers of.

### Why now

It used to be the second input to the seed hash, so it decided which LIBERO tasks a
submission was scored on. That input is `competitions.id` as of 2026-08-27, and a
number that no longer matches the seed, participates in no dispatch, and keys no
lookup is not a harmless leftover: it reads as something safe to branch on.

## 0.8.0 — 2026-08-26

One new optional field on `Competition`: **`base_model_family`**. Additive — no
existing field changed shape, no encoding moved, no vocabulary word was removed or
renamed.

### Miners already running: nothing to do

Nothing on chain moved. `Competition` is a response shape, not a payload, and the
new field is optional with a `None` default, so an old client that never sees it
behaves exactly as before.

### `openroboto-backend` / `openroboto-cli`: re-pin, then two follow-ups

The backend already serves the field (migration `0014`); until the pin there moved
to `0.8.0` the CLI's `Contract` base (`extra=ignore`) silently dropped it on the way
in, so **the CLI could not see it no matter what the backend sent**. The CLI is on
`0.9.0` now, so the field arrives. After re-pinning:

- `openroboto init` must copy `base_model_family` into `miner.yaml`'s
  `competition:` block (`commands/init.py`'s `SECTION_KEYS`). Its
  `test_section_keys_track_the_protocol_contract` fails until it does.
- Anything that picks a layout rule book or a format profile reads that key, not
  the adapter string.

### Why the field exists

`competitions.adapter` encoded two orthogonal things in one string: the track plus
either a base model (`sim_openpi`, `sim_lingbot`) or a piece of *hardware*
(`real_xarm6`). Three of the four things dispatched off it follow the base model
(layout rules, fingerprint inputs, the evaluator's loader) and one follows the
track (ranking format). So a real-robot season had nowhere to say which model it
runs, and "xArm 6 on π0.5" could not be written down at all.

`base_model_family` is that missing dimension. `adapter` keeps its exact
vocabulary and now decides only the ranking format; the `_openpi` / `_lingbot`
suffixes are historical and must not be read as the base model.

🔴 `None` means **not decided yet — refuse**, the opposite of the `None` on the
five instants ("this boundary is not checked" — permission). `real/1` is `None`
today. A consumer that defaults it to `openpi` judges a submission somebody already
paid for by rules nobody chose for that season.

## 0.7.0 — 2026-08-25

The real-robot track's contracts, plus a second base model for the simulation
track. Additive throughout: no existing field changed shape, no encoding moved,
no vocabulary word was removed or renamed.

### Miners already running: nothing to do

A payload without `cid` / `m` encodes to the bytes it always did. The two new keys
are appended after `bb` and **omitted entirely** when unset — writing `"cid":null`
would decode identically and change every byte on chain.

That is verified, not asserted:
`tests/test_commitment.py::test_gv1_reencodes_to_the_exact_on_chain_bytes` takes
the 295 real bytes of block 8808332 (netuid 80, `Data::BigRaw`), decodes them and
re-encodes them, and compares the result byte for byte. When that test goes red,
the format has drifted and every miner running today is writing something the
backend was not built to read.

`r` (round number) is deliberately kept. For a payload with no `cid` it is the
only thing that locates the season — the reader resolves it as
`(sim, round_num=r)`.

### `openroboto-backend` / `openroboto-cli`

Added, all optional, all with a defined meaning for data that predates them:

- **`commitment`**: two on-chain keys — `cid` (`competition_id`, `int`, following
  `competitions.id bigint`) and `m` (`model_hash`). `m` is the one value that
  cannot be looked up from `cid`, which is why it is on chain at all: the real
  track allows private repositories, so the backend cannot compute the fingerprint
  itself before evaluating. Also `Track` (`sim` / `real`, the value set of
  `competitions.track`) and `check_payload()`. A present but unusable `cid`
  decodes to `0`, not `None` — an identity primary key never hands out 0, so the
  season lookup fails loudly instead of quietly filing a real-track entry on the
  simulation leaderboard with the fee already paid.
- **`status`**: `SeasonStatus` (`awaiting_eval`, `awaiting_settlement`,
  `awaiting_confirmation`, `paying`, `paid_out`, `burned`) and `InvalidReason`
  (`hf_forbidden`, `hf_access_revoked`, `hf_repo_not_found`,
  `hf_revision_not_found`, `hf_files_incomplete`), each with a `*_VALUES` tuple so a
  `CHECK` constraint can be built from the package instead of from a hand-typed
  literal. `INVALID_REASON_VALUES` is exactly the word list already in the backend's
  0004 migration. **No new submission status**: real-track submissions live in the
  same table keyed by `competition_id` and share the lifecycle words that were
  already there — `registered` maps to `received`, `disqualified` to `rejected` plus
  a reason code. Two vocabularies on one column is the 2026-08-14 incident itself.

  Two of the season words are worth reading before you build a table on them.
  `awaiting_confirmation` is **not** in the PRD's five: it is the cooling period
  plus the manual gate — settlement written, no α moved — and the alternative to
  naming it is overloading a word that means something else. And the word is
  `paying`, **not** `paying_out`: `paying_out` and `paid_out` share a suffix and
  differ in the middle, while meaning "α leaves the wallet daily" versus "the season
  is closed". Nothing that must never be confused gets a near-identical spelling.

- **`status`**: `STORABLE_STATUSES` (8 words) and `TRANSIENT_STATUSES`
  (`burn_checking`, `burn_passed`) split what had been one set doing two jobs.
  `ALL_STATUSES` (10 words, unchanged) is the vocabulary for **reading** — is this
  word legal, does this transition hold. `STORABLE_STATUSES` is the vocabulary for
  **writing**, word for word the `ck_submissions_status` whitelist. Validating a
  to-be-written status against `ALL_STATUSES` passes two words the column refuses,
  and the CHECK violation surfaces as a 500 after the request was accepted, so a
  write-side check should move to the new set. Both sizes and the difference between
  them are now pinned by tests; the module's own docstring had been claiming
  "eight" for a set of ten, with nothing watching.
- **`schemas`**: `Competition` — the body of `GET /api/v1/competitions`, one season
  and the spec frozen for it (backend `competitions`, ADR 03). It reuses
  `commitment.Track` instead of spelling `sim` / `real` a second time, and lists
  through the existing `ListEnvelope`, so no wrapper model was added.
  Three field decisions worth knowing before you consume it, each written out in the
  docstring: the five instants are all optional and `None` means **this boundary is
  not checked** (not "unknown" — filling in a plausible date invents a submission
  window nobody configured); `base_repo` / `base_revision` are `None` until pinned and
  **cannot be `""`** (π0.5's commit was never pinned, and `""` builds a HuggingFace URL
  that quietly resolves to today's weights); and `params` stays `dict[str, Any]` on
  purpose — parsing it into a fixed model turns `jsonb` back into columns. 🔴
  `params["fee"]["coldkey"]` **is `null` today** on the real track; a consumer that
  reads it must fail closed and refuse to pay, because any substitute address spends
  the entry fee before the submission exists. `id` is served because it is what goes on
  chain as `cid`, but `(track, seq)` is the stable key — that is what `miner.yaml`
  stores and what a payload without `cid` resolves by. `status`
  (`draft` / `active` / `archived`) is a closed `Literal`, disjoint from the other
  three status vocabularies and test-pinned that way; `adapter` is deliberately an open
  `str` so a new season does not need a release of this package.
- **`schemas`**: `EpisodeResult`, `MediaRef`, `EpisodeFailure` — the body of
  `POST /real/tasks/{id}/episodes`, one episode per request, 24 per entry.
  `MediaRef` binds a `uri` to its `sha256` because both must come from the same
  upload; a reference without a digest freezes nothing. The simulation scoring
  path (`ScoreSubmission`) is untouched and a test pins its field set.
- **`model_format`**: `check_lingbot_layout()` and `LingbotLayout` for
  LingBot-VLA 2.0, **alongside** the openpi rules, never replacing them — miners
  keep submitting pi0.5 against rounds that are already open. Which base model
  applies is a season parameter. `check_checkpoint_layout()` is byte-for-byte
  unchanged. `REJECTING_ISSUE_CODES` / `WARNING_ISSUE_CODES` turn the reject/warn
  split, which until now existed only as a `(warning)` prefix inside docstrings
  guarded by nothing, into two frozensets; `rejecting` is derived from `warning`,
  so a code nobody classified defaults to the safe side.
- `__all__` is now declared and test-pinned on `commitment`, `status` and
  `model_format` (an AGENTS.md 1.0 prerequisite; `constants`, `seed` and
  `model_hash` remain).

Widened, without any change in behaviour: the docstrings of `b` / `bb` now say
"payment credential" rather than "burn". Whether the payment was a burn
(simulation, `add_stake_burn`) or a transfer to the season's coldkey (real track)
is decided by the season, so no second pair of keys was added and the Python
field names (`burn_tx_hash`, `burn_block`) stay — renaming them would be a major
bump for no gain.

Deliberately **not** added, so nobody re-proposes them:

- no `t` (track) key — the track is `competitions.track` on the row `cid` points
  at, and a second copy on chain can disagree with the database with no rule to
  settle it;
- no `f` / `fb` (fee) keys — `b` / `bb` already are the payment credential;
- no third `EpisodeFailure` value, and nothing for appeals (there is no appeals
  process).

Measured byte budget, now in the module docstring — `MAX_COMMITMENT_BYTES` is
still 512, and `hf_repo_id` is the only variable-length field:

| Payload | Fixed cost | Room left for `hf_repo_id` |
| --- | --- | --- |
| simulation, 7 keys (old miner) | 271 bytes | 241 characters |
| simulation, 8 keys (`cid`) | — | between the two |
| real robot, 9 keys (`cid` + `m`) | 368 bytes | 144 characters |

One more 64-hex key would cost 71 bytes and cut the real track's room to 73
characters. HuggingFace itself permits repo ids of up to 193 characters, so a
real-track miner with a very long name has to rename — `encode()` raises
`CommitmentTooLargeError` before the fee is paid, which is the only moment the
money can still be saved.

### Evaluation worker / external validators: no impact

They do not install this package. Nothing in this release changes an HTTP
response shape they read (`/api/weights` included) or the on-chain bytes of any
payload written by software they run.

## 0.6.0 — 2026-08-22

- `weights`: `NormalizedWeights` gains `dropped_share` — how much weight was lost
  because a hotkey in the snapshot is absent from the metagraph. Reported, not
  enforced: the backend knows which address is the burn target and can name it,
  an external validator running the CLI does not. Seen on testnet 313, a snapshot
  still carrying mainnet's burn address produced `uid=23 u16=65535` — one miner
  taking the entire subnet's emission — with one warning line and a successful
  extrinsic. The arithmetic is untouched.
- Consumers: a new field on an existing dataclass. Nothing to change; a threshold
  on `dropped_share` before sending weights is worth adding.

## 0.5.0 — 2026-08-21

- `schemas`: `model_hash`, `stage` and `reject_reason` are `str | None`. The
  backend's phase-1 work replaced every sentinel with SQL `NULL` and these three
  annotations had not followed, so the CLI's first real end-to-end run died in
  validation **after** the burn was paid and the model was on HuggingFace.
- Consumers: widening a field is a minor bump, and anyone pinned to `==0.4.0`
  keeps failing on live data until they move.

## 0.4.0 — 2026-08-20

- New module `weights`: `normalize_weights()`, the last conversion before
  emissions reach the chain (`{hotkey: share}` → `(uid, u16)`). It existed twice,
  in the backend's `chain_writer` and in the CLI's `chain/weights`, with nothing
  comparing them.
- Consumers: both copies were deleted on 2026-08-21, once this version was
  installed. Each repository carried a test that turned red the moment the package
  gained the module and stated the three steps; those tests went with the copies.
- Three details are load-bearing and must not be "cleaned up": strict `w > 0`,
  divide-before-multiply, and `int()` truncation rather than rounding. On-chain
  snapshot 122 is the evidence — `0.9 * 65535` is exactly 58981.5, `int` gives
  58981 and `round` gives 58982, so switching to `round()` rewrites a value
  already settled on chain.

## 0.3.0 — 2026-08-19

- `status`: `STAGE_CLAIMED`. Production has whitelisted four stages all along;
  this package shipped three, so a worker reporting `claimed` was legal upstream
  and rejected here. Progress reports are best-effort, so the 400 was swallowed
  silently and the queue page simply lost a step.
- `constants`: the 50-block burn-to-commitment window, with the three properties
  of the comparison pinned (symmetric distance, rejection strictly greater than
  the window, block 0 skips the check). It had been living in the CLI's own
  settings. The value is production's, not the deployment docs', which said 10.
- All comments and docstrings translated to English — this is a public package.

## 0.2.0 — 2026-08-19

- **Breaking** (allowed under `0.x`): `seed`, `drand_random`, `drand_round` and
  `revision` are nullable and constrained; `drand_round` is `Field(gt=0)`.
  Consumers reading these fields must handle `None`. Removing a default only
  changed what happens when a field is omitted — it did not reject a `0` a
  producer explicitly put on the wire, and the producer is exactly where the
  zeros came from.
- The 1.0 section of the README becomes four checkboxes, each stating why the
  compatibility promise is unenforceable without it.

## 0.1.0 — 2026-08-19

First release. `seed`, `commitment`, `model_hash`, `model_format`, `status`,
`constants`, `schemas`, `py.typed`; golden vectors for the seed derivation; zero
runtime dependencies, with `pydantic` behind the `[schemas]` extra so that
installing this on a miner's GPU box never builds a `pydantic-core` wheel.
