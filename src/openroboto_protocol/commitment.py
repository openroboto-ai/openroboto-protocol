"""Encoding and decoding of the on-chain commitment payload — miners write it,
the backend reads it, and the two sides must agree byte for byte.

A miner stuffs the metadata JSON of one submission into Bittensor's
`Commitments.set_commitment` (`Data::BigRaw`), and the backend scans the chain
and reads it back. **This one JSON is the submission itself** — it decides
which HF repo gets evaluated, which round it counts for, and which burn
transaction is used to check it. If the encoding side and the decoding side
disagree, the miner burned TAO and nobody saw it.

## The shape of the on-chain JSON

The key names are squeezed down to one letter not for looks: `Data::BigRaw` has
a hard limit of 512 bytes, and one hf_repo_id plus two 64-character hex hashes
already eat half of it. Changing a key name = major.

| Key | Field | Contract meaning |
|---|---|---|
| `s` | `hotkey_ss58` | miner hotkey, full SS58 string |
| `h` | `block_hash` | submission block hash, **self-reported**, untrusted, see below |
| `c` | `hf_commit` | HuggingFace commit SHA, 40 hex characters |
| `r` | `round_num` | round number |
| `i` | `hf_repo_id` | HuggingFace repo id, e.g. `kyleab/pi05-scmGbsBoEmiQ` |
| `b` | `burn_tx_hash` | burn transaction hash, **stored on chain without `0x`** |
| `bb` | `burn_block` | the block the burn is in; encoded as `null` when it is 0 |

## Two asymmetries that must be remembered

1. **`h` is self-reported by the miner and must not be fed straight into seed
   derivation.** Once the chain scanner has `commit_block` it must overwrite it
   with `get_block_hash(commit_block)` — the one the chain gives has the `0x`,
   the one the miner self-reports does not. And `derive_seed()` takes a sha256
   of a string, so two extra characters mean a different seed and a different
   set of evaluation tasks. This module **returns the miner's self-reported
   value as-is** (it does not add `0x`); overwriting it is the caller's
   responsibility.
2. **`b` goes the other way: decoding adds `0x`, encoding strips `0x`.** Both
   production consumers (backend/chain_scanner.py, scripts/seed_data.py) do it
   this way, and the deduplication key depends on it.

## The variant name is a length, not a version number

The field keys that come back from the SDK look like this: `BigRaw` / `Raw119`
/ `Raw82`. The N in `RawN` is the **number of bytes**; up to 128 bytes it is
`RawN`, and only beyond that is it `BigRaw`. It carries no client version
information whatsoever.

The 2026-08 conclusion that "three of UID 71's four submissions were Raw119, so
the old rt.py took the Raw branch" was a misjudgement. The raw on-chain data
(Taostats extrinsic API, re-checked 2026-08-17):

    8797897  Raw119  netuid=126   ← a different subnet
    8808332  BigRaw  netuid=80    ← this subnet, decoded normally by the backend
    8830097  Raw119  netuid=126   ← a different subnet
    8830168  Raw119  netuid=126   ← a different subnet

The same hotkey posts commitments on several subnets; those three Raw119
records are netuid 126's binary payloads, which the netuid 80 backend was never
supposed to see in the first place. The root cause of the misjudgement is that
Taostats **silently ignores** the `netuid` query parameter (measured: passing
`netuid=80` still returns records from subnets 123/126/68/47 and others), so an
offline analysis script pulling data by signer_address counted other subnets in.
The miner did not burn TAO for nothing.

Therefore this module does **no special handling for `Raw119`**: every `Raw*` /
`BigRaw` variant is decoded with the same JSON rules, and when decoding fails
`DecodeFailure` says which class of failure it was and carries the variant name
back to the caller for logging. The real signal of version drift is
`UNKNOWN_SCHEMA` (it is a JSON object but not a single known key appears in
it), not the variant name.

## What it is not responsible for

It does not touch the chain, does not look up netuid, does not verify the burn,
does not overwrite `h`, and does not decide whether a round is the current one
— all of those need I/O or backend configuration and stay in the caller.

## Who consumes it

* the miner CLI (`rt.py` / `openroboto submit`) — `encode()`, called before
  burning TAO;
* the backend chain scanner — `decode()`, the return value of
  `get_commitment_metadata()` is fed straight in;
* the historical backfill script — `decode()`, fed the `__kind`/`value` shape
  returned by the indexer.

## Minimal verification

`uv run pytest tests/test_commitment.py` — GV-1 in there is the 295 real bytes
of on-chain block 8808332, and the moment "encode and decode are inverses of
each other" goes red, the format has drifted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

# The hard limit of `Data::BigRaw`. An extrinsic longer than this is rejected by
# the chain — and the burn is spent before the submission, so it must be caught
# before the money is spent.
MAX_COMMITMENT_BYTES: Final = 512

# All known keys of the on-chain JSON. At decode time, "not one of them is
# recognized" = the other side is not a client of this protocol.
PAYLOAD_KEYS: Final = ("s", "h", "c", "r", "i", "b", "bb")


class DecodeFailure(StrEnum):
    """Classification of decode failures — the caller uses it to tell "noise"
    apart from "something actually broke".

    Measured in production (2026-08-14, 3000 lines of chain-scan logs): of 772
    decode failures, 768 were `NO_COMMITMENT` (that hotkey never submitted
    anything on chain at all) and 4 were `NOT_UTF8` (binary payloads from other
    subnets). The old scanner logged both of these classes together with real
    faults as a `decode FAILED` WARNING, so 768 lines of noise buried the real
    signal.
    """

    NO_COMMITMENT = "no_commitment"
    """There is no such commitment on chain: `None`, an empty string, or no
    usable field in `info.fields`.
    This is a **normal state**, not an error — the vast majority of hotkeys
    have never submitted anything."""

    NOT_HEX = "not_hex"
    """The field value declares itself as `0x...` but is not valid
    hexadecimal."""

    NOT_UTF8 = "not_utf8"
    """The byte stream is not UTF-8. It is safe to conclude it is a binary
    payload from another subnet."""

    NOT_JSON = "not_json"
    """It is UTF-8 text but not valid JSON."""

    NOT_OBJECT = "not_object"
    """It is valid JSON but the top level is not an object (an array or a
    number, say)."""

    UNKNOWN_SCHEMA = "unknown_schema"
    """It is a JSON object, but not a single one of `PAYLOAD_KEYS` appears.
    **This is the real signal of client version drift** — the other side is
    using a set of key names we do not know."""


class CommitmentDecodeError(ValueError):
    """Decoding failed. `reason` is for classification statistics, `detail` is
    for humans to read."""

    def __init__(self, reason: DecodeFailure, detail: str = "") -> None:
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason = reason
        self.detail = detail


class CommitmentTooLargeError(ValueError):
    """The encoded result exceeds `MAX_COMMITMENT_BYTES`, so this commitment
    cannot go on chain.

    It must be raised **before** the miner burns TAO: the burn happens first
    and the submission second, and a failed submission does not give the money
    back. In practice the only field that can blow the limit is `hf_repo_id`.
    """

    def __init__(self, size: int) -> None:
        super().__init__(
            f"commitment payload {size} bytes > {MAX_COMMITMENT_BYTES} limit; "
            f"shorten hf_repo_id"
        )
        self.size = size


@dataclass(frozen=True)
class CommitmentPayload:
    """All the on-chain fields of one submission — they must come from the same
    source, so they are bound into one immutable whole.

    Passing them as separate arguments would make "this block_hash with that
    burn_tx_hash" possible, and once those two are mismatched the seed and the
    burn check point at two different submissions.
    """

    hotkey_ss58: str
    """Miner hotkey (SS58). On chain: `s`."""

    block_hash: str
    """The submission block hash **as self-reported by the miner**, on chain
    `h`, without the `0x`.

    ⚠️ Untrusted. The chain scanner must overwrite it with
    `get_block_hash(commit_block)` before it is used to derive the seed,
    otherwise miners can pick their own seed."""

    hf_commit: str
    """HuggingFace commit SHA, on chain `c`, 40 hex characters.
    Empty values have occurred in production (the miner did not fill in the
    commit URL); this module does not block it, the backend's HF check
    rejects it."""

    round_num: int
    """Round number, on chain `r`. When missing it is treated as 0 — 0 is never
    equal to the current round, so it gets rejected by the backend."""

    hf_repo_id: str
    """HuggingFace repo id, on chain `i`, e.g. `kyleab/pi05-scmGbsBoEmiQ`."""

    burn_tx_hash: str
    """Burn transaction hash, on chain `b`.

    What is stored on chain does **not** have the `0x`; `decode()` adds `0x`
    when it decodes and `encode()` strips it when it writes back. The
    deduplication key (hotkey + round + burn_tx_hash) depends on this
    normalized form."""

    burn_block: int
    """The block the burn is in, on chain `bb`. 0 means the miner did not report
    it, and it is written as `null` when encoding."""


@dataclass(frozen=True)
class DecodedCommitment:
    """The result of `decode()`: the payload self-reported by the miner, plus
    the trustworthy part that the chain envelope provides.

    The two are kept apart because their trust levels differ: everything in
    `payload` was written by the miner, whereas `commit_block` was returned by
    the chain node.
    """

    payload: CommitmentPayload
    """The JSON the miner wrote on chain. Every field is a self-reported
    value."""

    commit_block: int
    """The number of the block containing the commitment, from the metadata
    envelope returned by the chain (trustworthy).
    The caller takes it to `get_block_hash()` to exchange it for the real block
    hash. It is 0 when the input has no envelope."""

    data_variant: str
    """The `Data` variant name reported by the SDK: `BigRaw` / `Raw119` / ….

    It is only a **byte-length label**, not a version number (see the module
    docstring). It is carried back so that the caller can see shape changes in
    the logs; do not branch on it.
    Empty string when bytes or a dict were passed directly."""


def encode(payload: CommitmentPayload) -> bytes:
    """Encode the payload into the bytes that go on chain. The key order and
    the separators are part of the contract and must not be changed.

    The compact form of `json.dumps(separators=(",", ":"))` plus the fixed key
    order `s,h,c,r,i,b,bb` is an established fact of the historical on-chain
    data (see tests/test_golden_vectors.py). A different key order would not
    fail to decode, but the bytes would no longer match — and any check that
    compares byte by byte would blow up.

    Beyond 512 bytes it raises `CommitmentTooLargeError`: such a payload cannot
    go on chain, and by that point the miner has already burned TAO.
    """
    data = {
        "s": payload.hotkey_ss58,
        "h": _strip_0x(payload.block_hash),
        "c": payload.hf_commit,
        "r": payload.round_num,
        "i": payload.hf_repo_id,
        "b": _strip_0x(payload.burn_tx_hash),
        # Writing 0 as null is the historical shape, not a typo.
        "bb": payload.burn_block or None,
    }
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(blob) > MAX_COMMITMENT_BYTES:
        raise CommitmentTooLargeError(len(blob))
    return blob


def decode(raw: object) -> DecodedCommitment:
    """Decode whatever the chain scan produced into a `DecodedCommitment`;
    raise `CommitmentDecodeError` on failure.

    It accepts every shape the SDK actually returns (in production not one of
    them can be left out):

    * `bytes` / `str` — already the payload itself;
    * `{"deposit":…, "block": N, "info": {"fields": [{"BigRaw": …}]}}`
      — the return value of `get_commitment_metadata()`; the field value may be
      JSON text, a `0x` hex string, or a tuple of integers, depending on the
      SDK version;
    * `{"__kind": "BigRaw", "value": "0x…"}` — the shape used by indexers
      (Taostats/subsquid);
    * the payload dict itself — the fallback path for old code.

    Only the **first** usable `Raw*` / `BigRaw` field is taken. One commitment
    on chain has exactly one field, and several of them means the shape is no
    longer one we recognize; rather than guess, let the check above report it.
    """
    if raw is None:
        raise CommitmentDecodeError(DecodeFailure.NO_COMMITMENT, "raw is None")

    if isinstance(raw, bytes | bytearray):
        if not raw:
            raise CommitmentDecodeError(DecodeFailure.NO_COMMITMENT, "empty bytes")
        return DecodedCommitment(_payload_from_bytes(bytes(raw)), 0, "")

    if isinstance(raw, str):
        # Measured in production: for a hotkey with no commitment, the SDK
        # returns an empty string rather than None.
        if not raw:
            raise CommitmentDecodeError(DecodeFailure.NO_COMMITMENT, "empty string")
        return DecodedCommitment(_payload_from_bytes(raw.encode("utf-8")), 0, "")

    if isinstance(raw, Mapping):
        commit_block = _as_int(raw.get("block"))
        for variant, value in _iter_data_fields(raw):
            blob = _field_bytes(value)
            if blob:
                return DecodedCommitment(
                    _payload_from_bytes(blob), commit_block, variant
                )
        # Fall back: the envelope has no field, but this dict itself looks like
        # a payload.
        if _has_known_key(raw):
            return DecodedCommitment(_payload_from_mapping(raw), commit_block, "")
        raise CommitmentDecodeError(
            DecodeFailure.NO_COMMITMENT, "no Raw*/BigRaw field in info.fields"
        )

    raise CommitmentDecodeError(
        DecodeFailure.NO_COMMITMENT, f"unsupported type {type(raw).__name__}"
    )


# ─── Internal implementation ─────────────────────────────────


def _strip_0x(value: str) -> str:
    return value[2:] if value.startswith("0x") else value


def _as_str(value: object) -> str:
    """Anything that is not a string counts as empty — a miner writing
    `"i": 123` gets rejected by the backend's HF check."""
    return value if isinstance(value, str) else ""


def _as_int(value: object, default: int = 0) -> int:
    """`None` / non-numeric → default. On chain, `bb` defaults to `null`."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _has_known_key(data: Mapping[Any, Any]) -> bool:
    return any(key in data for key in PAYLOAD_KEYS)


def _iter_data_fields(raw: Mapping[Any, Any]) -> Iterator[tuple[str, object]]:
    """Dig (variant name, field value) out of the chain envelope.

    Across SDK versions `info.fields` is either `[{...}]` or `[[{...}]]`, and
    both levels have to be accepted.
    """
    info = raw.get("info")
    if not isinstance(info, Mapping):
        return
    fields = info.get("fields")
    if not isinstance(fields, list | tuple):
        return
    for group in fields:
        entries = (group,) if isinstance(group, Mapping) else group
        if not isinstance(entries, list | tuple):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            # Indexer shape: {"__kind": "BigRaw", "value": "0x…"}
            kind = entry.get("__kind")
            if isinstance(kind, str) and _is_data_variant(kind):
                yield kind, entry.get("value")
                continue
            # SDK shape: {"BigRaw": …}
            for key, value in entry.items():
                if isinstance(key, str) and _is_data_variant(key):
                    yield key, value


def _is_data_variant(key: str) -> bool:
    """`RawN` (N = number of bytes, ≤128) or `BigRaw` (>128). N is not a version
    number."""
    return key == "BigRaw" or key.startswith("Raw")


def _field_bytes(value: object) -> bytes | None:
    """Restore a field value back to bytes. Return `None` when it is not
    recognized (and go on to the next field)."""
    if isinstance(value, bytes | bytearray):
        return bytes(value)
    if isinstance(value, str):
        if value.startswith("0x"):
            try:
                return bytes.fromhex(value[2:])
            except ValueError as exc:
                raise CommitmentDecodeError(DecodeFailure.NOT_HEX, str(exc)) from exc
        return value.encode("utf-8")
    if isinstance(value, list | tuple):
        flat: list[int] = []
        for item in value:
            if isinstance(item, list | tuple):
                flat.extend(i for i in item if isinstance(i, int))
            elif isinstance(item, int):
                flat.append(item)
        try:
            return bytes(flat)
        except ValueError:
            return None
    return None


def _payload_from_bytes(blob: bytes) -> CommitmentPayload:
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CommitmentDecodeError(DecodeFailure.NOT_UTF8, str(exc)) from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CommitmentDecodeError(DecodeFailure.NOT_JSON, str(exc)) from exc
    if not isinstance(data, dict):
        raise CommitmentDecodeError(
            DecodeFailure.NOT_OBJECT, f"top level is {type(data).__name__}"
        )
    if not _has_known_key(data):
        raise CommitmentDecodeError(
            DecodeFailure.UNKNOWN_SCHEMA, f"keys={sorted(map(str, data))[:8]}"
        )
    return _payload_from_mapping(data)


def _payload_from_mapping(data: Mapping[Any, Any]) -> CommitmentPayload:
    """A missing key always falls back to the default value — old data from
    before a minor version added a new key must still decode."""
    burn_tx_hash = _as_str(data.get("b"))
    if burn_tx_hash and not burn_tx_hash.startswith("0x"):
        burn_tx_hash = f"0x{burn_tx_hash}"
    return CommitmentPayload(
        hotkey_ss58=_as_str(data.get("s")),
        # No `0x` is added: the miner's self-reported value is returned as-is,
        # see the asymmetry note in the module docstring.
        block_hash=_as_str(data.get("h")),
        hf_commit=_as_str(data.get("c")),
        round_num=_as_int(data.get("r")),
        hf_repo_id=_as_str(data.get("i")),
        burn_tx_hash=burn_tx_hash,
        burn_block=_as_int(data.get("bb")),
    )
