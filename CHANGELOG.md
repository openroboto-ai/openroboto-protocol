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
- Consumers: both copies are meant to be deleted once this version is installed.
  Each repository carries a test that turns red the moment the package gains the
  module and states the three steps.
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
