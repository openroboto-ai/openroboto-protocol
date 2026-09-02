# 01 — Release runbook: from the bump to all three consumers upgraded

| Item | Value |
|---|---|
| **Title** | The release process of `openroboto-protocol` (including how a breaking change / major goes, and how to walk one back) |
| **Date** | 2026-08-19 |
| **Status** | In force. Steps were checked against the workflows in this repo. The first release is done — 0.1.0 went to PyPI on 2026-08-19, and as of 2026-09-02 the latest is 0.10.0 (ten versions, 0.1.0–0.10.0). |
| **Sources** | ① Executable code: this repo's `.github/workflows/release.yml` (tag trigger · `pypi` environment · trusted publishing), `.github/workflows/ci.yml` (`workflow_call` gates), `AGENTS.md` §1② ② Local measurements (2026-08-19, see §2 step 1 and §4) ③ `openroboto-backend/docs/specs/08-worker接入协议包说明.md` |
| **Scope** | Every release of `openroboto-protocol`, and the upgrade of `openroboto-backend` / `openroboto-cli` / the evaluation party's worker that follows |
| **Related** | `AGENTS.md` §1②③ · spec §5 (change seed derivation and historical evaluations stop reproducing) · `openroboto-evaluation/SCOPE.md` |

> **In one line**: `git tag` is the only publishing action, and `uv publish` is never
> run locally.
> **A published version number cannot be taken back** — PyPI never allows the same
> version to be uploaded again, not even after a delete.
> So every verification in this document sits **before** the tag, and the human
> approval in the `pypi` environment is the last gate.

---

## 1. Confirm these are true before every release

| Check | How | If it is not true |
|---|---|---|
| The PyPI project and its trusted publisher are configured | `curl -o /dev/null -w '%{http_code}' https://pypi.org/pypi/openroboto-protocol/json` → 200 | ✅ Re-checked 2026-09-02: 200, all ten versions present (0.1.0–0.10.0). If it ever turns 404, or the OIDC exchange fails, compare owner / repo / workflow filename / environment name character by character against the trusted publisher record on PyPI |
| The `pypi` environment has required reviewers | GitHub → Settings → Environments → `pypi` | Add them. No reviewer means one slipped tag goes straight to PyPI |
| CI on main is green | GitHub Actions | Fix it first; do not use a tag to find out |
| The version was already bumped **in the PR that made the change** | `git log -p pyproject.toml` | See §2 step 1 — a catch-up bump needs its own PR, do not push straight to main |

---

## 2. A normal release (patch / minor)

### Step 1 — bump the version in the same PR as the change

```bash
uv version --bump patch     # or minor. major does not go through here, see §5
```

**Why it must be the same PR** (`AGENTS.md` §1②): only the person who wrote the
change knows whether it is a patch or a minor. Leaving it to "whoever releases"
asks someone who has not read the diff to make that call.

Measured (2026-08-19, uv 0.10.11): `uv version --bump minor` exits **0** and edits
three things at once — `version` in `pyproject.toml`, this package's own entry in
`uv.lock` (`version = "1.1.0"`), and the local `.venv`.

⚠️ **`uv.lock` has to be committed with it.** Editing pyproject without updating the
lock makes `uv lock --check` exit **1** (`The lockfile at uv.lock needs to be
updated`), measured — and since every CI job starts with `uv sync --locked`, **the
whole run goes red** with an error that has nothing to do with your change.

### Step 2 — merge the PR into main, wait for CI to go green

Green means the three jobs of `ci.yml`: `golden vectors` ·
`lint + tests (py3.11 / py3.12)` · `build (packaging gate; publish lives in
release.yml)`.

> A red `golden vectors` is **not** a failing test — it is somebody rewriting
> history that already happened on chain. Do not "fix the test": stop and explain
> why that history needs to be rewritten (`AGENTS.md` §1③).

### Step 3 — push the tag (the only publishing action)

```bash
git checkout main && git pull
git tag v1.0.1 && git push origin v1.0.1
```

The tag must match `pyproject.toml`'s `version` character for character. When it
does not, `ci.yml`'s `Tag matches package version` aborts before the build, and
`release.yml`'s `Artifact version matches the tag` compares the artifact filenames
a second time.

### Step 4 — approval

`release.yml` first runs the PR gates verbatim (`uses: ./.github/workflows/ci.yml`)
and then stops in the `pypi` environment. **Before approving, look at three things
again**:

1. Is the tag name right (the `v` prefix, the version number)?
2. Does the tag point at the commit you think it does (`git rev-parse v1.0.1`)?
3. Is this actually a breaking change being released as a minor (see the criterion
   in §5)?

Approval runs `uv publish --trusted-publishing always`. **There is no undo after
this step.**

### Step 5 — verify "it installs", not "CI went green"

```bash
uv run --isolated --no-project --with "openroboto-protocol==1.0.1" \
    python -c "import openroboto_protocol.seed, openroboto_protocol.status; print('ok')"
```

Then confirm the zero-dependency promise still holds on PyPI (the `build` job
already inspected the wheel's METADATA; this checks the copy actually downloaded
from the index): after installing, `pip list` must show nothing beyond this package
unless you explicitly installed `[schemas]`.

### Step 6 — push it to the consumers

| Consumer | Who | What | Verification |
|---|---|---|---|
| `openroboto-cli` | us | edit the `==` in `pyproject.toml`, `uv lock`, PR | `protocol-guards.yml` green + `uv sync --locked` |
| `openroboto-backend` | us | same; do not drop the `[schemas]` extra | CI green + no `ingest:protocol-missing` alert after deploy |
| the evaluation party's worker | **them** | we send a notice, we do not set a deadline (`openroboto-evaluation/SCOPE.md`) | run `test_worker_contract_parity.py` on our side |

> ⚠️ Upgrading the consumers is **not** part of a release; it is a separate action
> afterwards. But a minor's promise that "old data missing the key must have a
> default" only means anything when a consumer has **not** upgraded in step — put
> another way, if a minor needs the consumers to upgrade in lockstep to work, it was
> a major.

---

## 3. When something goes wrong (ordered by when it happens)

| When | Consequence | What to do |
|---|---|---|
| Wrong bump (minor written as patch), **PR not merged yet** | none | Fix it and push again |
| Wrong tag name, **workflow still running / not approved yet** | none | `git tag -d v1.0.1 && git push origin :refs/tags/v1.0.1`, then re-tag. **A tag is recoverable as long as nothing reached PyPI** |
| A gate went red | not published | Fix it and re-tag (the same version number can be reused, because PyPI does not have it yet) |
| Trusted publisher misconfigured (`invalid-publisher`) | not published | Compare owner / repo / workflow filename / environment name character by character against the record on PyPI, then re-run that job |
| **Already uploaded to PyPI** and the content is wrong | 🔴 the version number is burned | See below |

### It is already out — how to roll back

**There is no rollback, only rolling forward.**

1. Stop `pip install` / `uv add` from recommending it: **yank** it on PyPI (project
   page → Manage → the version → Yank). What yank means, precisely:
   - consumers who **pinned** that version (all of ours use `==`) **can still
     install it** and are not interrupted;
   - new resolutions will not select it.
   So yank means "stop stepping in it", **not** "remove it from the world".
2. **Delete** (Delete release) is for extremes only, such as a leaked secret. After
   a delete that version number can **never** be used again — and it is exactly
   what consumers pinned, so their working environments become unrebuildable.
3. Fix it, release the next patch, and **notify all three consumers**, the
   evaluation party included.

---

## 4. The three things people trip over the first time

1. **Editing pyproject without committing `uv.lock`** → the whole CI run goes red
   with an error unrelated to the change (§2 step 1, measured exit 1).
2. **Assuming `==1.0.0` in a consumer's `pyproject.toml` pins the version** — if
   that repo has a `[tool.uv.sources]` path or git override, the `==` **takes no
   part in resolution**. Measured (uv 0.10.11 and 0.11.18 alike): declare
   `==1.0.0`, install `1.2.0`, exit code **0**, and the `Protocol version is pinned`
   check still prints `pin ok`.
   **When upgrading a consumer, the first thing to confirm is that the
   `[tool.uv.sources]` override is gone.**
3. **Running `uv publish` locally** → bypasses the gates and the approval, leaves a
   package on PyPI nobody can trace back to a commit, and burns that version number
   for good. `AGENTS.md` §3 forbids it outright.

---

## 5. Breaking changes (major) — "one changed constant is changed money"

### 5.1 First decide: is this changing history?

Spec §5: **change seed derivation and historical evaluations stop reproducing.**
A few other things here weigh the same: `model_hash` (the model fingerprint → whose
submission is whose) · the `commitment` codec (miners encode by A, the backend
decodes by B) · `CHAMPION_MARGIN` / `REQUIRED_ENVS` in `constants` (crown changes
and admission → how emissions are split).

There is one criterion, and it does not count changed lines:

> **Given the same on-chain input, does the new version compute the same result as
> the old one?**
> No → you are changing history. Answer "why does this history need rewriting"
> before discussing a version number.
> Yes → only then is it a patch / minor / major discussion.

The `golden vectors` job is that criterion in executable form. Red means the answer
is already in.

### 5.2 What a major actually involves (not "bump a digit")

| # | What | Who | If it is skipped |
|---|---|---|---|
| 1 | Write an **on-chain data migration plan**: which competitions follow the old rule, which follow the new one, at which block the switch happens, and how historical data is labelled | whoever proposes the change | "When does the new rule start applying" has no single answer → the two sides each compute money their own way |
| 2 | Review (`AGENTS.md` §1②: major needs review) | the team | — |
| 3 | **Notify first, publish second**: all three consumers, the evaluation party especially — they are on their own GPU machines and schedule their own upgrades | us | They get their rules swapped out without knowing |
| 4 | Give a **migration window**: the old major keeps receiving patches until all three consumers have moved; no forced deadline | us | See `SCOPE.md` — we do not decide their integration schedule for them |
| 5 | Release `2.0.0`. **Do not** slip a breaking change into a 1.x patch or minor — consumers pin exact versions so they will not auto-upgrade, but **any new consumer** installs code that behaves differently from the running system | us | Old and new consumers compute two different results from the same on-chain data, and nothing anywhere raises an error |
| 6 | When the switch point arrives, the backend gates the rule change on the competition, not on "everyone has upgraded by now" | backend | Upgrade time ≠ rule-effective time; mix the two and nothing can be reproduced |

### 5.3 Two things explicitly not done

- **No in-place edit of 1.x.** Not even to "correct an obviously wrong constant" —
  that wrong constant has already decided a batch of miners' earnings, so changing
  it is changing history (§5.1).
- **No using a breaking change to force consumers to upgrade.** The cost of the
  evaluation party not upgrading lands on miners, not on us.

---

## 6. When this runbook goes stale

- If `release.yml` changes its environment name or its filename: the trusted
  publisher record on PyPI has to change **at the same time**, or the next release
  goes red at the OIDC exchange;
- If uv ever fixes the behaviour where a `[tool.uv.sources]` override discards the
  `==` constraint: §4 item 2 has to be re-measured (it records what uv 0.10.11 /
  0.11.18 do, not a promise).

---

## 7. Status of the current release

> This section is **the state of the current release** and is **replaced at the next
> one**; the process itself is §2, do not write a second copy of it here.
> It deliberately **hardcodes no version number** — the previous version left
> "pending: 0.7.0" here, three releases went by after 0.7.0 shipped and nobody came
> back to it, so following it literally would re-cut a tag that is already spent
> (PyPI version numbers cannot be reused, see §3).

**Current state**: nothing pending. The latest published version is whatever PyPI
and `git tag` say:

```bash
curl -s https://pypi.org/pypi/openroboto-protocol/json | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])'
git tag --list 'v*' --sort=-v:refname | head -1
```

When releasing, fill the table below in with the actual state, and clear it back to
the line above once it is out:

| Item | State |
|---|---|
| `version` in `pyproject.toml` / `uv.lock` | ⬜ same PR as the change |
| The pin examples quoted in README / AGENTS.md / `pyproject.toml` | ⬜ guarded by `test_quoted_pin_examples_do_not_drift` |
| `CHANGELOG.md` has an entry for this version | ⬜ including "who has to act" |
| `uv run pytest` · `bash scripts/lint.sh` · 100% coverage · `uv build` | ⬜ all green locally |

**Not done (needs Cameron's nod each time)**: `git push`, opening the PR, pushing
the tag. Anything that leaves the machine is an outward action.

**The commands to run after approval**, in order. The first three put the commits on
main; only the fourth is the publishing action (replace `<version>` with the number
in `pyproject.toml`):

```bash
cd ~/Workspace/openroboto/rebuild/openroboto-protocol
git push -u origin HEAD:release-<version>    # ① push the local commits
gh pr create --base main --fill              # ② PR, so CI runs the three jobs of §2 step 2
gh pr merge --squash                         # ③ merge once green

# ④ the only publishing action — tag on main:
git checkout main && git pull
git tag v<version> && git push origin v<version>
```

⚠️ A tag can only point at a commit **already merged into main**, so ①②③ are not
optional.
⚠️ Run `git tag --list 'v*'` first to confirm the number has not been used — PyPI
does not allow a re-upload (§3).
`release.yml` then stops in the `pypi` environment for approval; go through the
three checks in §2 step 4 once more. **There is no undo after that** (§3).

Then continue with §2 step 5 (verify it installs in a clean environment) and step 6
(push it to the consumers, backend first, CLI second).
