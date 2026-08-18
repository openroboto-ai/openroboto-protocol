"""OpenRoboto subnet protocol contract — the single source of truth.

Everything in this package is consumed by BOTH sides of the subnet: the private
backend that scores submissions, and the public miner / evaluation code that
produces them. Before this package existed the same algorithms lived as
hand-copied files across four repositories and had already drifted
(``protocol/types.py`` by 105 lines, ``payment.py`` by 313).

Three rules make this package worth existing:

1. **No I/O, no database, no secrets.** Pure functions and plain data only.
   The moment this package fetches something over the network, the two sides
   can no longer be proven to agree.
2. **The version number IS the contract version.** SemVer is enforced:
   ``patch`` fixes a bug without changing behaviour, ``minor`` adds an optional
   field (old data missing the key must have a default), ``major`` is a
   breaking change and needs an on-chain data migration plan plus review.
3. **Golden vectors are history.** Inputs and outputs that already happened on
   chain are frozen as fixtures. Changing them is changing the past — a past
   that decided who got paid.

Consumers must not vendor a copy of this code; their CI checks for it.
"""

from __future__ import annotations

__all__: list[str] = []
