"""Model fingerprint: fold the LFS content of a HuggingFace repo into a single
64-character hex string.

Contract meaning
----------------
Two submissions with the same fingerprint = the same weights. The backend uses
this to judge plagiarism: when two fingerprints collide within one round, the
one with the earlier on-chain ``commit_block`` is the original and the later one
is marked ``rejected``. So this algorithm must be independently recomputable by
miners — if what they compute disagrees with the backend, it is the miner's
money that pays for it.

**The empty string is a sentinel value, not a fingerprint.** The backend
rejects an empty string outright (``reject_reason=model_hash_empty``); it means
"this repo gave us no LFS fingerprint at all", not "the fingerprint happens to
be empty".

Only the algorithm was moved here, not the I/O
----------------------------------------------
Fetching the repo's file list stays in the caller (the backend scanner uses the
HF tree API: the token, the timeout, the retries, and the decision not to retry
on 404 all live over there). The moment this package makes a network request,
the two sides can no longer be proven identical — and "being provable
identical" is its only reason to exist.

HF has two listing endpoints with different shapes, and both can be assembled
here:

- ``/api/models/<id>/tree/<rev>?recursive=true`` → entries carry a ``type``
  field, use :func:`model_hash_from_hf_tree` directly;
- the ``siblings`` / ``lfsFiles`` fallback path of ``/api/models/<id>`` →
  **no** ``type`` field, so the caller assembles it itself with
  :func:`extract_lfs_sha256` + :func:`fingerprint_lfs_sha256`.

Not responsible for
-------------------
- No network access, no token handling, no retries;
- Not validating that a string is a well-formed 64-character sha256
  (production never validates it, and this stays consistent with production);
- Deciding who the original is after a fingerprint collision is up to the
  backend, by ``commit_block``, not here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any, Final

# The three parameters of the fingerprint algorithm. Changing any one of them =
# every historical fingerprint on chain becomes void, and the plagiarism
# judgement goes wrong along with it. Touching them requires a major bump plus a
# migration plan for the on-chain data.
_SEPARATOR: Final = "\n"
_ENCODING: Final = "utf-8"

# HF's LFS fields have appeared in the prefixed string form ``sha256:<hex>``,
# so accept it.
_SHA256_PREFIX: Final = "sha256:"


def extract_lfs_sha256(lfs_field: object) -> str:
    """Pull the sha256 out of the ``lfs`` field of an HF tree entry; return an
    empty string if there is none.

    Contract: in the LFS field HF's REST API **gives only ``oid``, never a
    ``sha256`` key**, and that ``oid`` is the sha256 of the content —
    huggingface_hub maps it the same way itself
    (``BlobLfsInfo(sha256=lfs["oid"])``). Both keys are accepted, ``sha256``
    taking precedence.
    Evidence from real data: the tree response of the round 1 champion's repo
    contains only ``oid``, so the ``or oid`` fallback is the **main path**, not
    defensive code.

    ⚠️ Only the two shapes ``dict`` and ``"sha256:..."`` string are accepted,
    matching production. A caller using the huggingface_hub SDK gets a
    ``BlobLfsInfo`` **object** as ``RepoFile.lfs``, which matches neither
    branch → it silently gets an empty string → no fingerprint can be computed
    for the whole repo. If you want to use the SDK, convert it to
    ``{"sha256": info.sha256}`` yourself first.

    A non-string sha value deliberately raises ``TypeError`` instead of being
    skipped silently: in production it would blow up at the ``"\\n".join``
    step, be caught by the caller's ``except Exception``, and reject the whole
    thing. Skipping silently would turn "should have been rejected" into
    "compute a fingerprint from the remaining files", and that is a change of
    behavior.
    """
    if isinstance(lfs_field, dict):
        sha = lfs_field.get("sha256", "") or lfs_field.get("oid", "")
        if not isinstance(sha, str):
            raise TypeError(f"LFS sha256 must be a string, got {type(sha).__name__}")
        return sha
    if isinstance(lfs_field, str) and lfs_field.startswith(_SHA256_PREFIX):
        return lfs_field[len(_SHA256_PREFIX) :]
    return ""


def fingerprint_lfs_sha256(lfs_sha256s: Iterable[str]) -> str:
    """A set of LFS sha256s → a single fingerprint. An empty collection returns
    an empty string (= no fingerprint).

    Algorithm: sort lexicographically → join with newlines → encode as UTF-8 →
    sha256 → lowercase hex. The sort makes the file order irrelevant — the same
    weights uploaded in a different order must give the same fingerprint,
    otherwise a miner could "launder" plagiarism just by re-uploading.

    Duplicate shas are not deduplicated (the same content appearing twice in a
    repo contributes two entries), matching production.
    """
    ordered = sorted(lfs_sha256s)
    if not ordered:
        return ""
    return hashlib.sha256(_SEPARATOR.join(ordered).encode(_ENCODING)).hexdigest()


def model_hash_from_hf_tree(entries: Iterable[Mapping[str, Any]]) -> str:
    """The return value of the HF tree API (``?recursive=true``) → fingerprint.

    Only entries with ``type == "file"`` are considered — the type HF returns
    for a directory is ``"directory"``, and directory entries have no ``lfs``
    either, so both layers block them.

    **Only LFS files go into the fingerprint.** Small files that travel as
    ordinary git blobs (``config.json``, ``norm_stats.json``,
    ``round_info.json``) do not take part, so what the fingerprint covers is the
    weights themselves: editing one line of ``round_info.json`` does not change
    the fingerprint. This is intentional — a plagiarist cannot escape by
    changing the metadata.
    """
    return fingerprint_lfs_sha256(
        sha
        for entry in entries
        if entry.get("type") == "file" and (sha := extract_lfs_sha256(entry.get("lfs")))
    )
