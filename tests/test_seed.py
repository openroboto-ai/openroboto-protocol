"""Contract tests for ``openroboto_protocol.seed``.

The real on-chain vectors live in ``test_golden_vectors.py``; what is tested here
are the **properties and boundaries**: determinism of the formula, its range, its
sensitivity to how the inputs are spelled, and which negative cases must be
rejected.
"""

from __future__ import annotations

import dataclasses

import pytest

from openroboto_protocol.seed import (
    DRAND_API,
    DRAND_CHAIN_HASH,
    SEED_MAX,
    SeedInputs,
    derive_seed,
    drand_round_url,
    verify_seed,
)

# The example promised to external auditors in the public document
# openroboto-cli/docs/SEED_GENERATION.md. Like the golden vectors it is a public
# commitment, and it must not be deleted just because it "looks like filler".
DOC_BLOCK_HASH = "0x" + "11" * 32
DOC_ROUND = 1
DOC_DRAND = "22" * 32
DOC_SEED = 3898936287


def test_documented_example() -> None:
    """That assert in the public document has to keep holding, forever."""
    assert derive_seed(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND) == DOC_SEED


def test_is_deterministic() -> None:
    """Same input, same output — no time, no randomness, no environment
    variables involved."""
    first = derive_seed(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    second = derive_seed(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    assert first == second


def test_stays_in_uint32_range() -> None:
    """The last 4 bytes of the digest are taken, so the result necessarily lands
    in [0, 4294967295]. The column storing it must be BIGINT."""
    assert SEED_MAX == 4294967295
    for i in range(64):
        seed = derive_seed(f"0x{i:064x}", i, f"{i:064x}")
        assert 0 <= seed <= SEED_MAX


@pytest.mark.parametrize(
    ("block_hash", "round_num", "drand_random"),
    [
        ("0x" + "11" * 32, 2, "22" * 32),  # only round changed
        ("0x" + "12" * 32, 1, "22" * 32),  # only block_hash changed
        ("0x" + "11" * 32, 1, "23" * 32),  # only drand randomness changed
    ],
)
def test_every_input_participates(
    block_hash: str, round_num: int, drand_random: str
) -> None:
    """All three arguments really do go into the hash — if any one of them
    changes, the seed must change."""
    assert derive_seed(block_hash, round_num, drand_random) != DOC_SEED


def test_inputs_are_not_normalised() -> None:
    """The arguments are concatenated verbatim: case, the ``0x`` prefix and
    whitespace are all meaningful.

    Production stores lowercase hashes with the ``0x`` prefix. If someone strips
    the prefix or uppercases it "while they are at it", what comes out is a
    different seed and the whole history falls apart — which is why this
    difference has to be pinned down.
    """
    lower = derive_seed("0xabcd", 1, "ef01")
    assert derive_seed("0xABCD", 1, "ef01") != lower
    assert derive_seed("abcd", 1, "ef01") != lower
    assert derive_seed(" 0xabcd", 1, "ef01") != lower


def test_separator_is_part_of_the_message() -> None:
    """``:`` is part of the message format, not an interchangeable separator —
    swap it and it is a different formula."""
    assert derive_seed("a:b", 1, "c") != derive_seed("a", 1, "b:c")


def test_no_input_validation() -> None:
    """An empty string / round 0 does not raise; it still produces a uint32.

    This is the **current behaviour**, not a good idea by design: it means that
    when an upstream caller forgets to pass a field nothing blows up, a
    normal-looking seed is computed silently. Validation is the caller's
    responsibility (when drand cannot be fetched the backend must block the task
    rather than derive from an empty string). Changing this = breaking change.
    """
    assert derive_seed("", 0, "") == 4092634947


def test_verify_seed_accepts_and_rejects() -> None:
    """The auditor's direction: True when it matches, and False whenever any one
    of the inputs is mismatched."""
    assert verify_seed(DOC_SEED, DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    assert not verify_seed(DOC_SEED + 1, DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    assert not verify_seed(DOC_SEED, DOC_BLOCK_HASH, DOC_ROUND + 1, DOC_DRAND)
    assert not verify_seed(DOC_SEED, "0x" + "99" * 32, DOC_ROUND, DOC_DRAND)
    assert not verify_seed(DOC_SEED, DOC_BLOCK_HASH, DOC_ROUND, "99" * 32)


def test_drand_round_url_for_a_recorded_round() -> None:
    """Auditors take this URL to check the beacon, so the shape of the path is
    itself part of the contract."""
    assert drand_round_url(6347967) == (
        f"{DRAND_API}/{DRAND_CHAIN_HASH}/public/6347967"
    )


def test_drand_round_url_defaults_to_latest() -> None:
    """No round given means the latest round."""
    assert drand_round_url() == f"{DRAND_API}/{DRAND_CHAIN_HASH}/public/latest"
    assert drand_round_url("latest") == drand_round_url()


@pytest.mark.parametrize("bad_round", [0, -1, "6347967", "", "LATEST", 1.0])
def test_drand_round_url_rejects_non_rounds(bad_round: object) -> None:
    """The negative cases that must be rejected: 0 is the sentinel value for "no
    round computed yet"; string rounds, floats and negative numbers are not
    rounds either."""
    with pytest.raises(ValueError, match="positive integer"):
        drand_round_url(bad_round)  # type: ignore[arg-type]


def test_drand_chain_hash_is_quicknet() -> None:
    """Changing the chain hash = changing the entropy source = every piece of
    history becomes unreproducible. Pin it down."""
    assert DRAND_CHAIN_HASH == (
        "8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce"
    )
    assert DRAND_API == "https://api.drand.sh"


def test_seed_inputs_derive_matches_function() -> None:
    """The dataclass only binds the three fields together; the derived result
    must be identical to calling the function directly."""
    inputs = SeedInputs(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    assert inputs.derive() == DOC_SEED
    assert inputs.verify(DOC_SEED)
    assert not inputs.verify(DOC_SEED + 1)


def test_seed_inputs_is_frozen() -> None:
    """Once the seed inputs are recorded they are history, and rewriting them in
    place is not allowed."""
    inputs = SeedInputs(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    with pytest.raises(dataclasses.FrozenInstanceError):
        inputs.block_hash = "0x00"  # type: ignore[misc]


def test_seed_inputs_compares_by_value() -> None:
    """Same-origin checks rely on value equality, not object identity; they can
    go into a set or be used as a dict key."""
    a = SeedInputs(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    b = SeedInputs(DOC_BLOCK_HASH, DOC_ROUND, DOC_DRAND)
    c = SeedInputs(DOC_BLOCK_HASH, DOC_ROUND + 1, DOC_DRAND)
    assert a == b
    assert a != c
    assert len({a, b, c}) == 2
