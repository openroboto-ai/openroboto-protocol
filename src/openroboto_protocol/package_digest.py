"""The content identity of a task package — one rule for the miner, the
admission worker and the backend.

A task season (``competitions.track = "task"``) takes a **task package**
instead of a model checkpoint. The package is a directory: ``task.json`` plus
the instance payloads, manifests and (for AXIS) the scene and reference
solution. The commitment names the HuggingFace repository and commit, and the
package digest is how all three sides prove they are looking at the same bytes.

## The rule, exactly

1. Recurse over **every regular file** under the package root, with no
   whitelist and no exclusions — ``reference.json``, ``scene.xml`` and a stray
   ``.DS_Store`` all count.
2. Sort the paths **as paths**, not as strings.
3. Per file, ``sha256(read_bytes())``.
4. Digest the UTF-8 bytes of ``"\\n".join(f"{path}\\0{file_sha256}")``.

The sorting detail is not pedantry: ``Path`` orders by parts, so ``a/c.txt``
comes before ``a-b.txt``, while a string sort puts ``a-b.txt`` first (``-``
sorts before ``/``). A second implementation that sorts strings produces a
different number on exactly the packages that mix nesting and dashes, and the
miner pays for the difference. ``package_files()`` therefore returns the paths
in the order the rule hashes them; ``digest_of_files()`` does not re-sort.

## The filesystem boundary

This is the package's **only** module that reads the filesystem, and it reads
nothing else: no environment, no clock, no network. Its output is a pure
function of the bytes under ``directory``, so two machines holding the same
package compute the same number. The digest cannot be computed from a file list
alone, which is why the walk lives here rather than being left as a rule each
consumer re-implements — that replication is what this module exists to
eliminate. Everything that needs a decision beyond "which bytes are under this
path" (whether the package is well-formed, whether a referenced path escapes
the root) stays in the consumer.

The walk is ``pathlib.Path.rglob("*")``, and its symlink behaviour is part of
the rule as it was frozen: a symlink to a regular file is read and counted, a
symlink to a directory is not descended into. Consumers must not add a
follow-or-skip policy of their own.

## What it is not responsible for

- Validating the package (``task.json``, ``schema_version``, declared instance
  counts, payload references). That is the validator's ``task_source`` layer.
- Deciding what a digest mismatch means. The admission worker reports the
  value, the backend stores it, and the evaluation layer compares it to the
  ``EvaluationSpec`` it was dispatched with.
- Downloading the package or choosing which directory is the package root.

## Who consumes it

- ``openroboto-cli`` — before the entry fee is paid, so a miner can see the
  number the subnet will store;
- the admission worker — the reported ``package_digest``;
- the evaluation layer — ``EvaluationSpec.task.version_digest``.

The backend stores the reported value and does not recompute it, so it does
not need this module.

## Minimal verification

``uv run pytest tests/test_package_digest.py``. Its golden digest was produced
by the validator's implementation at ``e5fee86a`` against the same fixture; a
red case means one of the two copies drifted and miners would compute a number
the subnet does not store.
"""

from __future__ import annotations

import hashlib
import pathlib
from collections.abc import Iterable

#: The public surface of this module. What is not listed here is an
#: implementation detail and may change in a patch release (AGENTS.md §1②).
__all__ = ["digest_of_files", "package_digest", "package_files"]


def package_files(directory: str | pathlib.Path) -> tuple[tuple[str, str], ...]:
    """``(relative POSIX path, sha256 of its bytes)`` for every regular file.

    The tuple is ordered exactly as the digest hashes it: ``Path`` order over
    the recursive walk, which is not string order. ``directory`` is expanded
    and resolved first, so the same tree reached through a symlinked root
    yields the same relative paths.
    """

    root = pathlib.Path(directory).expanduser().resolve()
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        entries.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(entries)


def digest_of_files(files: Iterable[tuple[str, str]]) -> str:
    """Fold ``package_files()``'s output into the package digest.

    The input order is significant and is the order ``package_files()``
    returns; this function does no sorting of its own. An empty sequence hashes
    the empty string, which is what an empty package directory produces.
    """

    encoded = "\n".join(f"{relative}\0{digest}" for relative, digest in files)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def package_digest(directory: str | pathlib.Path) -> str:
    """The content identity of the task package rooted at ``directory``."""

    return digest_of_files(package_files(directory))
