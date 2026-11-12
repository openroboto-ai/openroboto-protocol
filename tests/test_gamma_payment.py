"""Exact amounts and independently reproducible Gamma payment evidence."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace
from decimal import localcontext

import pytest

from openroboto_protocol import gamma_payment
from openroboto_protocol.gamma_payment import (
    GAMMA_UNITS_PER_GAMMA,
    GammaSpend,
    gamma_amount_units,
    gamma_payment_reference,
    validate_gamma_payment,
)


@pytest.mark.parametrize(
    ("amount", "units"),
    [
        (1, 1_000_000_000),
        (0.1, 100_000_000),
        ("21.377078702", 21_377_078_702),
        ("0.000000001", 1),
        (1e-9, 1),
        ("1.0000000000", 1_000_000_000),
        ("18446744073.709551615", 2**64 - 1),
    ],
)
def test_exact_amounts(amount: str | int | float, units: int) -> None:
    assert gamma_amount_units(amount) == units


@pytest.mark.parametrize(
    "amount",
    [
        True,
        False,
        "",
        "not-a-number",
        "NaN",
        "sNaN",
        float("nan"),
        float("inf"),
        float("-inf"),
        0,
        -1,
        "-0.0",
        "0.0000000001",
        "1.0000000001",
        "18446744073.709551616",
        2**64,
    ],
)
def test_invalid_amounts_are_never_rounded(amount: str | int | float) -> None:
    with pytest.raises(ValueError):
        gamma_amount_units(amount)


def test_amount_conversion_does_not_depend_on_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 2
        assert gamma_amount_units("18446744073.709551615") == 2**64 - 1
        assert gamma_amount_units("21.377078702") == 21_377_078_702
        with pytest.raises(ValueError, match="nine decimal"):
            gamma_amount_units("21.3770787021")


def test_reference_has_fixed_domain_order_encoding_and_length() -> None:
    encoded = (
        b'["openroboto/task-submission/gamma/v1",93,27,"hotkey",'
        b'"miner/\\u9898\\u5305","0123456789abcdef"]'
    )
    reference = gamma_payment_reference(
        netuid=93,
        competition_id=27,
        hotkey="hotkey",
        hf_repo_id="miner/题包",
        hf_commit="0123456789abcdef",
    )
    assert reference == "0x" + hashlib.sha256(encoded).hexdigest()
    assert len(reference) == 66


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("netuid", 94),
        ("competition_id", 28),
        ("hotkey", "other-hotkey"),
        ("hf_repo_id", "miner/other-package"),
        ("hf_commit", "b" * 40),
    ],
)
def test_reference_binds_every_submission_identity_field(
    field: str, value: str | int
) -> None:
    identity = {
        "netuid": 93,
        "competition_id": 27,
        "hotkey": "hotkey",
        "hf_repo_id": "miner/package",
        "hf_commit": "a" * 40,
    }
    original = gamma_payment_reference(**identity)
    identity[field] = value
    assert gamma_payment_reference(**identity) != original


def _spend() -> GammaSpend:
    return GammaSpend(
        netuid=93,
        coldkey="owner",
        hotkey="hotkey",
        spender="owner",
        amount_units=7_123_456_789,
        reference="0x" + "a" * 64,
    )


def _validate(spends: list[GammaSpend]) -> GammaSpend:
    return validate_gamma_payment(
        spends,
        netuid=93,
        hotkey="hotkey",
        coldkey="owner",
        reference="0x" + "a" * 64,
        expected_units=7_123_456_789,
    )


def test_exact_payment_returns_the_matching_event() -> None:
    spend = _spend()
    assert _validate([replace(spend, hotkey="other-key"), spend]) is spend


def test_event_record_is_immutable() -> None:
    spend = _spend()
    with pytest.raises(FrozenInstanceError):
        spend.amount_units = 1


@pytest.mark.parametrize(
    "change",
    [
        {"netuid": 94},
        {"hotkey": "other-key"},
        {"reference": "0x" + "b" * 64},
    ],
)
def test_unrelated_payment_is_not_payment_for_this_submission(
    change: dict[str, str | int],
) -> None:
    with pytest.raises(ValueError, match="gamma_payment_not_found"):
        _validate([replace(_spend(), **change)])


def test_no_spent_events_cannot_prove_payment() -> None:
    with pytest.raises(ValueError, match="gamma_payment_not_found"):
        _validate([])


def test_duplicate_matching_spends_are_ambiguous() -> None:
    with pytest.raises(ValueError, match="gamma_payment_ambiguous"):
        _validate([_spend(), _spend()])


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"coldkey": "other-owner"}, "gamma_payer_not_owner"),
        ({"spender": "other-spender"}, "gamma_spender_not_owner"),
        ({"amount_units": 7_123_456_788}, "gamma_amount_mismatch"),
        ({"amount_units": 7_123_456_790}, "gamma_amount_mismatch"),
    ],
)
def test_payment_must_match_owner_spender_and_exact_amount(
    change: dict[str, str | int], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        _validate([replace(_spend(), **change)])


def test_public_surface_is_pinned() -> None:
    assert set(gamma_payment.__all__) == {
        "GAMMA_UNITS_PER_GAMMA",
        "GammaSpend",
        "gamma_amount_units",
        "gamma_payment_reference",
        "validate_gamma_payment",
    }
    assert GAMMA_UNITS_PER_GAMMA == 1_000_000_000
    assert all(hasattr(gamma_payment, name) for name in gamma_payment.__all__)
