"""Weight normalisation — the last step before emissions land on chain.

**What it promises**: given the backend's `{hotkey: share}` and the metagraph's
hotkey list, everyone computes the same `(uid, u16)` pair. The backend's
chain-writer and every external validator running the CLI feed the chain from
this one function, so "the backend computed one thing and the validator wrote
another" cannot happen by divergence.

It used to happen by copying. Two implementations existed --
`openroboto-backend/app/services/chain_writer.py` and
`openroboto-cli/src/openroboto/chain/weights.py` -- byte-for-byte identical in
arithmetic and different in every name, return type and log string. Nothing
compared them. Two copies of an expression whose floating-point shape *is* the
behaviour is the definition of a red line worth centralising: a "cleanup" in
one of them, months apart, silently changes who gets paid.

**What it is not responsible for**:

- Deciding the shares. That is King-of-the-Hill, and it stays in the backend
  (`app/domain/ranking.py`) -- it needs scores, history and a database.
- Sending the extrinsic. Both callers keep their own `set_weights` because the
  SDK result shapes and the retry policy differ between them.
- Knowing which hotkeys are registered. The caller passes the metagraph list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["U16_MAX", "NormalizedWeights", "normalize_weights"]

#: Maximum weight the chain accepts, as an unsigned 16-bit integer.
U16_MAX: Final = 65535


@dataclass(frozen=True, slots=True)
class NormalizedWeights:
    """What `set_weights` needs: two parallel lists plus the evidence.

    `uids` and `weights` are positional pairs, not a mapping, because that is
    the shape the chain call takes.
    """

    uids: list[int]
    #: u16 in [0, 65535]. **Not shares** -- shares are what came in.
    weights: list[int]
    #: Per-entry lines, written straight to the log. When weights turn out
    #: wrong, this is the only evidence of what went in and what came out.
    detail: list[str]


def normalize_weights(
    weights_raw: dict[str, float], hotkeys: list[str]
) -> NormalizedWeights:
    """Turn `{hotkey: share}` into the `(uid, u16)` lists the chain takes.

    🔴 **The shape of these expressions is the behaviour. Do not tidy them.**
    Three ways that has already gone wrong:

    - `weight > 0` is strictly greater. A miner on zero is **left out of the uid
      list**, not written as a zero. Writing an explicit zero is a different
      statement to the chain.
    - `share = w / total` first, then `share * U16_MAX` -- **not**
      `w * U16_MAX / total`. Those are not equal in floating point.
    - `int(...)` truncates, it does not round. The evidence is on chain: in
      snapshot 122 the burn address holds 0.9 of the total, and
      `0.9 * 65535 == 58981.5` exactly. `int` gives 58981, `round` gives 58982 --
      **switching to `round()` rewrites a value that is already on chain.**
      Truncation is also why the u16 values sum to slightly under 65535 (65533
      for that snapshot); the chain accepts the shortfall.

      ⚠️ Both former copies illustrated this with `1/3 → int(21844.999…) = 21844`.
      That example is wrong -- `(1/3) * 65535` is exactly `21845.0` and three
      equal shares sum to exactly 65535 -- and being wrong, it invited someone
      to "correct" the code to match it. The property is real; that
      illustration never was.

    Hotkeys present in `weights_raw` but absent from `hotkeys` (a miner who
    deregistered) are dropped, and what remains is renormalised over its own
    total. That is the behaviour in production; callers that want to notice it
    should diff the two key sets themselves before calling.

    Args:
        weights_raw: shares keyed by hotkey, as the backend computed them.
        hotkeys: the metagraph's hotkey list. **The index is the uid.**

    Returns:
        The uid list, the u16 list, and the per-entry log lines. Empty lists
        when nothing has a positive weight -- the caller must not send an
        extrinsic in that case, and must treat it as an event worth reporting:
        it means no miner is being paid this round.
    """
    positive: dict[int, float] = {}
    detail: list[str] = []
    for uid, hotkey in enumerate(hotkeys):
        weight = weights_raw.get(hotkey, 0.0)
        if weight > 0:
            positive[uid] = weight
            detail.append(f"  uid={uid:3d} hotkey={hotkey[:12]}... raw={weight:.6f}")

    if not positive:
        return NormalizedWeights([], [], ["no positive weights"])

    total = sum(positive.values())
    normed = {uid: weight / total for uid, weight in positive.items()}

    uids = list(normed.keys())
    weights = [int(share * U16_MAX) for share in normed.values()]

    detail.append(f"  raw total={total:.6f}, normalized to sum=1.0")
    detail.extend(
        f"  → uid={uid:3d} u16={w:5d} ({normed[uid]:.6f})"
        for uid, w in zip(uids, weights, strict=True)
    )
    return NormalizedWeights(uids, weights, detail)
