"""Contract tests for the commitment codec.

The first half is the **golden vectors** — input/output pairs that already
happened on chain; they are history, not expectations, and changing one of them
rewrites an on-chain fact (they belong in tests/test_golden_vectors.py, but that
file is currently taken by the seed module; when merging, move the "golden
vectors" section over as a whole).
The second half is a taxonomy of malformed inputs: when something cannot be
decoded we must **say which class it belongs to**, instead of stamping
everything with a single `decode FAILED` the way the old chain scanner did.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from openroboto_protocol import commitment as c
from openroboto_protocol.commitment import (
    MAX_COMMITMENT_BYTES,
    CommitmentDecodeError,
    CommitmentFieldError,
    CommitmentPayload,
    CommitmentTooLargeError,
    DecodeFailure,
    Track,
    check_payload,
    decode,
    encode,
)

# ═══════════════════════════════════════════════════════════════════
# Golden vectors — on-chain facts; changing them rewrites history
# ═══════════════════════════════════════════════════════════════════

# GV-1: netuid 80, block 8808332, `Commitments.set_commitment`, `Data::BigRaw`.
# Source: the Taostats extrinsic API, re-checked 2026-08-17, 295 raw bytes on
# chain. This is the only one of the "four submissions of UID 71" that was
# actually sent to this subnet, and the backend decoded it normally at the time.
GV1_BLOCK = 8808332
GV1_JSON = (
    '{"s":"5D33cWAUBDJLKEP6c2hCYxumbGKzV92qrbDuscmGbsBoEmiQ",'
    '"h":"94f06bc414624cf0935730f43a5d761df16b5e51d9327388287b280701cd0a22",'
    '"c":"09ecbfb798b7ab080fd5f54b60b3830d7e1a52e0",'
    '"r":1,'
    '"i":"kyleab/pi05-scmGbsBoEmiQ",'
    '"b":"ad2b5d0dee272c4a7459a737697e1cb538899b39e054fc8af40cf2d81cc9f310",'
    '"bb":8808331}'
)
GV1_BYTES = GV1_JSON.encode("utf-8")
GV1_PAYLOAD = CommitmentPayload(
    hotkey_ss58="5D33cWAUBDJLKEP6c2hCYxumbGKzV92qrbDuscmGbsBoEmiQ",
    block_hash="94f06bc414624cf0935730f43a5d761df16b5e51d9327388287b280701cd0a22",
    hf_commit="09ecbfb798b7ab080fd5f54b60b3830d7e1a52e0",
    claimed_competition_seq=1,
    hf_repo_id="kyleab/pi05-scmGbsBoEmiQ",
    # The chain stores the bare hash without 0x; decoding adds the 0x back — the
    # deduplication key depends on this form.
    burn_tx_hash="0xad2b5d0dee272c4a7459a737697e1cb538899b39e054fc8af40cf2d81cc9f310",
    burn_block=8808331,
)

# GV-2: netuid **126**, block 8797897, `Data::Raw119`. Sent by the same hotkey,
# but the recipient is **another subnet**. This one is the very source of the
# "the backend cannot decode Raw119" misdiagnosis: the netuid filter parameter of
# Taostats was silently ignored, so an offline analysis counted it as a
# submission to this subnet.
GV2_RAW119_HEX = (
    "0xdfb254add593413b116e2d3e45a57944592cf0a59cbbc3a3d8b5ddb51c355c0a"
    "51f9c84fe283d5806026bfd99fb6739c9d3b2974d10e14eb99718b425218ad1dae"
    "0dc8a8af925e037049a058ae5e071d598c4855cb21d052dbd90d3c41455ca68fa7"
    "b13a0282be1588607ba05adb7a344b5908f03bdd6a"
)


def test_gv1_on_chain_bytes_decode_to_the_recorded_submission() -> None:
    """Those 295 bytes on chain must decode to the submission the backend
    persisted at the time."""
    assert len(GV1_BYTES) == 295
    result = decode(GV1_BYTES)
    assert result.payload == GV1_PAYLOAD
    assert result.commit_block == 0  # bare bytes carry no chain envelope
    assert result.data_variant == ""


def test_gv1_reencodes_to_the_exact_on_chain_bytes() -> None:
    """Encoding and decoding are inverses of each other: key order, compact
    separators and the integer form of `bb` are identical byte for byte.

    Once this goes red the encoding format has drifted — the bytes a new miner
    writes on chain no longer have the same shape as history.
    """
    assert encode(GV1_PAYLOAD) == GV1_BYTES


def test_gv1_through_the_sdk_envelope() -> None:
    """Through the return shape of `get_commitment_metadata()`, the result must
    be exactly the same."""
    raw = {
        "deposit": 0,
        "block": GV1_BLOCK,
        "info": {"fields": [{"BigRaw": GV1_JSON}]},
    }
    result = decode(raw)
    assert result.payload == GV1_PAYLOAD
    assert result.commit_block == GV1_BLOCK
    assert result.data_variant == "BigRaw"


def test_gv1_through_the_indexer_envelope() -> None:
    """Through the `__kind` / `value` hexadecimal shape of an indexer
    (Taostats/subsquid)."""
    raw = {"info": {"fields": [{"__kind": "BigRaw", "value": "0x" + GV1_BYTES.hex()}]}}
    result = decode(raw)
    assert result.payload == GV1_PAYLOAD
    assert result.data_variant == "BigRaw"


def test_gv2_foreign_subnet_raw119_is_classified_not_crashed() -> None:
    """A Raw119 from another subnet: report NOT_UTF8 explicitly, do not crash and
    do not swallow it silently."""
    # the N of RawN is the byte count
    assert len(bytes.fromhex(GV2_RAW119_HEX[2:])) == 119

    raw = {"block": 8797897, "info": {"fields": [{"Raw119": GV2_RAW119_HEX}]}}
    with pytest.raises(CommitmentDecodeError) as exc:
        decode(raw)
    assert exc.value.reason is DecodeFailure.NOT_UTF8


def test_gv3_foreign_raw82_matches_the_production_error() -> None:
    """Reproduces the 4 `can't decode byte 0xec in position 5` lines in the
    production logs.

    Source: `_salvage/prod-logs-tail/chain_scanner.log` (2026-08-14); the field
    is `Raw82` and the content is a load balancer domain name — another payload
    belonging to a different subnet. Here the prefix bytes left in the log are
    used to reproduce the same classification.
    """
    prefix = "0x0c000d3401ec646f67656c617965722d"
    with pytest.raises(CommitmentDecodeError) as exc:
        decode({"info": {"fields": [{"Raw82": prefix}]}})
    assert exc.value.reason is DecodeFailure.NOT_UTF8
    assert "position 5" in exc.value.detail


def test_gv4_hotkey_without_commitment_returns_empty_string() -> None:
    """Measured in production: for a hotkey that has never committed, the SDK
    returns an **empty string** rather than `None`.

    In the 3000 lines of chain-scanning logs from 2026-08-14, 768 of the 772
    decode FAILED lines were this one — a normal state stamped as a WARNING,
    burying the real signal. It must be distinguishable from a real failure.
    """
    with pytest.raises(CommitmentDecodeError) as exc:
        decode("")
    assert exc.value.reason is DecodeFailure.NO_COMMITMENT


# ═══════════════════════════════════════════════════════════════════
# encode
# ═══════════════════════════════════════════════════════════════════


def test_encode_strips_0x_from_both_hashes() -> None:
    """The chain stores bare hashes. If `h` and `b` come in with a `0x` it has to
    be stripped, otherwise the bytes do not match history."""
    blob = encode(
        CommitmentPayload(
            hotkey_ss58="5Dxxx",
            block_hash="0xaabb",
            hf_commit="c" * 40,
            claimed_competition_seq=2,
            hf_repo_id="u/r",
            burn_tx_hash="0xccdd",
            burn_block=7,
        )
    )
    assert b'"h":"aabb"' in blob
    assert b'"b":"ccdd"' in blob


def test_encode_writes_null_for_missing_burn_block() -> None:
    """Writing `null` when `bb` is 0 is the historical shape, not a typo."""
    blob = encode(
        CommitmentPayload("5D", "h", "c", 1, "u/r", "", burn_block=0),
    )
    assert b'"bb":null' in blob
    assert b'"b":""' in blob


def test_encode_keeps_non_ascii_repo_id_verbatim() -> None:
    """`ensure_ascii=False`: non-ASCII is written as UTF-8 verbatim, not turned
    into `\\uXXXX`.

    Escaping would make the same repo name produce two different byte strings,
    and then the on-chain comparison no longer matches.
    """
    blob = encode(CommitmentPayload("5D", "", "", 1, "用户/模型", "", 0))
    assert "用户/模型".encode() in blob


def test_encode_rejects_payload_that_cannot_land_on_chain() -> None:
    """More than 512 bytes cannot land on chain, and the burn has already been
    paid — so it must blow up before the money is spent."""
    with pytest.raises(CommitmentTooLargeError) as exc:
        encode(CommitmentPayload("5D", "h", "c", 1, "u/" + "x" * 600, "", 0))
    assert exc.value.size > MAX_COMMITMENT_BYTES
    assert "hf_repo_id" in str(exc.value)


def test_encode_accepts_payload_exactly_at_the_limit() -> None:
    """The boundary is that 512 itself is allowed, not 511."""
    fixed = len(encode(CommitmentPayload("5D", "h", "c", 1, "", "", 0)))
    repo_id = "x" * (MAX_COMMITMENT_BYTES - fixed)
    assert len(encode(CommitmentPayload("5D", "h", "c", 1, repo_id, "", 0))) == (
        MAX_COMMITMENT_BYTES
    )


# ═══════════════════════════════════════════════════════════════════
# decode — envelope shapes
# ═══════════════════════════════════════════════════════════════════


def test_decode_accepts_bytearray_and_str_payloads() -> None:
    """Depending on the SDK version, the payload may be bytes, bytearray or an
    already decoded str."""
    assert decode(bytearray(GV1_BYTES)).payload == GV1_PAYLOAD
    assert decode(GV1_JSON).payload == GV1_PAYLOAD


def test_decode_accepts_raw_bytes_field_values() -> None:
    """The field value is bytes itself (some SDK versions do no decoding)."""
    raw = {"info": {"fields": [{"BigRaw": bytearray(GV1_BYTES)}]}}
    assert decode(raw).payload == GV1_PAYLOAD


def test_decode_accepts_int_tuple_field_values() -> None:
    """Old SDKs return the bytes as a (possibly one level nested) tuple of
    integers."""
    tail = tuple(GV1_BYTES[4:])
    raw = {"info": {"fields": ({"Raw200": (tuple(GV1_BYTES[:4]), *tail)},)}}
    assert decode(raw).payload == GV1_PAYLOAD


def test_decode_accepts_doubly_nested_field_groups() -> None:
    """`fields` is sometimes `[[{...}]]` instead of `[{...}]`; both levels have to
    be recognised."""
    raw = {"info": {"fields": [[{"BigRaw": GV1_JSON}]]}}
    assert decode(raw).payload == GV1_PAYLOAD


def test_decode_falls_back_to_a_bare_payload_dict() -> None:
    """The caller hands in an already decoded dict directly (the fallback path of
    old code)."""
    result = decode({"block": 42, "r": 3, "i": "u/r"})
    assert result.payload.claimed_competition_seq == 3
    assert result.payload.hf_repo_id == "u/r"
    assert result.commit_block == 42
    assert result.data_variant == ""


def test_decode_reads_commit_block_from_a_string_envelope() -> None:
    """Some SDKs / indexers give the block number as a string."""
    raw = {"block": "8808332", "info": {"fields": [{"BigRaw": GV1_JSON}]}}
    assert decode(raw).commit_block == 8808332


def test_decode_skips_unusable_fields_and_keeps_looking() -> None:
    """Empty values, unrecognised types and non-Raw variants are all skipped and
    the search continues."""
    raw = {
        "info": {
            "fields": [
                123,  # the whole group is neither a mapping nor a sequence
                ["not-a-mapping"],
                {"netuid": 80},  # not a Data variant
                # in use by another subnet
                {"__kind": "TimelockEncrypted", "value": "0xdead"},
                {"Raw0": ""},  # empty value
                {"Raw8": None},  # unrecognised type
                {"__kind": "Raw0", "value": ""},  # empty value, indexer shape
                {"BigRaw": GV1_JSON},
            ]
        }
    }
    assert decode(raw).payload == GV1_PAYLOAD


def test_decode_rejects_oversized_ints_in_a_tuple_field() -> None:
    """Non-byte things mixed into the tuple (integers > 255, `None`): this is not
    a byte stream, skip it."""
    with pytest.raises(CommitmentDecodeError) as exc:
        decode({"info": {"fields": [{"Raw4": (999, None)}]}})
    assert exc.value.reason is DecodeFailure.NO_COMMITMENT


@pytest.mark.parametrize(
    ("raw", "detail_hint"),
    [
        (None, "None"),
        (b"", "empty"),
        ("", "empty"),
        (12345, "int"),
        ({"info": "not-a-mapping"}, "no Raw"),
        ({"info": {"fields": "not-a-sequence"}}, "no Raw"),
        ({"info": {"fields": []}}, "no Raw"),
        ({"deposit": 0, "block": 1}, "no Raw"),
    ],
)
def test_decode_reports_no_commitment_for_empty_envelopes(
    raw: object, detail_hint: str
) -> None:
    """The state "there is no such commitment on chain" is normal and must be
    kept apart from a real failure — in production it is 99% of the volume."""
    with pytest.raises(CommitmentDecodeError) as exc:
        decode(raw)
    assert exc.value.reason is DecodeFailure.NO_COMMITMENT
    assert detail_hint in exc.value.detail


# ═══════════════════════════════════════════════════════════════════
# decode — content classification
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("0xzz", DecodeFailure.NOT_HEX),
        ("0xec", DecodeFailure.NOT_UTF8),
        ("not json", DecodeFailure.NOT_JSON),
        ("[1,2,3]", DecodeFailure.NOT_OBJECT),
        ('{"foo":1}', DecodeFailure.UNKNOWN_SCHEMA),
        ("{}", DecodeFailure.UNKNOWN_SCHEMA),
    ],
)
def test_decode_classifies_every_malformed_input(
    value: str, reason: DecodeFailure
) -> None:
    """The caller has to be able to bucket the counts by reason: noise counted as
    noise, version drift counted as version drift."""
    with pytest.raises(CommitmentDecodeError) as exc:
        decode({"info": {"fields": [{"BigRaw": value}]}})
    assert exc.value.reason is reason


def test_unknown_schema_is_the_real_version_drift_signal() -> None:
    """Not a single known key = the other side is using key names we do not
    recognise. The error has to carry the key names to make it debuggable."""
    with pytest.raises(CommitmentDecodeError) as exc:
        decode('{"hotkey":"5D","repo":"u/r"}')
    assert exc.value.reason is DecodeFailure.UNKNOWN_SCHEMA
    assert "hotkey" in exc.value.detail


def test_decode_error_message_carries_the_reason() -> None:
    """The classification has to be visible even when the log only prints
    str(exc)."""
    with pytest.raises(CommitmentDecodeError, match="no_commitment: raw is None"):
        decode(None)
    with pytest.raises(CommitmentDecodeError, match=r"^not_utf8$"):
        raise CommitmentDecodeError(DecodeFailure.NOT_UTF8)


# ═══════════════════════════════════════════════════════════════════
# decode — field-level tolerance
# ═══════════════════════════════════════════════════════════════════


def test_decode_defaults_every_missing_key() -> None:
    """A key added in a minor version: old data missing it must still decode
    (AGENTS rule ②)."""
    payload = decode('{"i":"u/r"}').payload
    assert payload == CommitmentPayload("", "", "", 0, "u/r", "", 0)


def test_decode_coerces_wrong_typed_fields_instead_of_crashing() -> None:
    """A miner writing the wrong type must not blow up a whole chain-scanning
    pass; a bad field degrades to its default value and the backend's own
    validations will reject it."""
    payload = decode('{"s":5,"i":null,"r":"7","bb":"x","c":true}').payload
    assert payload.hotkey_ss58 == ""
    assert payload.hf_repo_id == ""
    assert payload.claimed_competition_seq == 7  # a numeric string is accepted
    assert payload.burn_block == 0  # non-numeric falls back to the default
    assert payload.hf_commit == ""  # a bool is not a string
    # in Python a bool is a subclass of int, so it must not be casually taken
    # as 1/0
    assert decode('{"r":true,"i":"u/r"}').payload.claimed_competition_seq == 0


def test_decode_does_not_double_prefix_an_already_prefixed_burn_hash() -> None:
    """Some clients bring their own `0x`. Adding it twice would make the
    deduplication key disagree with history."""
    assert decode('{"b":"0xabc"}').payload.burn_tx_hash == "0xabc"
    assert decode('{"b":"abc"}').payload.burn_tx_hash == "0xabc"
    assert decode('{"b":"","r":1}').payload.burn_tx_hash == ""


def test_decode_leaves_block_hash_unprefixed() -> None:
    """⚠️ Red line: `h` is a value the miner reports itself — **do not add 0x**
    and do not process it in any way.

    `derive_seed()` takes the sha256 of `f"{block_hash}:{cid}:{drand}"`, so two
    extra characters mean a different seed and a different set of tasks. The
    caller must overwrite it with the real on-chain hash; "helpfully adding a 0x"
    for it here amounts to silently changing the seed.
    """
    assert decode(GV1_BYTES).payload.block_hash == GV1_PAYLOAD.block_hash
    assert not decode(GV1_BYTES).payload.block_hash.startswith("0x")


def test_payload_is_frozen() -> None:
    """The seven fields must share one source. If it were mutable, "this
    block_hash paired with that burn_tx" could happen."""
    with pytest.raises(AttributeError):
        decode(GV1_BYTES).payload.claimed_competition_seq = 99  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════
# Derived vectors (NOT on-chain history)
# ═══════════════════════════════════════════════════════════════════
#
# The `cid` key does not exist on chain yet: `sim seq=2` and the first real-track
# season only go active once 0.7.0 has shipped. GV-5 and GV-6 are therefore
# **derived and constructed values, not on-chain facts** — the discipline "change
# one and you have rewritten history" applies to GV-1 ~ GV-4 above, not here.
#
# ⏳ When the first commitment carrying a `cid` is really on chain, come back and
# replace GV-5 with the real bytes, and move it up into the on-chain section.

# GV-5: a simulation payload that names its season — GV-1's fields plus `cid`.
GV5_PAYLOAD = dataclasses.replace(GV1_PAYLOAD, competition_id=2)

# GV-6: the real track. Worst case for every fixed-width field:
#   s   48-character SS58
#   h   64 hex characters
#   c   40 hex characters
#   r   one digit
#   b   64 hex characters
#   bb  7 digits
#   cid the largest value a `bigint GENERATED ALWAYS AS IDENTITY` can hand out
#   m   64 hex characters
# which leaves `hf_repo_id` — the only variable-length field — as the budget.
GV6_REAL_FIXED_BYTES = 368
GV6_MAX_REPO_ID_CHARS = MAX_COMMITMENT_BYTES - GV6_REAL_FIXED_BYTES  # 144
GV6_PAYLOAD = CommitmentPayload(
    hotkey_ss58="5" + "D" * 47,
    block_hash="a" * 64,
    hf_commit="b" * 40,
    claimed_competition_seq=9,
    hf_repo_id="n" * 96 + "/" + "m" * 47,  # 144 characters, the limit
    # Normalized form, with the `0x` decode() adds back; the chain stores the
    # bare 64 characters.
    burn_tx_hash="0x" + "c" * 64,
    burn_block=8808331,
    competition_id=9223372036854775807,
    model_hash="d" * 64,
)


def test_sim_payload_without_cid_is_byte_identical_to_0_6_0() -> None:
    """The single most expensive assertion in this repository: if it goes red,
    every old miner is locked out on the day 0.7.0 ships.

    Adding `cid` / `m` must leave the bytes of a payload that uses neither
    completely untouched — the key must be **absent**, not `null`, because
    `"cid":null` decodes the same but changes the byte count.
    """
    blob = encode(GV1_PAYLOAD)
    assert blob == GV1_BYTES  # 295 real on-chain bytes
    assert b'"cid"' not in blob
    assert b'"m"' not in blob
    assert json.loads(blob).keys() == {"s", "h", "c", "r", "i", "b", "bb"}


def test_gv1_decodes_with_competition_id_none() -> None:
    """An old miner's commitment has no `cid`, and that must be silent: `None`
    means "the key was absent", and the caller reads the submission as
    `(sim, seq=claimed_competition_seq)`. Raising here would reject every miner
    running today.
    """
    payload = decode(GV1_BYTES).payload
    assert payload.competition_id is None
    assert payload.model_hash is None


def test_gv1_claimed_competition_seq_survives() -> None:
    """`r` may not be tidied away. For a payload without `cid` it is the only
    thing that locates the season, and the backend's backfill keys off exactly
    this (`competitions.track='sim' AND seq=claimed_competition_seq`)."""
    assert decode(GV1_BYTES).payload.claimed_competition_seq == 1
    assert "claimed_competition_seq" in {
        f.name for f in dataclasses.fields(CommitmentPayload)
    }


def test_gv5_sim_with_cid_survives_a_full_encode_decode() -> None:
    """A simulation payload that names its season: `cid` comes back as it went
    in, and the package does not infer a track from it."""
    blob = encode(GV5_PAYLOAD)
    assert decode(blob).payload == GV5_PAYLOAD
    assert decode(blob).payload.competition_id == 2
    # `r` is still there next to `cid`
    assert decode(blob).payload.claimed_competition_seq == 1
    assert json.loads(blob).keys() == {"s", "h", "c", "r", "i", "b", "bb", "cid"}


def test_gv6_real_carries_model_hash() -> None:
    """`m` comes back verbatim. It is the one field that cannot be looked up
    from `cid`, and on the real track it is what makes a private repository
    trustworthy."""
    payload = decode(encode(GV6_PAYLOAD)).payload
    assert payload == GV6_PAYLOAD
    assert payload.model_hash == "d" * 64
    check_payload(payload, Track.REAL)  # a complete real-track payload


def test_gv6_real_worst_case_fits_on_chain() -> None:
    """Measured budget for the real track.

    With every fixed-width field at its maximum the nine keys cost **368 bytes**,
    so `hf_repo_id` may be up to **144 characters** and the payload is then
    exactly 512 — zero headroom left, by construction.

    ⚠️ The often-quoted "450 bytes, 62 to spare" only holds for an `hf_repo_id`
    of at most 82 characters; it is not a worst case. HuggingFace allows
    96 + 1 + 96 = 193 characters, and such a repo id does **not** fit on the real
    track (561 bytes) — see the test below. One more 64-hex key would cost 71
    bytes and cut the 144 characters down to 73.
    """
    blob = encode(GV6_PAYLOAD)
    assert len(blob) == MAX_COMMITMENT_BYTES == 512
    assert len(GV6_PAYLOAD.hf_repo_id) == GV6_MAX_REPO_ID_CHARS == 144
    # The fixed cost is the number to quote when someone wants to add a key.
    empty_repo = dataclasses.replace(GV6_PAYLOAD, hf_repo_id="")
    assert len(encode(empty_repo)) == GV6_REAL_FIXED_BYTES == 368


def test_a_real_payload_with_the_longest_possible_repo_id_is_refused() -> None:
    """A 193-character repo id (HuggingFace's own maximum) exceeds 512 on the
    real track, and it has to blow up **before** the entry fee is paid.

    This is not a defect of the encoding, it is the budget: the real track's
    nine keys leave 144 characters for the repo id. A miner in that position has
    to rename the repository, and `CommitmentTooLargeError` says so.
    """
    too_long = dataclasses.replace(GV6_PAYLOAD, hf_repo_id="n" * 96 + "/" + "m" * 96)
    with pytest.raises(CommitmentTooLargeError) as exc:
        encode(too_long)
    assert exc.value.size == 561
    assert "hf_repo_id" in str(exc.value)


def test_payload_never_contains_track_or_fee_keys() -> None:
    """The three key sets are exactly 7 / 8 / 9 keys.

    Asserting equality rather than `"t" not in ...` one key at a time: it blocks
    `t` (the track is read from the season `cid` points at), `f` / `fb` (the fee
    reuses `b` / `bb`) **and** any unforeseen key, without needing a new
    assertion per banned name.
    """
    assert len(json.loads(encode(GV1_PAYLOAD))) == 7
    assert len(json.loads(encode(GV5_PAYLOAD))) == 8
    assert json.loads(encode(GV6_PAYLOAD)).keys() == {
        "s",
        "h",
        "c",
        "r",
        "i",
        "b",
        "bb",
        "cid",
        "m",
    }


def test_unknown_keys_are_ignored_not_rejected() -> None:
    """Adding a key is a minor bump, so an older copy of this package has to be
    able to read a payload written by a newer one. `UNKNOWN_SCHEMA` means "not
    one known key", not "a key I have not seen"."""
    newer = dict(json.loads(GV1_JSON), t="sim", f="0xdeadbeef")
    payload = decode(json.dumps(newer).encode()).payload
    assert payload == GV1_PAYLOAD  # the two extra keys land nowhere
    assert payload.competition_id is None


def test_golden_vector_sections_are_labelled() -> None:
    """The on-chain section and the derived section must stay marked as such.

    Mixing them is the one mistake this file can make: "changing a vector
    rewrites history" is only true of the on-chain ones, and if the derived ones
    look like history nobody will dare replace GV-5 with the real bytes once
    they exist.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    assert "Derived vectors (NOT on-chain history)" in source
    assert "on-chain facts; changing them rewrites history" in source


# ═══════════════════════════════════════════════════════════════════
# check_payload — the pre-flight both sides share
# ═══════════════════════════════════════════════════════════════════


def test_check_payload_accepts_an_old_style_simulation_payload() -> None:
    """GV-1 is what a simulation miner sends today, and it must keep passing."""
    check_payload(GV1_PAYLOAD, Track.SIM)


@pytest.mark.parametrize("bad", ["b" * 39, "b" * 41, "B" * 40, "", "g" * 40])
def test_check_payload_rejects_a_malformed_hf_commit(bad: str) -> None:
    """40 lowercase hex characters, on both tracks. Uppercase is rejected too:
    the same commit in two spellings would be two different byte streams and two
    different deduplication keys."""
    with pytest.raises(CommitmentFieldError) as exc:
        check_payload(dataclasses.replace(GV1_PAYLOAD, hf_commit=bad), Track.SIM)
    assert exc.value.field == "c"


def test_check_payload_demands_a_model_hash_on_the_real_track() -> None:
    """Missing `m` names the missing key. It cannot be looked up from `cid` —
    the repository may be private — so a real-track submission without it can
    never be verified."""
    without_m = dataclasses.replace(GV6_PAYLOAD, model_hash=None)
    with pytest.raises(CommitmentFieldError) as exc:
        check_payload(without_m, Track.REAL)
    assert exc.value.field == "m"
    assert "model_hash" in str(exc.value)
    # ...and the same payload is perfectly legal on the simulation track.
    check_payload(without_m, Track.SIM)


@pytest.mark.parametrize("bad", ["d" * 63, "d" * 65, "D" * 64, "", "z" * 64])
def test_check_payload_rejects_a_malformed_model_hash(bad: str) -> None:
    """64 lowercase hex characters — the shape `model_hash.py` produces."""
    with pytest.raises(CommitmentFieldError) as exc:
        check_payload(dataclasses.replace(GV6_PAYLOAD, model_hash=bad), Track.REAL)
    assert exc.value.field == "m"


def test_check_payload_demands_a_competition_id_on_the_real_track() -> None:
    """Without `cid` a real-track submission is read as
    `(sim, seq=claimed_competition_seq)` —
    it would land on the simulation leaderboard with the entry fee spent."""
    with pytest.raises(CommitmentFieldError) as exc:
        check_payload(dataclasses.replace(GV6_PAYLOAD, competition_id=None), Track.REAL)
    assert exc.value.field == "cid"


# ═══════════════════════════════════════════════════════════════════
# Track — the value set of competitions.track
# ═══════════════════════════════════════════════════════════════════


def test_track_has_exactly_two_values() -> None:
    """The same two words as the `ck_competitions_track` CHECK in the backend's
    0003 migration."""
    assert sorted(t.value for t in Track) == ["real", "sim"]


def test_track_rejects_an_unknown_value_loudly() -> None:
    """An unknown track must raise and the message must carry the value.
    Falling back to `sim` would silently move a real-track submission onto the
    simulation leaderboard."""
    with pytest.raises(ValueError, match="banana"):
        Track("banana")


# ═══════════════════════════════════════════════════════════════════
# The public surface
# ═══════════════════════════════════════════════════════════════════


def test_module_exports_are_pinned() -> None:
    """`__all__` is the public surface, and the public surface is what the
    version number promises. Without this test there is no line between a patch
    and a breaking change."""
    assert c.__all__ == [
        "MAX_COMMITMENT_BYTES",
        "PAYLOAD_KEYS",
        "CommitmentDecodeError",
        "CommitmentFieldError",
        "CommitmentPayload",
        "CommitmentTooLargeError",
        "DecodeFailure",
        "DecodedCommitment",
        "Track",
        "check_payload",
        "decode",
        "encode",
    ]
    assert all(hasattr(c, name) for name in c.__all__)


def test_payload_keys_are_the_nine_on_chain_names() -> None:
    """In the order `encode()` writes them: new keys are appended, never
    inserted, so that payloads which do not use them keep their bytes."""
    assert c.PAYLOAD_KEYS == ("s", "h", "c", "r", "i", "b", "bb", "cid", "m")


def test_decode_keeps_a_present_but_unusable_cid_out_of_the_none_branch() -> None:
    """`"cid":"banana"` is not the same thing as "no cid at all".

    A miner who mistyped the season must fail loudly at season lookup (0 is not
    a value an identity primary key hands out); reading it as `None` would send
    the submission to `(sim, seq=r)` with the fee already spent.
    """
    assert decode('{"i":"u/r","cid":"banana"}').payload.competition_id == 0
    assert decode('{"i":"u/r","cid":"7"}').payload.competition_id == 7
    assert decode('{"i":"u/r","cid":null}').payload.competition_id is None
    # A non-string `m` is "unusable", not "absent" — check_payload rejects it.
    assert decode('{"i":"u/r","m":123}').payload.model_hash == ""
