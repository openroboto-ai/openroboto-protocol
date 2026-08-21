# openroboto-protocol

The shared contract between the OpenRoboto backend and everything that talks to it —
miners, the evaluation worker, and the web frontend. Bittensor **netuid 80**.

## Install

Pin the exact version. A floating range means two sides of the subnet can resolve
to different code, which is the failure this package was created to prevent.

```bash
uv add "openroboto-protocol==0.5.0"
# or
pip install "openroboto-protocol==0.5.0"
```

```python
from openroboto_protocol.seed import derive_seed, verify_seed

seed = derive_seed(block_hash, round_num, drand_random)
```

Python **3.11+** (miners and the evaluator run 3.11, the backend runs 3.12; CI
tests both). Ships `py.typed`, so your `mypy` sees the real types.

**The base install has zero dependencies** and CI enforces that from the built
wheel's metadata, not from a promise in a comment. A miner installs this to derive a
seed and decode a commitment; that must not cost a `pydantic-core` wheel build on a
GPU box. Only `schemas.py` needs pydantic, and only the backend needs `schemas.py`:

```bash
uv add "openroboto-protocol[schemas]==0.5.0"   # backend only
```

## Why this package exists

The seed derivation, the commitment codec and the status vocabulary are used by both
sides of the subnet. They used to live as hand-copied files in four separate
repositories, with no version number and no check that the copies agreed. They had
already drifted.

If the seed derivation changes, **every historical evaluation becomes
irreproducible**. That is not hypothetical — this package exists because it was one
copy-paste away from happening.

## What belongs here

| Module | Contract | Who needs both sides to agree |
| --- | --- | --- |
| `seed.py` | Seed derivation — block hash + round + drand randomness → uint32 | Backend derives it, miners verify it |
| `commitment.py` | Commitment payload encode / decode | Miners write it on chain, backend reads it |
| `model_hash.py` | Model fingerprinting | Both compute it; a mismatch rejects a submission |
| `model_format.py` | What a submittable checkpoint must contain | Miners export to it, the evaluator rejects against it |
| `status.py` | Task status and stage vocabulary | Backend, worker and frontend all render it |
| `schemas.py` | Request / response models for every API endpoint | Backend serves them, worker and CLI consume them |
| `constants.py` | `CHAMPION_MARGIN`, `REQUIRED_ENVS`, … | Ranking and admission both read them |

`model_format.py` exists because miners currently have to clone a *second*
repository to check their model before paying the submission fee:

```
uv run libero_eval/check_model.py --model <output_dir> --config pi05_libero
```

Getting it wrong burns TAO for nothing — the evaluator rejects bare LoRA
adapters at a pre-eval check. The rule is shared, so it belongs here, and
`openroboto check` reads it from this package.

**What never belongs here:** I/O of any kind, database access, secrets, backend
business logic. A pure function can be proven identical on both sides; a function
that makes an HTTP call cannot.

## Versioning is a promise

The version number *is* the contract version.

> ### ⚠️ `0.x` — the promise is not in force yet
>
> While the version is `0.x`, **compatibility is not promised**. The shapes in
> `schemas.py` and the vocabularies in `status.py` may still change without a
> major bump.
>
> This is deliberate, and it ends on a specific event, not on a date:
> **`openroboto-backend` and `openroboto-cli` have not picked their launch
> versions yet.** Neither of them can even install this package today — the
> backend still carries three hand-copied mirrors (`app/domain/worker_reports.py`,
> `app/domain/reasons.py`, the copied block in `app/api/envelope.py`), which is
> the very drift this package exists to end. Freezing a contract that has never
> been consumed would freeze whatever shape it happens to have, not the shape
> integration proves it needs.
>
> **`1.0.0` ships when backend and CLI lock their launch versions against it.**
> From that release on, the table below is binding and going back to `0.x`
> is not an option — `tests/test_version.py` enforces exactly that.

| Bump | Meaning |
| --- | --- |
| `patch` | Bug fix, behaviour unchanged |
| `minor` | New optional field. Old data missing the key **must** have a default |
| `major` | Breaking change. Requires an on-chain data migration plan and review |

Consumers pin an exact version (`openroboto-protocol==0.5.0`). Floating versions are
rejected in CI, as is any vendored copy of this code.

## What consumers must add to their own CI

Publishing this package does not, by itself, stop the drift — a repository can
install it *and* keep its old hand-copied `protocol/` directory, import the copy,
and nobody notices until an evaluation stops reproducing. That already happened:
`protocol/types.py` had drifted 105 lines and `payment.py` 313 lines across four
repositories before this package existed.

Both checks are now wired up, not just written down:
`openroboto-cli/.github/workflows/protocol-guards.yml` and the `ci` job of
`openroboto-backend/.github/workflows/ci.yml`. Copy them from there into any new
consumer.

### 1. No vendored copy

```yaml
      - name: No vendored protocol copy
        run: |
          copies="$(git ls-files '*protocol/*.py')"
          if [ -n "$copies" ]; then
            echo "::error::vendored copy of openroboto-protocol found:"
            echo "$copies"
            echo "delete it and import openroboto_protocol instead"
            exit 1
          fi
```

The `*protocol/*.py` pathspec catches nested copies too (`backend/protocol/status.py`
in the old prototype). It deliberately matches only `.py` files, so a `docs/protocol/`
directory of prose does not trip it.

That snippet is what `openroboto-backend` runs, where it passes. `openroboto-cli`
cannot run it yet: its `protocol/{__init__,seed,types}.py` are still on disk, and
that repository's own rule is that files inherited from `openroboto-subnet` are never
deleted, only stopped being used (`openroboto-cli/SCOPE.md`). So the cli variant
compares the file list against an explicit grandfathered set instead of requiring it
to be empty — see the workflow. Two properties matter: a **fourth** copy anywhere in
the repository is still red, and the day those three files are archived the list stops
matching and the exemption has to be deleted along with them. It is not
`continue-on-error` and it is not scoped to `src/` — a check that cannot fail on the
files it was written for is decoration.

The imports were the actual leak, and they are gone: nothing in `openroboto-cli`
imports `protocol.seed` any more (the two docs that told miners to do so now say
`openroboto_protocol.seed`), and `protocol/seed.py` was turned into a re-export shim,
so even a stale import gets this package's code rather than a copy that can drift.

Two files could not follow. `protocol/types.py` cannot become a shim because it has
**already** drifted — its `TOP_K_EMISSION_WEIGHTS` is the relative `[0.70, 0.20, 0.10]`
against this package's live absolute `(0.07, 0.02, 0.01)`, and its status vocabulary
shares no word with `status.py`. Re-exporting would silently swap those values, which
is changing behaviour, not moving code. It is deprecated whole instead. Two legacy
files still read `PI05_BASE_CHECKPOINT` and `VLAEpisode` from it, and neither symbol
exists here yet — whether they belong here at all is an open question.

`protocol/__init__.py` has no imports at all, deliberately. A package-level re-export
there would make `openroboto_protocol` a hard requirement of `from protocol.types
import …` (Python runs the parent `__init__` first), and the miners still on the old
`requirements.txt` training flow do not have this package installed. That would have
broken their training at the first line, silently, for people who are not on the team.
`openroboto-cli/tests/test_vendored_protocol.py` holds that shape in place.

### 2. Version is pinned

```yaml
      - name: Protocol version is pinned
        run: |
          python3 - <<'PY'
          import pathlib, sys, tomllib

          reqs: list[str] = []
          path = pathlib.Path("pyproject.toml")
          if path.is_file():
              data = tomllib.loads(path.read_text("utf-8"))
              project = data.get("project", {})
              reqs += project.get("dependencies", [])
              for group in project.get("optional-dependencies", {}).values():
                  reqs += group
              for group in data.get("dependency-groups", {}).values():
                  reqs += [item for item in group if isinstance(item, str)]
          for req_file in sorted(pathlib.Path().glob("requirements*.txt")):
              reqs += [
                  line.split("#", 1)[0].strip()
                  for line in req_file.read_text("utf-8").splitlines()
              ]

          floating = [
              req for req in reqs
              if req.replace(" ", "").startswith("openroboto-protocol")
              and "==" not in req
          ]
          if floating:
              print("::error::openroboto-protocol must be pinned to an exact version:")
              print("\n".join(floating))
              sys.exit(1)
          print(f"pin ok ({len(reqs)} requirements scanned)")
          PY
```

This parses the dependency tables rather than grepping lines, which the earlier
line-based version of this snippet did. Grepping does not survive contact with real
repositories: `openroboto-backend/pyproject.toml` carries a paragraph of comments
explaining why this package is not installed yet, and `openroboto-cli/pyproject.toml`
has a `[tool.uv.sources]` entry plus its own comments — six lines total that mention
`openroboto-protocol` without a `==`, none of them a dependency. The old snippet
failed on both repositories for reasons that had nothing to do with pinning.

Ceilings, both deliberate. A `[tool.uv.sources]` path or git override is **not**
flagged: `openroboto-cli` currently needs one because this package is not published,
and the pin still applies on top of it. And the check reads `pyproject.toml` and
`requirements*.txt` only — a `constraints.txt` or a `Dockerfile` `pip install` line
slips through. Add the file to the loop if a repository grows one.

The pin check belongs in *consumer* repositories only. Run it inside
`openroboto-protocol` itself and it fires on this package's own dev group, which
depends on `openroboto-protocol[schemas]` unpinned by design.

## Golden vectors

`tests/test_golden_vectors.py` (seeds) and `tests/test_model_golden_vectors.py`
(model fingerprints and real HuggingFace repository layouts) hold input/output pairs
that already happened on chain. They are facts, not expectations — changing one is
changing history, and that history decided who got paid.

Three historical seeds (uid 60 / 194 / 192) cannot be reproduced from their stored
inputs. They are recorded in the irreproducible list with the reason, and are
deliberately **not** golden vectors. Two model fingerprints whose HuggingFace
revisions have since been deleted are excluded for the same reason: the input is
gone, so the test could only ever be red.

New golden files must be named `tests/test_*golden_vectors.py` — that is the glob the
dedicated CI job runs.

## Development

```bash
uv sync                                          # install, including dev group
bash scripts/lint.sh                             # ruff check + ruff format --check + mypy strict
uv run coverage run --source=src -m pytest -q    # tests
uv run coverage report --fail-under=100          # the gate
uvx pre-commit install                           # optional: run the lint hooks before each commit
```

CI runs `scripts/lint.sh` itself rather than repeating the commands, so green
locally means green there. `pre-commit` is deliberately not in the dev dependency
group: CI installs that group on every run and has no use for it.

`--source=src` is not optional: without it `coverage` also measures pytest itself,
the denominator inflates and the 100% gate stops meaning anything.

## CI

`.github/workflows/ci.yml`, three jobs:

| Job | What it means when it is red |
| --- | --- |
| `golden vectors (RED = on-chain history was rewritten)` | Someone changed an input/output pair that already happened on chain. Not a test failure — a claim that the past was wrong |
| `lint + tests (py3.11)` / `(py3.12)` | ruff, `mypy --strict` on `src`, pytest, and **coverage < 100%** |
| `build (packaging gate; publish lives in release.yml)` | `uv build` broke, `twine check --strict` rejected the metadata, the wheel is missing modules or `py.typed`, or it grew an **unconditional** dependency (one without an `extra ==` marker — that one lands on every miner's machine) |

The matrix is not decoration: miners and the evaluator run 3.11 (the subnet
`Dockerfile` hardcodes `python3.11`), the backend runs 3.12, and this package is
installed into both.

The coverage gate is 100%, not 90%. The package is a few hundred lines and every
one of them decides whether an evaluation reproduces, whether a burn counts, or who
gets emissions. There is no line here that does not matter.

## Release

A `v*` tag is the only publishing action. `.github/workflows/release.yml` takes it
from there; nobody runs `uv publish` from a laptop.

```bash
uv version --bump patch       # or minor|major — see the table above
# commit the bump in the same PR as the change it describes, let CI go green, merge
git tag v1.0.1 && git push origin v1.0.1
```

The tag triggers, in order:

1. **The same gates every PR runs.** `release.yml` calls `ci.yml` with
   `workflow_call` instead of restating a release-specific checklist — a second copy
   would be the weaker one, and nobody maintains two. Golden vectors, 3.11 + 3.12,
   100% coverage, `twine check`, and installing the built wheel to confirm every
   module imports and `py.typed` shipped.
2. **Tag/version agreement**, twice: `ci.yml` compares the tag against
   `pyproject.toml`, and the publish job compares it against the artifact filenames
   about to be uploaded. A consumer that pinned an exact version and got something
   else voids the only reason this package exists.
3. **A human.** The publish job runs in the `pypi` GitHub environment, which can
   require a reviewer. PyPI never lets a version number be reused, not even after a
   delete, so a wrong upload can only be walked forward with another patch release.
4. **PyPI Trusted Publishing (OIDC).** No long-lived API token exists to leak. The
   credential is minted for that one run, and PyPI additionally checks it came from
   this repository, this workflow file and that environment. Same reasoning as
   `openroboto-backend` using Workload Identity Federation instead of a service
   account key.

The publish job downloads the artifact the gates produced rather than rebuilding, so
what reaches PyPI is the exact file that passed. It does not check the repository out
at all: the only thing running with the OIDC token is `uv publish`.

There is deliberately **no TestPyPI step**. The one thing it would add is "the index
accepted an upload", and `twine check --strict` plus installing the wheel and
importing every module already cover more than that. The cost is a second trusted
publisher and a second environment to keep in sync — two configurations where only
one will stay correct.

## License

MIT
