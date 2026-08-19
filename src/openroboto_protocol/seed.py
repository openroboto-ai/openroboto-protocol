"""Evaluation seed derivation — the reddest red line in the subnet.

**What it promises**: given three public inputs (the hash of the block that
contains the commitment, the subnet round, and the randomness of the drand
quicknet beacon), anyone can compute exactly the same uint32 seed as the
backend. The formula is public, and publishing it gives nothing away: at the
moment a miner submits a commitment, the hash of the block that will contain it
is not yet decided, and the drand randomness of the round that follows does not
exist yet either — nobody can compute their own seed in advance.

**What it is not responsible for**:
- It does not fetch drand (``fetch_drand`` lives in the backend, it has to make
  a network request).
- It does not work out which drand round to use from the block timestamp (also
  left in the backend's I/O half).
- It does not do the secondary derivation from base seed to a per-LIBERO-task
  seed (that lives in the public evaluation toolchain).
- What to do when drand is unavailable is not decided here: the backend **must
  block and retry**, and must never be allowed to degrade into "just use
  block_hash" — that would change the already published formula, and it would
  cut two independent sources of entropy down to one.

**Who consumes it**: the backend derives the seed before evaluation; miners /
auditors recompute it from the public values on chain and on the beacon, in
order to verify that the backend did not hand-pick a seed for a particular
person.

**Minimal way to verify**: ``uv run pytest tests/test_golden_vectors.py`` —
it holds input/output pairs that really happened on chain.

Behavior source: ``prototype/backend/seed.py`` and the public repo
``openroboto-cli/protocol/seed.py`` (the two are identical word for word).
**Not one byte was changed** when it was moved into this package; historical
evaluations must stay reproducible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# Public identifier of the drand quicknet chain. Changing the chain = changing
# the entropy source = nothing in the history is reproducible any more, which
# makes it a major change.
DRAND_CHAIN_HASH = "8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce"

# HTTP endpoint of the public drand beacon. It lives here only so that auditors
# can assemble a URL they can check against; this package itself never visits it
# (zero I/O is the precondition for this package existing at all).
DRAND_API = "https://api.drand.sh"

# Upper bound of the seed's value range. It is part of the contract, not an
# implementation detail: the seed takes the last 4 bytes of a SHA256 digest, so
# the maximum is 4294967295 — beyond the range of a PostgreSQL INTEGER. The
# column that stores the seed must be BIGINT; production went through one online
# migration for this
# (prototype/backend/database.py:438 _migrate_seed_to_bigint).
SEED_MAX = 0xFFFFFFFF


def derive_seed(block_hash: str, round_num: int, drand_random: str) -> int:
    """Derive this round's evaluation uint32 seed from three public inputs.

    The formula (already published externally; changing one character is
    changing history)::

        message = UTF8("{block_hash}:{round_num}:{drand_random}")
        seed    = big_endian_uint32(SHA256(message)[-4:])

    All three arguments take part in the concatenation **exactly as given**,
    with no normalization whatsoever — no stripping of the ``0x`` prefix, no
    case changes, no whitespace stripping. The block_hash stored in the
    production database is lowercase hex with the ``0x``; the drand randomness
    is lowercase hex without a prefix; a different spelling is a different seed.
    """
    # The three lines below are word-for-word identical to
    # prototype/backend/seed.py:26 and to the public repo
    # openroboto-cli/protocol/seed.py:18.
    # `.encode()` and `.encode("utf-8")` produce the same bytes, and UP012 wants
    # us to drop the argument — but keeping it word for word is what makes it
    # obvious at a glance that extracting this package "only moved things, it
    # did not change them". So the rule stays silenced.
    seed_input = f"{block_hash}:{round_num}:{drand_random}".encode("utf-8")  # noqa: UP012
    digest = hashlib.sha256(seed_input).digest()
    return int.from_bytes(digest[-4:], byteorder="big")


def verify_seed(
    expected_seed: int,
    block_hash: str,
    round_num: int,
    drand_random: str,
) -> bool:
    """The auditor's direction: was a published seed really computed from these
    three public inputs.

    Returning False only says that this record's seed does not match **the
    inputs stored with it**; it does not distinguish between the backend
    computing it wrong, the inputs being overwritten after the fact, and legacy
    data — deciding the cause is left to the caller.
    """
    return expected_seed == derive_seed(block_hash, round_num, drand_random)


def drand_round_url(drand_round: int | str = "latest") -> str:
    """Assemble the public query URL for one round of drand randomness (it only
    builds a string, it sends no request).

    ``"latest"`` means the most recent round. The round must be a positive
    integer: drand rounds start at 1, and in the backend 0 is the sentinel value
    meaning "which round to use has not been worked out yet", not a round that
    can be queried.
    """
    if drand_round != "latest" and (
        not isinstance(drand_round, int) or drand_round <= 0
    ):
        raise ValueError("drand_round must be a positive integer or 'latest'")
    return f"{DRAND_API}/{DRAND_CHAIN_HASH}/public/{drand_round}"


@dataclass(frozen=True, slots=True)
class SeedInputs:
    """The three inputs of one evaluation seed, bound into a single immutable
    value.

    They are bound together because they **must come from the same source**: all
    three fields have to come from the same submission. Pairing A's block_hash
    with B's drand_random silently computes a uint32 that looks perfectly
    normal, and nothing anywhere raises an error — this kind of mismatch can
    only be blocked at the type level, which is why this dataclass exists.

    ``frozen`` is not fastidiousness: once seed inputs are recorded they are
    history, and changing them is changing who got paid.
    """

    #: hash of the block containing the commitment; the production format is
    #: lowercase hex with the ``0x`` prefix
    block_hash: str
    #: subnet round (not the drand round — do not mix the two up)
    round_num: int
    #: the drand quicknet randomness of that round, lowercase hex without the
    #: ``0x`` prefix
    drand_random: str

    def derive(self) -> int:
        """Derive the uint32 seed that corresponds to this set of inputs."""
        return derive_seed(self.block_hash, self.round_num, self.drand_random)

    def verify(self, expected_seed: int) -> bool:
        """Check whether a published seed really came out of this set of inputs."""
        return self.derive() == expected_seed
