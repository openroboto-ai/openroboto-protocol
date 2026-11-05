"""The task-package digest: one number the miner, the admission worker and the
backend must compute identically.

The golden value below was produced by the validator's implementation at
``e5fee86a`` (``task_source/package.py``) against the same fixture. It is a
cross-repository contract, not a value this repository chose: if this test goes
red, one side drifted and a miner would pay the entry fee against a digest the
subnet does not store.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from openroboto_protocol import package_digest as pd

#: Produced by validator ``task_source.package.package_digest`` at e5fee86a on
#: exactly the four files written by ``_digest_fixture`` below.
GOLDEN_DIGEST = "c9ed7c7266f5dd8dae6126c9df2012791ec3065a3ea302222277eb9c8e2b6ece"


def _digest_fixture(root: Path) -> Path:
    package = root / "pkg"
    (package / "dir").mkdir(parents=True)
    (package / "a").mkdir()
    (package / "root.txt").write_bytes(b"hello\n")
    (package / "dir" / "b.txt").write_bytes(b"world\n")
    (package / "a" / "c.txt").write_bytes(b"nested\n")
    (package / "a-b.txt").write_bytes(b"\x00binary")
    return package


def test_digest_matches_the_validator_golden(tmp_path: Path) -> None:
    """The one assertion that keeps the three layers on the same number."""
    assert pd.package_digest(_digest_fixture(tmp_path)) == GOLDEN_DIGEST


def test_files_are_sorted_as_paths_not_as_strings(tmp_path: Path) -> None:
    """`a/c.txt` comes before `a-b.txt` under ``Path`` ordering.

    A string sort puts `a-b.txt` first (``-`` sorts before ``/``), which
    produces a different digest on exactly the packages that mix nested and
    dashed names. The validator sorts paths; this module has to.
    """
    files = pd.package_files(_digest_fixture(tmp_path))
    assert [relative for relative, _ in files] == [
        "a/c.txt",
        "a-b.txt",
        "dir/b.txt",
        "root.txt",
    ]
    assert files == (
        ("a/c.txt", hashlib.sha256(b"nested\n").hexdigest()),
        ("a-b.txt", hashlib.sha256(b"\x00binary").hexdigest()),
        ("dir/b.txt", hashlib.sha256(b"world\n").hexdigest()),
        ("root.txt", hashlib.sha256(b"hello\n").hexdigest()),
    )


def test_every_regular_file_counts_including_dotfiles(tmp_path: Path) -> None:
    package = _digest_fixture(tmp_path)
    before = pd.package_digest(package)
    (package / ".DS_Store").write_bytes(b"metadata")
    assert pd.package_digest(package) != before, (
        "the validator walks every regular file with no whitelist; a stray file "
        "changes the digest and this implementation must see it too"
    )


def test_digest_is_independent_of_the_directory_name(tmp_path: Path) -> None:
    first = _digest_fixture(tmp_path / "one")
    second = _digest_fixture(tmp_path / "two")
    assert pd.package_digest(first) == pd.package_digest(second)


def test_the_root_is_resolved_before_paths_are_made_relative(tmp_path: Path) -> None:
    """``..`` in the root, or a symlinked root, must not reach the digest."""
    package = _digest_fixture(tmp_path)
    roundabout = package / "dir" / ".."
    assert pd.package_digest(roundabout) == GOLDEN_DIGEST


def test_an_empty_directory_hashes_the_empty_string(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert pd.package_files(empty) == ()
    assert pd.package_digest(empty) == hashlib.sha256(b"").hexdigest()


def test_digest_of_files_does_not_reorder_its_input(tmp_path: Path) -> None:
    """Ordering belongs to ``package_files()``; folding a hand-built list in a
    different order is a different number, not a repaired one."""
    files = pd.package_files(_digest_fixture(tmp_path))
    assert pd.digest_of_files(files) == GOLDEN_DIGEST
    assert pd.digest_of_files(reversed(files)) != GOLDEN_DIGEST


def test_symlinks_follow_the_frozen_rglob_behaviour(tmp_path: Path) -> None:
    """A symlink to a regular file is read and counted; a symlinked directory
    is not descended into.

    This is the walk the digest was frozen with. Consumers must not add a
    follow-or-skip policy of their own — a package whose digest depends on the
    consumer's symlink policy cannot be compared across machines.
    """
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "real.txt").write_bytes(b"real")
    os.symlink(package / "real.txt", package / "link.txt")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.txt").write_bytes(b"hidden")
    os.symlink(outside, package / "linkdir")

    files = pd.package_files(package)
    assert [relative for relative, _ in files] == ["link.txt", "real.txt"]
    assert files[0] == ("link.txt", hashlib.sha256(b"real").hexdigest())


def test_public_surface_is_pinned() -> None:
    """`__all__` is what SemVer promises about (AGENTS.md §1②)."""
    assert set(pd.__all__) == {"digest_of_files", "package_digest", "package_files"}
    assert all(hasattr(pd, name) for name in pd.__all__)
