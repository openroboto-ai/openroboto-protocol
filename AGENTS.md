# openroboto-protocol

> # 🚫 Every line in this package is a red line
>
> Its output decides which evaluation reproduces, which payment counts, and who
> gets emissions. One wrong constant costs miners real money and the subnet its
> credibility.
>
> **Allowed**: new modules, new tests, type annotations, comment and wording changes.
> **Forbidden**: changing the output of any released function, deleting or editing a
> golden vector, adding a runtime dependency, adding any I/O.

The protocol contract package of the OpenRoboto subnet (Bittensor netuid 80).
**The single source of truth both sides share**: the private backend and the public
miner / evaluation code install the same version number of this.

Distribution `openroboto-protocol` · import `openroboto_protocol` · Python **3.11+**.

## 0. Why it exists

Seed derivation, commitment codec and the status vocabulary are needed by both
sides. They used to be **hand-copied files in four repositories**, with no version
number and nothing keeping them in agreement — and they had already drifted:
`protocol/types.py` by 105 lines, `payment.py` by 313. Seed derivation itself had
not drifted yet, and **this package was extracted while that was still true**.

Spec §5: change seed derivation and historical evaluations stop reproducing.

## 1. Three disciplines

### ① Zero I/O, zero runtime dependencies

```toml
dependencies = []   # keep it this way
```

Pure functions and plain data. **The moment this package makes a network request or
reads a file, the two sides can no longer be proven to agree** — and "provably in
agreement" is its only reason to exist.

The half that needs I/O stays in the caller: `derive_seed()` here,
`fetch_drand()` in the backend.

Before adding a dependency, ask whether a miner's environment should pay for it.
(If `schemas.py` needs pydantic, discuss it separately; do not reach for `uv add`.)

### ② The version number is the contract version

| bump | Meaning |
|---|---|
| `patch` | Bug fix, behaviour unchanged |
| `minor` | Adds an **optional** field. Old data missing the key must have a default |
| `major` | Breaking change — needs an on-chain data migration plan and review |

> ⚠️ **That table is not in force during `0.x`.** While the version is `0.x`,
> **compatibility is not promised** — the shapes in `schemas.py` and the
> vocabularies in `status.py` may still change without a major bump.
>
> This is deliberate, and it ends on an event, not on a date. Freezing a version
> number for a contract nobody has really consumed freezes the shape it **happens
> to have grown into**, not the shape integration proves it needs.
>
> **`1.0.0` ships the moment all four boxes below are ticked.** None of them is a
> nice-to-have: without any one of them, the bump table above is an instruction
> nobody can carry out.
>
> - [ ] **`openroboto-backend` and `openroboto-cli` each pin the version they go
>   live with.** `major` means "breaking, needs a migration plan". With no launch
>   version there is no party to migrate, so any change can be argued to break
>   nobody — and at review time the line between `major` and `minor` has no
>   criterion behind it.
>
>   **2026-09-02**: both pin exactly, but a minor apart — the backend pins
>   `openroboto-protocol[schemas]` at 0.10.0 (production has run that since
>   08-22), the CLI pins the same package at 0.9.0 (published as PyPI
>   `openroboto` 1.2.0). (No full `name==version` literal here:
>   `test_quoted_pin_examples_do_not_drift` would read it as this repo's own pin
>   example, and it is somebody else's pin.)
>   ⏳ **Undecided**: is "each pins its launch version" enough, or must it be the
>   same number? The box stays unticked until that is ruled on.
>
> - [x] **The backend's 3 hand-copied mirrors are gone, replaced by submodule
>   imports.** (✅ 2026-08-19/20) `app/api/envelope.py` · `app/domain/reasons.py` ·
>   `app/domain/worker_reports.py`, plus `app/services/legacy_views.py`.
>   The parity tests guarding the copies went with them (`test_envelope_parity.py`,
>   `3322338`) — they skipped **wholesale** when the package was absent, so the
>   backend's green CI proved nothing. The package is a hard dependency now, so
>   what 1.0 freezes is a shape that has actually been imported.
>
> - [x] **`normalize_weights` moved into this package** (✅ 0.4.0 `weights.py`)
>   **and both copies deleted** (✅ 2026-08-21).
>   There used to be two, in `openroboto-backend/app/services/chain_writer.py` and
>   `openroboto-cli/src/openroboto/chain/weights.py`; both now import from here.
>   This is the **last conversion** before emissions go on chain, and for a while
>   it was not on this package's surface at all — 1.0's compatibility promise did
>   not cover the subnet's most critical step.
>   The two copies agreed numerically at the time (2010 measured inputs, 0
>   disagreements, including on-chain snapshot 122), but **nothing kept them
>   agreeing**: return type (`tuple` vs a `NormalizedWeights` dataclass), the
>   ceiling constant (literal `65535` vs the named `U16_MAX`), log language and
>   parameter names (`uids` vs `hotkeys`) had already diverged, and the test suites
>   were separate; only the backend logged a WARNING when a hotkey present in the
>   snapshot was missing from the metagraph.
>   Three counter-intuitive details (`w > 0` strictly greater · divide before
>   multiply · `int()` truncates rather than rounding to nearest) — "fix" any one of
>   them on one side and the two sides compute different u16 values, so the on-chain
>   consensus averages them away. **No error, no alert, no way to trace it.**
>   ⚠️ When moving code, **do not** bring `chain_writer.py`'s truncation example
>   with it: its `1/3 → int(21844.999…) = 21844` is wrong (`(1/3)*65535` is exactly
>   `21845.0`, and three of them sum to exactly 65535). The real evidence is
>   on-chain snapshot 122 itself: `0.9*65535 == 58981.5`, `int` gives 58981 and
>   nearest-integer rounding gives 58982 — so switching would rewrite a historical
>   value. That evidence is in the module docstring and its test case.
>   Deletion was triggered by design: each consumer repo kept a **self-expiring**
>   test case (`test_protocol_weight_parity.py`) that went red the moment this
>   package grew `weights`, spelling out the three things to do; once the copies
>   were gone those cases were deleted too. Written as a test and not a comment
>   because the previous to-do of that shape (the CLI's `burn_block_window`,
>   "waiting for 0.3.0") was released the next day and nobody came back to it.
>
> - [ ] **Every module declares `__all__`, with a test pinning the list.**
>   5 of 8 have it: `commitment` / `model_format` / `status` / `schemas` /
>   `weights`; **3 to go**: `constants` / `seed` / `model_hash`.
>   What SemVer promises about is the public surface. With no surface defined
>   there is no criterion between `patch` (behaviour unchanged) and `major`
>   (breaking) — delete a helper in `status.py` that nobody knows is public or
>   not, and which is it?
>   `tests/test_schemas.py::test_every_exported_model_is_pinned` is the template.
>   **The top-level `__init__.py` keeps an empty `__all__`**: consumers import
>   from submodules only (see "Import shape" below).
>
> Once 1.0 is out there is **no going back to `0.x`**: that withdraws a promise
> already in force, and a consumer pinned with `==` will not find out by itself.
> `tests/test_version.py` is the enforcer — it guards both directions: 0.x without
> the "compatibility is not promised" warning, and 1.0 that still carries it.

**Import shape: submodules only, the top level re-exports nothing.**

```python
from openroboto_protocol.seed import derive_seed  # ✅
from openroboto_protocol.status import normalize_stage  # ✅
from openroboto_protocol import derive_seed  # ❌ no such name at the top level
```

Not a style preference — it is how the zero-dependency promise is implemented.
`schemas.py` is the only module needing pydantic (behind the `[schemas]` extra).
Re-export at the top level and `import openroboto_protocol` drags pydantic in;
a miner installs this package to derive a seed and should not compile
pydantic-core on a GPU box.
`tests/test_schemas.py::test_miner_facing_imports_do_not_require_pydantic` pins it
(it asserts `openroboto_protocol.__all__ == []`).
Measured: `import openroboto_protocol` 0.24 ms, `openroboto_protocol.schemas` 74 ms.
All 24 real imports across the two consumer repos are the submodule shape and
**none takes a symbol from the top level** — migration cost is zero.

Consumers pin an exact version (`openroboto-protocol==0.10.0`, the README is
authoritative). Floating versions and vendored copies are both rejected by consumer
CI (both checks are quoted in the README under "What consumers must add to their
own CI", and both already run in `openroboto-backend` and `openroboto-cli`).

**Who bumps: the author of the change, in the same PR.** Not left to "whoever
releases" — that would make someone judge afterwards whether a change was a patch
or a minor, and the only person who knows is the one who wrote it. Review is the
one moment where the version and the change can be looked at together.

**How to release** (`git tag` is the only publishing action; never `uv publish`
locally):

```bash
uv version --bump patch       # or minor / major, edits pyproject's version
git commit -am "release: 1.0.1" && gh pr create   # version and change in one PR
# after the PR is merged into main and CI is green:
git tag v1.0.1 && git push origin v1.0.1
```

Push the tag and `.github/workflows/release.yml` takes over: it runs **exactly the
same** gates as a PR (golden vectors / 3.11 + 3.12 / 100% coverage / build and
install), then stops in the `pypi` environment for a human approval, then uploads
via PyPI Trusted Publishing (OIDC, no long-lived token).
A tag that disagrees with `pyproject.toml`'s version aborts before publishing —
consumers pin the version number, so a mismatch voids this package's only reason
to exist.

**A published version number cannot be recovered.** PyPI never allows the same
version to be uploaded twice, not even after a delete. A bad release can only be
walked forward with the next patch, which is why that human approval is not a
formality.

### ③ Golden vectors are history, not expectations

`tests/test_golden_vectors.py` holds input/output pairs that **already happened on
chain**. Changing one is changing history, and that history decided who got paid.

⚠️ 3 historical seeds (uid 60 / 194 / 192) cannot be reproduced from their stored
inputs; this is accepted. They **must not** become golden vectors — record them in
the irreproducible list with the reason, or the suite is red forever.

## 2. Structure

```
src/openroboto_protocol/
├── seed.py           seed derivation: block hash + competition id + drand → uint32
├── commitment.py     commitment payload encode / decode
├── model_hash.py     model fingerprint
├── model_format.py   what a submittable checkpoint must look like
├── status.py         task status + stage vocabulary
├── weights.py        share → u16 normalization, the last step before emissions
├── schemas.py        request / response models for every API endpoint
├── constants.py      CHAMPION_MARGIN, REQUIRED_ENVS, …
└── py.typed          marks the package as typed, so consumers' mypy sees it
tests/
├── test_golden_vectors.py    on-chain facts; changing one is changing history
└── test_<module>.py          mirrors the structure of src/
docs/
├── open-questions.md   still-open technical questions, each with what settles it
└── runbooks/           repeatable procedures (releasing…)
```

**One module, one contract.** There is one criterion for whether a piece of code
belongs here: **must both sides understand it identically?** Yes → in; only one
side uses it → out.

## 3. Commands

```bash
uv sync                       install (including the dev group)
uv run pytest                 tests; coverage gate is 100%
bash scripts/lint.sh          ruff check + ruff format --check + mypy strict (CI calls this script)
uv run ruff format .          reformat files (lint.sh only checks)
uvx pre-commit install        run the lint hooks before each commit; pre-commit is not in the dev group, only on human machines
uv version --bump patch       bump before releasing, see §1②
git tag v1.0.1 && git push origin v1.0.1     the only publishing action
```

Do not `uv publish` locally. Releases go through `release.yml`; publishing from a
laptop bypasses the gates and the approval, and leaves a package on PyPI that
nobody can trace back to a commit.

The coverage gate is **100%**, not 90% — the package is a few hundred lines and
every one of them is on the money path, so there is no reason to leave a gap.

## 4. Conventions

- **Language: this is a public repository — comments, docstrings, commit messages
  and the CHANGELOG are English, with no exceptions.** This file and
  `pyproject.toml` comments count. Its readers are not on the team: miners, the
  evaluation party, external validators, and anyone who clicks into the source
  after `pip install`. A comment they cannot read is not a comment, and the
  comments of this package are its main asset — the "why this must not be changed
  back" kind of information that the code itself cannot express.
  The criterion is **whether the repository will be public**, not whether it is
  public right now.
- Commit format is Conventional Commits: `type(scope): summary`. Imperative
  summary, at most 72 characters; the body says **why**, it does not restate the
  diff.
- Exported functions, constants, dataclasses and schemas must carry a comment
  stating the **contract meaning**, not restating the name (the state-machine
  semantics of `status`, that `CHAMPION_MARGIN` is an absolute value and not a
  percentage).
- Fields that must come from the same source are bound into one `frozen=True`
  dataclass, so a mismatch is impossible at the type level.
- A new module states: what it promises, what it is not responsible for, who
  consumes it, and the minimal way to verify it.
- Constants are defined here, never scattered as literals in consumers — a
  disagreeing status vocabulary has bitten before (the queue page's progress bar
  disappeared entirely).
- Comments state **the invariant as it is now**. History goes in git log, not into
  a paragraph explaining what a line used to say.

## 5. Before committing

- [ ] `uv run pytest` green, coverage 100%
- [ ] `bash scripts/lint.sh` green
- [ ] Touched anything in `src/` → `pyproject.toml`'s `version` bumped in the
      **same PR** (§1②)
- [ ] Changed the **output** of a released function → say so explicitly, and say
      whether it is major or patch
- [ ] Added a runtime dependency → say why a miner's environment should pay for it
- [ ] Touched a golden vector → say why that history needs to be rewritten

## 6. Explicitly not done here

| Not done | Why |
|---|---|
| Any I/O, database, secrets | With I/O the two sides cannot be proven to agree |
| Backend business logic | Ranking, admission and weight computation belong to the backend |
| Convenience helpers | If it is not a contract it does not belong, or this becomes a second `utils/` |
| Cosmetic breaking renames | Consumers pin exact versions; a rename is a major |

## 7. References

- Cameron's ruling log, shared by the three repos and outranking everything here:
  `../openroboto-backend/DECISIONS.md`
- Engineering and documentation conventions:
  `~/Playground/quantitative-trading-agent-service/CLAUDE.md`
- Rebuild workspace notes: `../README.md`
- Related decisions: `../openroboto-backend/docs/adr/`
