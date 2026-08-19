"""The version number and the install example in the README must be the same
number.

Why this deserves a test: the only selling point of this package is "both sides
can be proven to have installed the same thing", and what consumers copy is that
`uv add "openroboto-protocol==X"` line in the README. When the README says 1.0.0
while `pyproject.toml` is still at 0.1.0, whoever copies it cannot install;
worse, after a version bump only `pyproject.toml` gets edited and the README goes
on pinning every new consumer to an old version — nothing raises an error, it is
only discovered some day when the two sides turn out not to be running the same
code.

The version is read from the **metadata of the installed distribution**, not
grepped out of the `pyproject.toml` text: that is the value a consumer sees after
`pip install`.
"""

from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[1] / "README.md"

# Matches `openroboto-protocol==1.0.0`, and the form with an extra,
# `openroboto-protocol[schemas]==1.0.0`. The version segment is required to start
# with a digit, so the `grep -v 'openroboto-protocol=='` line in the CI snippet
# (no version number after the `==`) is not mistaken for a pin.
_PIN = re.compile(r"openroboto-protocol(?:\[[^\]]*\])?==([0-9][^\"'\s`)]*)")


def test_readme_pins_match_the_installed_version() -> None:
    """Every exact pin appearing in the README must equal this package's
    version."""
    pins = _PIN.findall(README.read_text(encoding="utf-8"))
    assert pins, (
        "README has no `openroboto-protocol==<version>` pin at all — "
        "the install example is gone"
    )
    assert set(pins) == {version("openroboto-protocol")}, (
        f"README pins {sorted(set(pins))} but the package version is "
        f"{version('openroboto-protocol')}"
    )


def test_version_and_the_compatibility_promise_move_together() -> None:
    """The version number and whether compatibility is promised must move
    together; one must not be changed without the other.

    ⚠️ This is not a version-format check, it is **the enforcer of a ruling**.

    It used to assert `major >= 1` ("a contract package must not be released as
    0.x"). That changed on 2026-08-19: 0.x is allowed, but the documentation
    **must** state that compatibility is not promised during this period. The
    reason is that `openroboto-backend` and `openroboto-cli` have not settled on
    the versions they go live with — today the two of them cannot even install
    this package (the backend still keeps 3 hand-copied duplicates). Freezing a
    version number on a contract that has never actually been consumed freezes
    the shape it **happens to have grown into**.

    So this test now guards two directions, and both of them are shapes that fail
    silently:

    1. **0.x with no warning** — consumers assume from SemVer intuition that
       `0.1.x` releases are compatible with each other, while we may change the
       shape of `schemas.py` at any moment. Nobody raises an error; they just
       parse the wrong data.
    2. **1.0+ that still carries the warning** — the contract is already frozen,
       yet the documentation still says compatibility is not promised, so
       consumers do not dare pin a version and keep hand-copying — exactly what
       this package exists to eliminate.

    Once `1.0.0` is out there is no going back to 0.x: that would be withdrawing
    a promise that already took effect, and the version a consumer has pinned
    with `==` will not find out about it by itself.
    """
    root = Path(__file__).resolve().parents[1]
    major = int(version("openroboto-protocol").split(".")[0])
    warned = "compatibility is not promised" in (root / "README.md").read_text(
        "utf-8"
    ) and "不承诺兼容" in (root / "AGENTS.md").read_text("utf-8")
    assert _promise_mismatch(major, warned) is None, _promise_mismatch(major, warned)


def _promise_mismatch(major: int, warned: bool) -> str | None:
    """The criterion is extracted into a pure function so that **both
    directions** can be tested.

    Writing it as `if major == 0: assert … else: assert …` would leave the other
    half unreachable forever (today's version is 0.x), which would turn the 100%
    coverage gate red — and marking that half as no cover would be admitting that
    half of this rule has never been verified. Yet the half it guards is
    precisely the **future** one: 1.0 goes out and the documentation forgets to
    remove the warning.
    """
    if major == 0 and not warned:
        return (
            "version is 0.x, but neither README nor AGENTS.md carries the "
            "'compatibility is not promised' warning"
            " — consumers will assume SemVer compatibility"
        )
    if major > 0 and warned:
        return (
            f"version is already {major}.x (the contract is in force), yet the docs "
            "still say compatibility is not promised"
            " — consumers will keep hand-copying instead of pinning"
        )
    return None


@pytest.mark.parametrize(
    ("major", "warned", "bad"),
    [
        (0, True, False),  # today: 0.x + warning present
        (0, False, True),  # 0.x with no warning — consumers assume compatibility
        (1, False, False),  # after 1.0: contract in effect, warning removed
        # 1.0 and the warning is still there — consumers do not dare pin a
        # version and keep hand-copying
        (1, True, True),
        (2, True, True),  # same reasoning for higher majors
    ],
)
def test_promise_mismatch_catches_both_directions(
    major: int, warned: bool, bad: bool
) -> None:
    """Two cases for each direction. Drop either one and the silent failure in
    that direction is left unguarded."""
    assert (_promise_mismatch(major, warned) is not None) is bad
