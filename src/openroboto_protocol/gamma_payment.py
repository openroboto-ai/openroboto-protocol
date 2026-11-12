"""Exact Gamma amounts and task-submission payment identity.

The miner CLI and backend share this contract: a payment names the same subnet,
competition, submitting hotkey and immutable repository revision on both sides.
Only an actual ``GammaSpent`` event can supply a ``GammaSpend``; transaction
success and ``GammaSpendSkipped`` events do not prove payment.

Chain access, event decoding, transaction recovery and replay prevention remain
in the consumers. This module performs no I/O and changes no commitment bytes.
Verify with ``uv run pytest tests/test_gamma_payment.py``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, cast

__all__ = [
    "GAMMA_UNITS_PER_GAMMA",
    "GammaSpend",
    "gamma_amount_units",
    "gamma_payment_reference",
    "validate_gamma_payment",
]

#: Gamma's on-chain precision: one Gamma is one billion integer units.
GAMMA_UNITS_PER_GAMMA: Final = 1_000_000_000
_MAX_AMOUNT: Final = Decimal("18446744073.709551615")
_REFERENCE_DOMAIN: Final = "openroboto/task-submission/gamma/v1"


def gamma_amount_units(amount: str | int | float) -> int:
    """Convert a positive Gamma amount to exact u64 units without rounding.

    Decimal strings preserve all nine fractional places. Floats use their
    decimal spelling, never binary floating-point multiplication. Non-finite,
    non-positive, fractional-unit and out-of-u64-range amounts are invalid.
    """
    if isinstance(amount, bool):
        raise ValueError("Gamma amount must be a number, not a boolean")
    try:
        value = Decimal(str(amount))
    except InvalidOperation as exc:
        raise ValueError("Gamma amount must be a decimal number") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError("Gamma amount must be finite and greater than zero")
    if value > _MAX_AMOUNT:
        raise ValueError("Gamma amount exceeds the on-chain u64 range")
    components = value.as_tuple()
    significant_digits = len(components.digits)
    while significant_digits > 1 and components.digits[significant_digits - 1] == 0:
        significant_digits -= 1
    unit_exponent = (
        cast(int, components.exponent) + len(components.digits) - significant_digits + 9
    )
    if unit_exponent < 0:
        raise ValueError("Gamma amount must be exact to at most nine decimal places")
    coefficient = 0
    for digit in components.digits[:significant_digits]:
        coefficient = coefficient * 10 + digit
    return coefficient * cast(int, 10**unit_exponent)


def gamma_payment_reference(
    *,
    netuid: int,
    competition_id: int,
    hotkey: str,
    hf_repo_id: str,
    hf_commit: str,
) -> str:
    """Bind a Gamma payment to one immutable task submission as 32-byte hex.

    The field order, domain, ASCII escaping and compact JSON encoding are the
    contract. Transaction hashes and block numbers are excluded so that payment
    recovery reproduces the reference before a transaction is included.
    """
    identity = [
        _REFERENCE_DOMAIN,
        netuid,
        competition_id,
        hotkey,
        hf_repo_id,
        hf_commit,
    ]
    encoded = json.dumps(identity, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return "0x" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class GammaSpend:
    """Facts from one GammaSpent event belonging to the payment transaction.

    Consumers decode chain addresses and the reference into their canonical
    SS58 and ``0x`` hex spellings before constructing this record. Amounts are
    integer Gamma units, never TAO or decimal Gamma.
    """

    netuid: int
    coldkey: str
    hotkey: str
    spender: str
    amount_units: int
    reference: str


def validate_gamma_payment(
    spends: Sequence[GammaSpend],
    *,
    netuid: int,
    hotkey: str,
    coldkey: str,
    reference: str,
    expected_units: int,
) -> GammaSpend:
    """Require exactly one self-spent payment for this submission and fee.

    Both the deposit owner and spender must be the submitting hotkey's owner
    coldkey. The exact fee is charged; neither underpayment nor overpayment is
    accepted. Missing, duplicate or mismatched evidence raises ``ValueError``.
    """
    matches = [
        spend
        for spend in spends
        if spend.netuid == netuid
        and spend.hotkey == hotkey
        and spend.reference == reference
    ]
    if not matches:
        raise ValueError("gamma_payment_not_found")
    if len(matches) != 1:
        raise ValueError("gamma_payment_ambiguous")
    spend = matches[0]
    if spend.coldkey != coldkey:
        raise ValueError("gamma_payer_not_owner")
    if spend.spender != coldkey:
        raise ValueError("gamma_spender_not_owner")
    if spend.amount_units != expected_units:
        raise ValueError("gamma_amount_mismatch")
    return spend
