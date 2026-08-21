"""Weight normalisation.

Every case here pins a property that a "cleanup" would plausibly change, and
each one changes who gets paid. The arithmetic used to exist twice -- once in
the backend, once in the CLI -- with nothing comparing them; these are the
assertions that make one copy enough.
"""

from __future__ import annotations

import pytest

from openroboto_protocol import weights
from openroboto_protocol.weights import U16_MAX, normalize_weights

HK_A = "5Aaa" + "0" * 44
HK_B = "5Bbb" + "0" * 44
HK_C = "5Ccc" + "0" * 44
HK_BURN = "5Bur" + "0" * 44


def test_index_in_the_hotkey_list_is_the_uid() -> None:
    """The metagraph list is positional; its index is the uid the chain wants."""
    result = normalize_weights({HK_C: 1.0}, [HK_A, HK_B, HK_C])

    assert result.uids == [2]


def test_zero_weight_is_left_out_not_written_as_zero() -> None:
    """`weight > 0` is strictly greater.

    Leaving a miner out and writing an explicit zero for them are different
    statements to the chain. The filter is what makes the difference, so it is
    asserted rather than assumed.
    """
    result = normalize_weights({HK_A: 0.5, HK_B: 0.0}, [HK_A, HK_B])

    assert result.uids == [0]
    assert result.weights == [U16_MAX]


def test_negative_weight_is_left_out_too() -> None:
    """Same branch, opposite sign. A negative share is nonsense, and the chain
    takes unsigned values -- letting one through would be an overflow, not a
    small mistake."""
    assert normalize_weights({HK_A: -1.0}, [HK_A]).uids == []


def test_truncation_matches_what_is_already_on_chain() -> None:
    """`int()` truncates, and snapshot 122 is the proof.

    In that snapshot the burn address holds 0.9 of the total, and
    `0.9 * 65535` is exactly `58981.5`. Truncation gives 58981, rounding would
    give 58982 -- so switching to `round()` **rewrites a value that already
    settled on chain**. That is why the exact integer is asserted, not just the
    property.

    The same truncation is why the u16 values sum to 65533 rather than 65535.
    The chain accepts the shortfall; making the sum land exactly would change
    every validator's weights.

    ⚠️ Both former copies illustrated this with `1/3 → 21844`, which is wrong:
    `(1/3) * 65535` is exactly `21845.0`, asserted below. A wrong example
    invites someone to "correct" the code to match it.
    """
    hotkeys = [HK_BURN, HK_A, HK_B, HK_C]
    result = normalize_weights(
        {HK_BURN: 0.9, HK_A: 0.07, HK_B: 0.02, HK_C: 0.01}, hotkeys
    )

    assert result.weights == [58981, 4587, 1310, 655]
    assert sum(result.weights) == U16_MAX - 2
    assert round(0.9 * U16_MAX) == 58982, "rounding really would differ"

    equal = normalize_weights({HK_A: 1.0, HK_B: 1.0, HK_C: 1.0}, [HK_A, HK_B, HK_C])
    assert equal.weights == [21845, 21845, 21845]
    assert sum(equal.weights) == U16_MAX


def test_share_first_then_scale() -> None:
    """`w / total` then `* U16_MAX`, not `w * U16_MAX / total`.

    The two are not equal in floating point. This input is one where they
    differ, so reordering the expression fails here instead of drifting
    unnoticed into somebody's emissions.
    """
    weights_raw = {HK_A: 0.07, HK_B: 0.02, HK_C: 0.01}
    hotkeys = [HK_A, HK_B, HK_C]

    got = normalize_weights(weights_raw, hotkeys).weights

    total = sum(weights_raw.values())
    assert got == [int((w / total) * U16_MAX) for w in weights_raw.values()]


def test_unknown_hotkeys_are_dropped_and_the_rest_renormalised() -> None:
    """A hotkey that deregistered is not on the metagraph, so it gets no weight
    and the survivors split the whole allocation.

    This is production behaviour, and it is silent: the caller sees a valid
    weight table, not an error.
    """
    result = normalize_weights({HK_A: 1.0, "5Gone" + "0" * 43: 3.0}, [HK_A, HK_B])

    assert result.uids == [0]
    assert result.weights == [U16_MAX], "survivor should take the whole allocation"


def test_no_positive_weights_yields_empty_lists() -> None:
    """Nothing to send.

    🔴 The caller must **not** send an extrinsic here, and must not treat it as
    routine: it means no miner is paid this round. Production once hit exactly
    this -- the response shape changed, every lookup missed, `positive` came out
    empty, and `set_weights` was never called. No exception, no non-2xx, just a
    line saying "No positive weights" and network emissions at zero.
    """
    result = normalize_weights({}, [HK_A, HK_B])

    assert result.uids == []
    assert result.weights == []
    assert result.detail == ["no positive weights"]


def test_detail_records_both_sides_of_the_conversion() -> None:
    """The log lines are the only evidence left when weights turn out wrong.

    Raw input and resulting u16 both have to be in there; either alone leaves
    you unable to tell a bad input from a bad conversion.
    """
    detail = " ".join(normalize_weights({HK_A: 0.07}, [HK_A]).detail)

    assert "raw=0.070000" in detail
    assert f"u16={U16_MAX:5d}" in detail


def test_empty_metagraph_is_not_an_error() -> None:
    """No registered hotkeys yet: nothing to weight, and nothing to crash on."""
    assert normalize_weights({HK_A: 1.0}, []).uids == []


def test_public_surface_is_pinned() -> None:
    """`__all__` is the SemVer promise made concrete.

    Without a declared surface there is no criterion separating `patch`
    (behaviour unchanged) from `major` (breaking): removing a helper nobody
    knows is public counts as whichever the reviewer feels like. Pinning it here
    makes a change to the surface show up as a failing test, in the same PR that
    has to choose the version bump.
    """
    assert weights.__all__ == ["U16_MAX", "NormalizedWeights", "normalize_weights"]


def test_dropped_share_reports_what_went_missing() -> None:
    """The share that belonged to absent hotkeys, as a fraction of the input.

    Callers refuse to send on a large value. The number has to be right for
    that decision to mean anything, so it is asserted exactly rather than
    "greater than zero".
    """
    result = normalize_weights(
        {"burn": 0.9, "a": 0.07, "b": 0.02, "c": 0.01}, ["a", "b", "c"]
    )

    assert result.dropped_share == pytest.approx(0.9)
    # What remains is still renormalised over itself -- reporting the loss does
    # not change the arithmetic.
    assert sum(result.weights) == pytest.approx(U16_MAX, abs=4)


def test_dropped_share_is_zero_when_everyone_is_present() -> None:
    result = normalize_weights({"a": 0.7, "b": 0.3}, ["a", "b"])

    assert result.dropped_share == 0.0


def test_dropped_share_is_one_when_nobody_is_present() -> None:
    """Every hotkey gone: no uids to send, and the caller should hear why.

    Distinguishing this from an empty snapshot matters -- one means the chain
    moved on without us, the other means nothing was scored.
    """
    result = normalize_weights({"a": 0.7, "b": 0.3}, ["someone-else"])

    assert result.uids == []
    assert result.dropped_share == 1.0


def test_dropped_share_of_an_empty_snapshot_is_zero_not_a_crash() -> None:
    """No input to lose. Zero, not a division by zero."""
    assert normalize_weights({}, ["a"]).dropped_share == 0.0
