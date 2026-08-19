"""Protocol constants — every one of them states its unit and its dimension.

Writing down the unit is not fastidiousness. The old comment on
`CHAMPION_MARGIN` read `min avg_score margin to dethrone king (2%)` while the
value was `0.01` (an absolute difference in avg_score, not a percentage). That
"(2%)" has already misled someone once. The emission weights are worse: the
repo holds two sets of numbers (0.07/0.02/0.01 and 0.70/0.20/0.10) and nowhere
says that they are measured differently, so whichever set you happen to see is
the one you assume is real.

**What this module is not responsible for**: backend parameters that are
tunable at runtime. Per ADR 01 the single source of backend behavior parameters
is `backend.yaml`, and `control.json` only carries payment / dataset / training
/ process. What lives here are the protocol constants that "both sides must
understand identically"; every item that `backend.yaml` can override says so
explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ─────────────────────────────────────────────────────────────────────────────
# Emission weights
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EmissionWeights:
    """Top-K emission weights. **Two ways of measuring the same thing, bound
    together so that there can be only one input.**

    The repo holds two sets of numbers for the same thing, and nothing anywhere
    explains the difference between them:

    ========================  ====================================  ==============
    Numbers                   Meaning                               In effect?
    ========================  ====================================  ==============
    ``[0.07, 0.02, 0.01]``    share of **total network emissions**  **in effect**
    ``[0.70, 0.20, 0.10]``    relative split **among** the Top-3    never shipped
    ========================  ====================================  ==============

    Mathematically they agree: ``0.70 × (1 − 0.90 burn) = 0.07``. So the two are
    not a contradiction, they are two expressions of the same thing — but mixing
    them is guaranteed to blow up, because whether rank 1 gets "0.7" or "0.07"
    is a factor of ten.

    The relative form appears in the control.json produced by
    `owner/tools/round_controller.py:98` (under the key ``emission_weights``).
    Measured on 2026-08-17, https://api.openroboto.ai/control.json **does not
    have that key** — it never shipped, and on-chain emissions have always used
    the absolute form.

    Only the absolute shares are stored here; the relative shares and the burn
    share are both **computed**, so they can never disagree.
    """

    #: The share of total network emissions taken by each of ranks 1..K.
    #: Descending, and the sum is < 1 (the remainder is burned).
    #: Dimension: a dimensionless ratio, 1.0 = total network emissions.
    absolute: tuple[float, ...]

    @property
    def top_k(self) -> int:
        """Number of slots on the board. The length of the board is exactly the
        number of weights — the two numbers must not be written in two places."""
        return len(self.absolute)

    @property
    def relative(self) -> tuple[float, ...]:
        """The relative split **among** the Top-K (sums to 1.0). This is the
        form used by control.json."""
        total = sum(self.absolute)
        return tuple(w / total for w in self.absolute)

    @property
    def burn_share(self) -> float:
        """The part that did not make the board — all of it is burned. Same
        dimension as `absolute`.

        ⚠️ This is **an inference from the protocol's point of view** (1 − the
        Top-K sum). What production actually uses when writing to chain is
        `scanner.burn_ratio` from `backend.yaml` (live value 0.9). The two agree
        today; if either one is ever changed the other must be changed at the
        same time, otherwise the weights no longer sum to 1.
        """
        return 1.0 - sum(self.absolute)


#: The emission weights in the form that is actually in effect.
#: **Changing this is changing the money miners take home.**
TOP_K_EMISSION: Final[EmissionWeights] = EmissionWeights(
    absolute=(0.07, 0.02, 0.01),
)

#: Compatibility with the old name (`protocol/types.py:82` has always called it
#: this). A tuple rather than a list: constants should not be mutable.
TOP_K_EMISSION_WEIGHTS: Final[tuple[float, ...]] = TOP_K_EMISSION.absolute

#: Number of slots on the board (the length of the King of the Hill board).
#: Same source as the number of weights.
TOP_K: Final[int] = TOP_K_EMISSION.top_k


# ─────────────────────────────────────────────────────────────────────────────
# Dethroning threshold
# ─────────────────────────────────────────────────────────────────────────────

#: For a challenger to dethrone the champion, its avg_score must be **strictly
#: greater than** the champion's plus this value.
#:
#: Unit: an **absolute difference** in avg_score (avg_score itself is the mean
#: over the 6 suites, each in 0..1), so 0.01 = 0.01 points, **not 1%, and even
#: less the "(2%)" that the old comment claimed**.
#:
#: The comparison is strictly greater than, so when the difference is exactly
#: equal to the margin the challenge **fails**
#: (`chall_avg > target_avg + margin` in `backend/db/rankings.py:224`).
#: Deciding a tie against the challenger is the cornerstone of anti-plagiarism:
#: copying the champion's weights can only tie, and a tie loses on the margin.
#: Writing `>=` in any single place is a unilateral dismantling of this barrier.
#:
#: ⚠️ Can be overridden at runtime by `ranking.champion_margin` in
#: `backend.yaml` (the live value is also 0.01). This is the protocol default;
#: miners use it to estimate whether they can dethrone the champion, and if the
#: two sides disagree the miner burned TAO for nothing.
CHAMPION_MARGIN: Final[float] = 0.01


# ─────────────────────────────────────────────────────────────────────────────
# Burn-to-commitment window
# ─────────────────────────────────────────────────────────────────────────────

#: How many blocks may separate the burn transaction from the chain commitment.
#:
#: Unit: **blocks**, not seconds. At Bittensor's ~12 s block time this is about
#: 10 minutes, but the check is on block numbers -- do not convert and compare
#: time.
#:
#: The rule exists to stop burn replay: a fee paid once cannot be attached to a
#: later submission, or to several. Exceeding the window is a **terminal
#: rejection and the TAO is not refunded**, so both sides must agree on the
#: number and on the comparison.
#:
#: Three details of the comparison are load-bearing, taken from the backend's
#: `scanner/burn_verify.py:68-75`:
#:
#: 1. the distance is ``abs(burn_block - commit_block)`` -- **symmetric**; a burn
#:    after the commitment counts just the same;
#: 2. rejection is ``> window``, so a distance of exactly the window is
#:    **accepted**. A consumer writing ``>=`` refuses submissions the backend
#:    would have taken;
#: 3. when either block number is 0 (unknown) the backend **skips the check
#:    entirely**. A consumer that is stricter here rejects submissions that
#:    would have been accepted.
#:
#: ⚠️ Can be overridden at runtime by `scanner.burn_block_window` in
#: `backend.yaml`. The live value is also 50 (verified 2026-08-19: read by
#: `backend/config.py:77`, enforced at `scanner/burn_verify.py:71`). Deployment
#: docs claimed 10 for a long time; production behaviour is what settled it, and
#: the miner-facing documents were corrected to 50.
#:
#: This lives here rather than in each consumer because it is exactly the kind of
#: number that must not exist twice: the CLI warns the miner with it, the backend
#: enforces with it, and if they ever disagree the miner burns TAO for a
#: submission that was already doomed.
BURN_BLOCK_WINDOW: Final[int] = 50


# ─────────────────────────────────────────────────────────────────────────────
# LIBERO evaluation environments
# ─────────────────────────────────────────────────────────────────────────────

#: The LIBERO task suites one evaluation has to run; the order is the order of
#: the `env_list` dispatched to the worker.
#: As of 2026-08-17, the `env_list` of the 117 live submissions and the
#: `env_scores` of the 82 scoring records are all exactly these 6 — not one
#: more, not one fewer.
LIBERO_TASK_SUITES: Final[tuple[str, ...]] = (
    "libero_spatial",
    "libero_object",
    "libero_goal",
    "libero_10",
    "libero_object_swap",
    "libero_spatial_swap",
)

#: The environments that must all be present at scoring time. Missing one gets
#: the whole thing rejected (`MISSING_ENVS`), and the task keeps its current
#: status so the worker can retry — the mean over 3 suites and the mean over 6
#: are not comparable, and mixing them into the same board is handing out free
#: points.
#: Same source as `LIBERO_TASK_SUITES`: there is no such half-configuration as
#: "run 6 but only require 4".
REQUIRED_ENVS: Final[frozenset[str]] = frozenset(LIBERO_TASK_SUITES)


# ─────────────────────────────────────────────────────────────────────────────
# drand random beacon
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DrandBeacon:
    """The three parameters of a drand chain, bound into one record — they can
    only ever be changed together.

    `genesis_time` and `period_seconds` decide "which round a given point in
    time corresponds to", and `chain_hash` decides "which chain that round is
    fetched from". Cross any one of them and the round you compute points at a
    random number on a different chain, the seed changes with it, and **once the
    seed changes, historical evaluations are no longer reproducible** (spec §5).
    Writing the three as separate module constants is what gives you the chance
    to cross them.
    """

    #: drand chain identifier (also a segment of the API path).
    chain_hash: str
    #: Unix timestamp (seconds) of round 1 of that chain.
    genesis_time: int
    #: Beacon period (seconds).
    period_seconds: int


#: The drand chain used in production: the League of Entropy default chain
#: (beaconID `default`, schemeID `pedersen-bls-chained`).
#:
#: Checked against `https://api.drand.sh/<chain_hash>/info` on 2026-08-17:
#: period=30, genesis_time=1595431050, and the hash matches.
#:
#: The round conversion formula (`max(1, (ts - genesis) // period + 1)`) and the
#: network request that fetches the random number are both **not in this
#: module** — the former belongs to `seed.py`, the latter is I/O and belongs to
#: the backend.
#:
#: ⚠️ `seed.py` currently holds a second copy of `DRAND_CHAIN_HASH` with the
#: same value (used to build the URL). Two copies of one constant is exactly
#: what this package exists to eliminate; until they are converged into one
#: place, `tests/test_constants.py` has an assertion watching that the two are
#: equal, so it goes red the moment they drift.
DRAND_DEFAULT_CHAIN: Final[DrandBeacon] = DrandBeacon(
    chain_hash="8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce",
    genesis_time=1595431050,
    period_seconds=30,
)
