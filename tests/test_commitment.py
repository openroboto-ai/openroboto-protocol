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

import pytest

from openroboto_protocol.commitment import (
    MAX_COMMITMENT_BYTES,
    CommitmentDecodeError,
    CommitmentPayload,
    CommitmentTooLargeError,
    DecodeFailure,
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
    round_num=1,
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
            round_num=2,
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
    assert result.payload.round_num == 3
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
    round; a bad field degrades to its default value and the backend's own
    validations will reject it."""
    payload = decode('{"s":5,"i":null,"r":"7","bb":"x","c":true}').payload
    assert payload.hotkey_ss58 == ""
    assert payload.hf_repo_id == ""
    assert payload.round_num == 7  # a numeric string is accepted
    assert payload.burn_block == 0  # non-numeric falls back to the default
    assert payload.hf_commit == ""  # a bool is not a string
    # in Python a bool is a subclass of int, so it must not be casually taken
    # as 1/0
    assert decode('{"r":true,"i":"u/r"}').payload.round_num == 0


def test_decode_does_not_double_prefix_an_already_prefixed_burn_hash() -> None:
    """Some clients bring their own `0x`. Adding it twice would make the
    deduplication key disagree with history."""
    assert decode('{"b":"0xabc"}').payload.burn_tx_hash == "0xabc"
    assert decode('{"b":"abc"}').payload.burn_tx_hash == "0xabc"
    assert decode('{"b":"","r":1}').payload.burn_tx_hash == ""


def test_decode_leaves_block_hash_unprefixed() -> None:
    """⚠️ Red line: `h` is a value the miner reports itself — **do not add 0x**
    and do not process it in any way.

    `derive_seed()` takes the sha256 of `f"{block_hash}:{round}:{drand}"`, so two
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
        decode(GV1_BYTES).payload.round_num = 99  # type: ignore[misc]
