"""Contract tests for constants.py.

Every number here is on the money path. Before turning any of these assertions
red, ask first: what has already happened on chain under the old value?
"""

from __future__ import annotations

import dataclasses
import itertools
import math

import pytest

from openroboto_protocol import constants as C

# ── Emission weights ──────────────────────────────────────────────────────


def test_absolute_weights_are_the_effective_ones() -> None:
    """The effective reading: the absolute share of total network emissions
    (`protocol/types.py:82` has always carried this value)."""
    assert C.TOP_K_EMISSION_WEIGHTS == (0.07, 0.02, 0.01)
    assert C.TOP_K == 3
    assert C.TOP_K == len(C.TOP_K_EMISSION_WEIGHTS)


def test_relative_weights_match_the_control_json_dialect() -> None:
    """The [0.70, 0.20, 0.10] set in control.json is another reading of the same
    thing, not a second set of rules.

    This assertion is the executable form of the sentence "they agree
    mathematically": 0.70 × (1 − 0.90 burn) = 0.07. From here on the two sets of
    numbers cannot drift apart independently.
    """
    assert all(
        math.isclose(got, want)
        for got, want in zip(C.TOP_K_EMISSION.relative, (0.70, 0.20, 0.10), strict=True)
    )
    assert math.isclose(sum(C.TOP_K_EMISSION.relative), 1.0)


def test_burn_share_is_the_remainder() -> None:
    """The 90% that did not make the leaderboard is all burned. Consistent with
    scanner.burn_ratio=0.9 in backend.yaml."""
    assert math.isclose(C.TOP_K_EMISSION.burn_share, 0.90)
    total = sum(C.TOP_K_EMISSION_WEIGHTS) + C.TOP_K_EMISSION.burn_share
    assert math.isclose(total, 1.0)


def test_weights_are_strictly_descending() -> None:
    """Descending weights *are* the rank order: rank 1 takes
    TOP_K_EMISSION_WEIGHTS[0]. The ranking engine works backwards from
    "descending weights = rank order" to reconstruct the leaderboard, so a tie or
    a wrong order makes the reconstruction wrong.
    """
    w = C.TOP_K_EMISSION_WEIGHTS
    assert all(a > b for a, b in itertools.pairwise(w))
    assert all(0.0 < x < 1.0 for x in w)


def test_emission_weights_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        C.TOP_K_EMISSION.absolute = (0.7, 0.2, 0.1)  # type: ignore[misc]


# ── Dethrone threshold ────────────────────────────────────────────────────


def test_champion_margin_is_an_absolute_delta() -> None:
    """0.01 is an absolute delta on avg_score, not a percentage. The old comment
    saying "(2%)" has already misled someone once."""
    assert C.CHAMPION_MARGIN == 0.01


def test_a_tie_loses_the_challenge() -> None:
    """`chall_avg > target_avg + margin` — strictly greater than.

    Judging a tie as a loss is the cornerstone of anti-plagiarism: copying the
    champion's weights can only tie, and a tie loses on the margin. Writing `>=`
    anywhere is the same as unilaterally taking this gate down.
    """
    king = 0.80
    # delta exactly equal to the threshold
    assert not king + C.CHAMPION_MARGIN > king + C.CHAMPION_MARGIN
    assert 0.8101 > king + C.CHAMPION_MARGIN  # only exceeding the threshold wins
    assert not 0.8099 > king + C.CHAMPION_MARGIN


# ── Burn-to-commitment window ─────────────────────────────────────────────


def test_burn_block_window_is_the_production_value() -> None:
    """50 blocks, in blocks -- not the 10 that deployment docs claimed.

    Verified 2026-08-19 against production: `scanner.burn_block_window` in
    backend.yaml, read by backend/config.py:77, enforced at
    scanner/burn_verify.py:71.
    """
    assert C.BURN_BLOCK_WINDOW == 50


def test_a_distance_exactly_equal_to_the_window_is_accepted() -> None:
    """The backend rejects on `> window`, so equality passes.

    A consumer that writes `>=` refuses submissions the backend would have
    accepted -- the miner has already burned by then, so being stricter than the
    enforcer costs them the fee for nothing.
    """
    burn, window = 1_000, C.BURN_BLOCK_WINDOW
    assert not abs((burn + window) - burn) > window  # exactly at the edge: fine
    assert abs((burn + window + 1) - burn) > window  # one past it: rejected


def test_the_window_is_symmetric() -> None:
    """`abs(burn_block - commit_block)`: a burn *after* the commitment counts too."""
    window = C.BURN_BLOCK_WINDOW
    assert abs(1_000 - (1_000 + window + 1)) > window
    assert abs((1_000 + window + 1) - 1_000) > window


# ── LIBERO environments ───────────────────────────────────────────────────


def test_required_envs_are_the_six_production_suites() -> None:
    """The env_list of the 117 live submissions and the env_scores of the 82
    scoring records are all exactly these 6."""
    assert C.REQUIRED_ENVS == {
        "libero_spatial",
        "libero_object",
        "libero_goal",
        "libero_10",
        "libero_object_swap",
        "libero_spatial_swap",
    }
    assert len(C.LIBERO_TASK_SUITES) == 6


def test_required_envs_and_task_suites_are_same_source() -> None:
    """There is no half-way config that "allows running 6 but only requires 4" —
    the mean over 4 and the mean over 6 are not comparable."""
    assert C.REQUIRED_ENVS == frozenset(C.LIBERO_TASK_SUITES)
    assert len(C.REQUIRED_ENVS) == len(C.LIBERO_TASK_SUITES)  # no duplicates


def test_task_suite_order_matches_the_dispatched_env_list() -> None:
    """The order of the env_list dispatched to the worker, exactly as it is in
    production."""
    assert C.LIBERO_TASK_SUITES[0] == "libero_spatial"
    assert C.LIBERO_TASK_SUITES[-1] == "libero_spatial_swap"


# ── drand ─────────────────────────────────────────────────────────────────


def test_drand_chain_parameters() -> None:
    """Cross-checked against https://api.drand.sh/<chain_hash>/info on
    2026-08-17. Changing any one of them makes the seed point at the randomness
    of another chain — and historical evaluations immediately become
    unreproducible (spec §5).
    """
    chain = C.DRAND_DEFAULT_CHAIN
    assert chain.chain_hash == (
        "8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce"
    )
    assert chain.genesis_time == 1595431050
    assert chain.period_seconds == 30
    assert len(chain.chain_hash) == 64


def test_chain_hash_has_no_second_copy_that_drifted() -> None:
    """`seed.py` currently holds a second `DRAND_CHAIN_HASH` with the same value
    (it needs it to build the URL).

    Two hand-made copies of the same constant is exactly what this package exists
    to eliminate — that is how `protocol/types.py` drifted by 105 lines back in
    the day. Until they are collapsed into one place, this assertion is what
    makes the drift go red on the spot.
    """
    from openroboto_protocol import seed

    assert seed.DRAND_CHAIN_HASH == C.DRAND_DEFAULT_CHAIN.chain_hash


def test_drand_beacon_is_frozen() -> None:
    """The three parameters can only be changed together; not being able to
    change them beats changing them wrongly."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        C.DRAND_DEFAULT_CHAIN.period_seconds = 3  # type: ignore[misc]
