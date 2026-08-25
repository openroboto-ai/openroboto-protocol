"""Edge behaviour of ``model_format``. Real miner repos live in
``test_model_golden_vectors.py``.

The cases split in two: the ones that **must be accepted** (accepting wrongly =
rejecting a miner who has already burned TAO) and the ones that **must be
rejected** (rejecting wrongly = burning a slot of GPU time for nothing).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import get_type_hints

import pytest

from openroboto_protocol import model_format
from openroboto_protocol.model_format import (
    LIBERO_LAYOUT,
    LINGBOT_MODEL_CONFIG_FILE,
    LINGBOT_REQUIRED_TENSOR_PREFIXES,
    LINGBOT_WEIGHTS_INDEX_FILE,
    MIN_TOTAL_SIZE_BYTES,
    REJECTING_ISSUE_CODES,
    WARNING_ISSUE_CODES,
    CheckpointFile,
    CheckpointKind,
    FormatIssueCode,
    LingbotLayout,
    check_checkpoint_layout,
    check_lingbot_layout,
)

BIG = 900 * 1024 * 1024
NORM_STATS = "assets/physical-intelligence/libero/norm_stats.json"


def _f(*paths: str, size: int = BIG) -> list[CheckpointFile]:
    return [CheckpointFile(path=p, size_bytes=size) for p in paths]


def _codes(paths: list[CheckpointFile]) -> list[FormatIssueCode]:
    return [i.code for i in check_checkpoint_layout(paths).errors]


def _warn_codes(paths: list[CheckpointFile]) -> list[FormatIssueCode]:
    return [i.code for i in check_checkpoint_layout(paths).warnings]


# ── The layout itself ─────────────────────────────────────────────────────


def test_norm_stats_path_is_derived_from_the_asset_id() -> None:
    """The path is not a second constant written out by hand, it is derived from
    ``asset_id``, so the two cannot drift apart."""
    assert LIBERO_LAYOUT.norm_stats_relpath == NORM_STATS
    assert LIBERO_LAYOUT.asset_id in LIBERO_LAYOUT.norm_stats_relpath


def test_pytorch_checkpoint_is_accepted() -> None:
    report = check_checkpoint_layout(_f("model.safetensors", NORM_STATS))
    assert report.ok
    assert report.kind is CheckpointKind.PYTORCH


def test_jax_orbax_checkpoint_is_accepted() -> None:
    report = check_checkpoint_layout(
        _f("params/_METADATA", "params/manifest.ocdbt", "params/d/0abc", NORM_STATS)
    )
    assert report.ok
    assert report.kind is CheckpointKind.JAX


def test_pytorch_wins_when_both_formats_are_present() -> None:
    """openpi loads ``model.safetensors`` and ignores ``params/``; the report
    follows it."""
    report = check_checkpoint_layout(
        _f("model.safetensors", "params/_METADATA", NORM_STATS)
    )
    assert report.kind is CheckpointKind.PYTORCH


def test_a_file_literally_named_params_is_not_a_jax_checkpoint() -> None:
    """``params`` has to be a directory level in the middle of the path, it
    cannot be the final file name."""
    assert _codes(_f("params", NORM_STATS)) == [FormatIssueCode.MISSING_WEIGHTS]


def test_nested_layout_is_accepted_at_any_depth() -> None:
    """uid 130 put the whole checkpoint under ``merged/``; that is a legal
    submission."""
    assert check_checkpoint_layout(
        _f("merged/model.safetensors", f"merged/{NORM_STATS}")
    ).ok


def test_deeper_nesting_than_the_evaluator_searches_warns_but_passes() -> None:
    """Admission accepts it (that is how production judges it), but the evaluator
    only searches two levels down — tell the miner up front."""
    files = _f(f"a/b/c/{LIBERO_LAYOUT.pytorch_weights_file}", f"a/b/c/{NORM_STATS}")
    report = check_checkpoint_layout(files)
    assert report.ok
    assert [i.code for i in report.warnings] == [FormatIssueCode.NESTED_TOO_DEEP]


def test_a_shallow_copy_silences_the_nesting_warning() -> None:
    """The evaluator takes the shallowest copy, so as long as one copy is shallow
    enough there is no problem."""
    files = _f("a/b/c/model.safetensors", "model.safetensors", NORM_STATS)
    assert check_checkpoint_layout(files).warnings == ()


# ── Must be rejected ──────────────────────────────────────────────────────


def test_no_weights_is_rejected() -> None:
    assert _codes(_f("README.md", NORM_STATS)) == [FormatIssueCode.MISSING_WEIGHTS]


def test_bare_lora_adapter_is_rejected_with_its_own_reason() -> None:
    """The verdict is rejection just like "missing weights", but the reason has to
    be spelled out — the miner needs to know to go and merge."""
    files = _f("adapter_config.json", "adapter_model.safetensors", NORM_STATS)
    report = check_checkpoint_layout(files)
    assert [i.code for i in report.errors] == [FormatIssueCode.BARE_LORA_ADAPTER]
    assert "merge" in report.errors[0].message.lower()
    assert report.kind is None


def test_merged_checkpoint_shipped_next_to_the_adapter_is_fine() -> None:
    """The merged full weights are there; also uploading the adapter alongside
    them does not make it a bare adapter."""
    files = _f("adapter_model.safetensors", "model.safetensors", NORM_STATS)
    assert check_checkpoint_layout(files).ok


def test_missing_norm_stats_is_rejected() -> None:
    assert _codes(_f("model.safetensors")) == [FormatIssueCode.MISSING_NORM_STATS]


def test_leftover_upload_state_is_rejected() -> None:
    """The basename of ``.cache/models/x.bin`` is not a dotfile, so the path has
    to be checked segment by segment."""
    files = _f("model.safetensors", NORM_STATS, ".cache/huggingface/x.bin")
    assert _codes(files) == [FormatIssueCode.LEFTOVER_UPLOAD_STATE]


def test_incomplete_file_is_rejected_even_with_a_multi_dot_name() -> None:
    """Taking the first dot makes the suffix of ``checkpoint.001.tmp`` come out as
    ``.001.tmp``, which misses the verdict."""
    files = _f("model.safetensors", NORM_STATS, "checkpoint.001.tmp")
    assert _codes(files) == [FormatIssueCode.INCOMPLETE_FILE]


def test_too_small_repo_is_rejected() -> None:
    """A dozen-odd KB of "weights" is basically just an LFS pointer, the real
    files were never uploaded."""
    files = _f("model.safetensors", NORM_STATS, size=1024)
    report = check_checkpoint_layout(files)
    assert [i.code for i in report.errors] == [FormatIssueCode.TOTAL_SIZE_TOO_SMALL]
    assert report.counted_size_bytes == 2048


def test_size_is_not_reported_on_top_of_a_real_problem() -> None:
    """A repo with missing files is small to begin with; reporting both is noise.
    That is how production judges it."""
    assert _codes(_f("README.md", size=10)) == [
        FormatIssueCode.MISSING_WEIGHTS,
        FormatIssueCode.MISSING_NORM_STATS,
    ]


def test_size_threshold_is_ten_megabytes() -> None:
    files = _f("model.safetensors", NORM_STATS, size=MIN_TOTAL_SIZE_BYTES // 2)
    assert check_checkpoint_layout(files).ok


# ── dotfiles: the 2026-08-14 false-rejection incident ─────────────────────


def test_gitattributes_is_not_a_problem() -> None:
    """HF generates it automatically when a repo is created. Rejecting on the
    prefix means rejecting every miner (8 out of 8 repos have it)."""
    files = _f("model.safetensors", NORM_STATS, ".gitattributes")
    report = check_checkpoint_layout(files)
    assert report.ok
    assert report.warnings == ()


def test_dotfiles_do_not_count_towards_the_size() -> None:
    """They are not model content; counting them towards the size would distort
    the "too small" verdict."""
    files = _f("model.safetensors", NORM_STATS, ".gitignore", size=1024)
    assert check_checkpoint_layout(files).counted_size_bytes == 2048


def test_rejected_segments_can_be_whitelisted() -> None:
    """The on-the-spot escape hatch for when a real miner is falsely rejected
    (production config ``scanner.hf_allow_dotfiles``)."""
    files = _f("model.safetensors", NORM_STATS, ".cache/x.bin")
    allowed = frozenset({".cache"})
    assert check_checkpoint_layout(files, allowed_path_segments=allowed).ok


# ── Historical divergences that admission lets through but the evaluator
#    cannot load ───────────────────────────────────────────────────────────


def test_legacy_pytorch_bin_passes_admission_with_a_warning() -> None:
    """Production admission accepts ``pytorch_model.bin``, the evaluator does not
    — leave the verdict alone, but say it out loud first."""
    report = check_checkpoint_layout(_f("pytorch_model.bin", NORM_STATS))
    assert report.ok
    assert report.kind is None
    assert [i.code for i in report.warnings] == [
        FormatIssueCode.UNLOADABLE_WEIGHTS_FORMAT
    ]


def test_legacy_bin_next_to_real_weights_does_not_warn() -> None:
    files = _f("pytorch_model.bin", "model.safetensors", NORM_STATS)
    assert check_checkpoint_layout(files).warnings == ()


def test_legacy_norm_stats_location_passes_admission_with_a_warning() -> None:
    """Production admission also accepts two other locations: ``assets/libero/``
    and a bare ``norm_stats.json``."""
    files = _f("model.safetensors", "assets/libero/norm_stats.json")
    report = check_checkpoint_layout(files)
    assert report.ok
    assert [i.code for i in report.warnings] == [
        FormatIssueCode.NON_CANONICAL_NORM_STATS
    ]


def test_bare_norm_stats_at_the_root_also_passes_with_a_warning() -> None:
    files = _f("model.safetensors", "norm_stats.json")
    assert _warn_codes(files) == [FormatIssueCode.NON_CANONICAL_NORM_STATS]


# ── LingBot-VLA 2.0: a second rule set, added in 0.7.0 ────────────────────
#
# The openpi cases above are the contract miners have been submitting against
# since round 1. Everything below is additive; nothing above changed.

LINGBOT_LAYOUT = LingbotLayout(
    model_config_file=LINGBOT_MODEL_CONFIG_FILE,
    weights_index_file=LINGBOT_WEIGHTS_INDEX_FILE,
    camera_names=("camera_top", "camera_wrist"),
    joint_field_names=("j1", "j2", "j3", "j4", "j5", "j6", "gripper"),
)
LINGBOT_MINIMAL = (
    "config.json",
    "model.safetensors.index.json",
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
)
LINGBOT_WEIGHT_MAP = {
    "model.action_in_proj.weight": "model-00001-of-00002.safetensors",
    "model.action_out_proj.weight": "model-00001-of-00002.safetensors",
    "model.state_proj.weight": "model-00001-of-00002.safetensors",
    "model.qwenvl_with_expert.qwen_expert.model.layers.0.mlp.gate.weight": (
        "model-00002-of-00002.safetensors"
    ),
}


def _lb(
    *paths: str,
    size: int = BIG,
    layout: LingbotLayout | None = None,
    weight_map: dict[str, str] | None = None,
    allowed_path_segments: frozenset[str] = frozenset(),
) -> tuple[list[FormatIssueCode], list[FormatIssueCode]]:
    """Run the LingBot rules and return (rejection codes, warning codes)."""
    report = check_lingbot_layout(
        _f(*paths, size=size),
        layout or LINGBOT_LAYOUT,
        weight_map=weight_map,
        allowed_path_segments=allowed_path_segments,
    )
    return [i.code for i in report.errors], [i.code for i in report.warnings]


# ── The layout object itself ──────────────────────────────────────────────


def test_lingbot_layout_is_frozen_and_fully_typed() -> None:
    """The five fields must come from one competition row, so they travel bound
    together and cannot be reassigned afterwards."""
    hints = get_type_hints(LingbotLayout)
    assert set(hints) == {
        "model_config_file",
        "weights_index_file",
        "camera_names",
        "joint_field_names",
        "cli_config_file",
    }
    assert hints["camera_names"] == tuple[str, ...]
    assert hints["joint_field_names"] == tuple[str, ...]
    with pytest.raises(FrozenInstanceError):
        LINGBOT_LAYOUT.model_config_file = "other.json"  # type: ignore[misc]


def test_camera_names_are_never_a_package_constant() -> None:
    """Two cameras this season, maybe three next season. Freezing either count
    into the package means publishing a release to change a camera count, and
    every miner upgrading the CLI for it."""
    assert [n for n in dir(model_format) if "CAMERA" in n] == []
    assert [n for n in dir(model_format) if "JOINT" in n] == []


def test_there_is_no_lingbot_layout_singleton() -> None:
    """``LIBERO_LAYOUT`` can be a singleton because every field of it is a
    constant. Three fields of ``LingbotLayout`` come from a competition's
    parameters, so a singleton would freeze one season into the package."""
    assert not hasattr(model_format, "LINGBOT_LAYOUT")


def test_lingbot_file_name_constants() -> None:
    """Read off robbyant/lingbot-vla-v2-6b@11c703bf. Changing one silently
    changes who gets rejected."""
    assert LINGBOT_MODEL_CONFIG_FILE == "config.json"
    assert LINGBOT_WEIGHTS_INDEX_FILE == "model.safetensors.index.json"
    assert LINGBOT_REQUIRED_TENSOR_PREFIXES == frozenset(
        {
            "model.action_in_proj",
            "model.action_out_proj",
            "model.state_proj",
            "model.qwenvl_with_expert.qwen_expert.",
        }
    )


def test_checkpoint_kind_still_has_exactly_two_members() -> None:
    """No ``LINGBOT`` member was added. This enum is the weight *form* and its
    literals match the evaluator's ``checkpoint_type``; LingBot ships sharded
    safetensors, so its form is ``pytorch``. Which base model a checkpoint holds
    is expressed by the layout the caller chose — an orthogonal question that
    does not belong in the same enum."""
    assert sorted(k.value for k in CheckpointKind) == ["jax", "pytorch"]


# ── Must be accepted ──────────────────────────────────────────────────────


def test_minimal_lingbot_checkpoint_is_accepted() -> None:
    report = check_lingbot_layout(_f(*LINGBOT_MINIMAL), LINGBOT_LAYOUT)
    assert report.ok, report.errors
    assert report.kind is CheckpointKind.PYTORCH


def test_lingbot_nesting_is_accepted_like_openpi() -> None:
    """uid 130 put a whole openpi checkpoint under ``merged/`` and it was a legal
    submission; the same has to hold here."""
    nested = [f"merged/{p}" for p in LINGBOT_MINIMAL]
    assert check_lingbot_layout(_f(*nested), LINGBOT_LAYOUT).ok


def test_lingbot_deeper_nesting_warns_but_passes() -> None:
    nested = [f"a/b/c/{p}" for p in LINGBOT_MINIMAL]
    report = check_lingbot_layout(_f(*nested), LINGBOT_LAYOUT)
    assert report.ok
    assert [i.code for i in report.warnings] == [FormatIssueCode.NESTED_TOO_DEEP]


def test_lingbot_cli_config_present_satisfies_the_rule() -> None:
    """When a competition does name an export descriptor, having it is enough —
    the rule looks for the name the competition gave, not a hard-coded one."""
    layout = replace(LINGBOT_LAYOUT, cli_config_file="lingbotvla_cli.yaml")
    errors, _ = _lb(*LINGBOT_MINIMAL, "lingbotvla_cli.yaml", layout=layout)
    assert errors == []


def test_lingbot_rejected_segments_can_be_whitelisted() -> None:
    """The same escape hatch openpi has, for the day a real miner is falsely
    rejected."""
    errors, _ = _lb(
        *LINGBOT_MINIMAL, ".cache/x.bin", allowed_path_segments=frozenset({".cache"})
    )
    assert errors == []


# ── Must be rejected ──────────────────────────────────────────────────────


def test_lingbot_without_the_weight_index_is_not_recognized() -> None:
    """A bare ``model.safetensors`` beside a ``config.json`` is what an openpi
    PyTorch submission looks like. Accepting it here would mean one repo
    satisfies both rule sets, which is the thing the two of them exist to keep
    apart."""
    errors, _ = _lb("config.json", "model.safetensors")
    assert errors == [FormatIssueCode.MISSING_WEIGHTS]


def test_a_caller_supplied_weight_map_stands_in_for_the_index_file() -> None:
    """A caller that read the inventory some other way already knows what this
    is; do not make it upload a file to prove it."""
    unsharded = {k: "model.safetensors" for k in LINGBOT_WEIGHT_MAP}
    errors, _ = _lb("config.json", "model.safetensors", weight_map=unsharded)
    assert errors == []


def test_lingbot_without_model_config_is_rejected() -> None:
    errors, _ = _lb(*[p for p in LINGBOT_MINIMAL if p != "config.json"])
    assert errors == [FormatIssueCode.MISSING_MODEL_CONFIG]


def test_lingbot_bare_lora_is_rejected_with_its_own_reason() -> None:
    errors, _ = _lb("config.json", "adapter_config.json", "adapter_model.safetensors")
    assert errors == [FormatIssueCode.BARE_LORA_ADAPTER]


def test_lingbot_adapter_beside_merged_weights_is_fine() -> None:
    errors, _ = _lb(*LINGBOT_MINIMAL, "adapter_model.safetensors")
    assert errors == []


def test_lingbot_missing_shard_names_the_file() -> None:
    """The message has to say which shard, otherwise the miner has to diff the
    index by hand."""
    files = _f(*[p for p in LINGBOT_MINIMAL if p != "model-00002-of-00002.safetensors"])
    report = check_lingbot_layout(files, LINGBOT_LAYOUT, weight_map=LINGBOT_WEIGHT_MAP)
    assert [i.code for i in report.errors] == [FormatIssueCode.MISSING_WEIGHT_SHARD]
    assert "model-00002-of-00002.safetensors" in report.errors[0].message


def test_lingbot_weight_map_omitted_skips_the_shard_rules() -> None:
    """No index read means no evidence about shards — absence of evidence must
    not turn into a rejection."""
    errors, _ = _lb(*[p for p in LINGBOT_MINIMAL if not p.startswith("model-0000")])
    assert FormatIssueCode.MISSING_WEIGHT_SHARD not in errors
    assert FormatIssueCode.MISSING_REQUIRED_TENSOR not in errors


def test_lingbot_without_the_action_expert_is_rejected() -> None:
    """Every file name right, but the tensors are a plain Qwen3-VL: it would
    load and then have nothing to produce actions with."""
    pruned = {
        k: v
        for k, v in LINGBOT_WEIGHT_MAP.items()
        if not k.startswith("model.qwenvl_with_expert.qwen_expert.")
    }
    errors, _ = _lb(*LINGBOT_MINIMAL, weight_map=pruned)
    assert errors == [FormatIssueCode.MISSING_REQUIRED_TENSOR]


def test_lingbot_too_small_repo_is_rejected() -> None:
    errors, _ = _lb(*LINGBOT_MINIMAL, size=1024)
    assert errors == [FormatIssueCode.TOTAL_SIZE_TOO_SMALL]


def test_lingbot_size_is_not_reported_on_top_of_a_real_problem() -> None:
    """Same judgement order as openpi: a broken repo is small anyway, so the
    size is only reported when nothing else is wrong."""
    errors, _ = _lb("model.safetensors", size=10)
    assert errors == [
        FormatIssueCode.MISSING_MODEL_CONFIG,
        FormatIssueCode.MISSING_WEIGHTS,
    ]


# ── The two checkers share one set of base-model-independent rules ─────────


def test_both_checkers_scan_shared_rules_alike() -> None:
    """``check_lingbot_layout`` re-implements the per-file scan instead of
    refactoring ``check_checkpoint_layout`` into a shared helper: that function
    decides whether TAO a miner already burned counts, it has been published
    since 0.6.0, and an empty diff is the cheapest proof it still judges round 1
    the way it did.

    This test is what buys that duplication back. If the two copies ever drift on
    repo-internal state, unfinished uploads or the counted byte total, it goes
    red — instead of a miner going missing.
    """
    cases = [
        _f("model.safetensors", NORM_STATS),
        _f("model.safetensors", NORM_STATS, ".gitattributes"),
        _f("model.safetensors", NORM_STATS, ".cache/huggingface/x.bin"),
        _f("model.safetensors", NORM_STATS, "checkpoint.001.tmp"),
        _f("a/b/.git/config", "model.safetensors", NORM_STATS, size=7),
    ]
    shared = {
        FormatIssueCode.LEFTOVER_UPLOAD_STATE,
        FormatIssueCode.INCOMPLETE_FILE,
    }
    for files in cases:
        openpi = check_checkpoint_layout(files)
        lingbot = check_lingbot_layout(files, LINGBOT_LAYOUT)
        assert openpi.counted_size_bytes == lingbot.counted_size_bytes, files
        assert [i for i in openpi.errors if i.code in shared] == [
            i for i in lingbot.errors if i.code in shared
        ], files


def test_shared_thresholds_are_not_duplicated() -> None:
    """The 10 MB floor and the nesting depth are base-model-independent, so both
    checkers read the same constant rather than each carrying a literal."""
    source = Path(model_format.__file__).read_text(encoding="utf-8")
    assert source.count("10 * 1024 * 1024") == 1
    assert MIN_TOTAL_SIZE_BYTES == 10 * 1024 * 1024


# ── The codes are the outward contract ────────────────────────────────────


def test_the_nine_original_issue_codes_still_have_their_values() -> None:
    """Miner scripts, frontend copy and alert rules all match on these strings.
    Changing one does not break a build anywhere — it silently stops matching."""
    assert {c.value for c in FormatIssueCode} >= {
        "missing_weights",
        "bare_lora_adapter",
        "missing_norm_stats",
        "leftover_upload_state",
        "incomplete_file",
        "total_size_too_small",
        "unloadable_weights_format",
        "non_canonical_norm_stats",
        "nested_too_deep",
    }


def test_the_lingbot_issue_codes_exist_with_the_agreed_values() -> None:
    for value in (
        "missing_cli_config",
        "missing_model_config",
        "missing_weight_shard",
        "missing_required_tensor",
        "base_model_mismatch",
        "io_contract_mismatch",
    ):
        assert FormatIssueCode(value).value == value


def test_issue_code_count_is_pinned() -> None:
    """Adding a code is a contract change; it should not slip in unnoticed."""
    assert len(FormatIssueCode) == 15


def test_every_issue_code_explains_itself() -> None:
    """A code a miner cannot look up is a code they cannot act on. The length
    floor keeps out docstrings that only restate the name."""
    for code in FormatIssueCode:
        assert code.__doc__ and len(code.__doc__) > 20, code


def test_codes_are_partitioned_into_rejecting_and_warning() -> None:
    """The split used to live only in a ``(warning)`` prefix inside docstrings,
    with nothing guarding it. Forgetting to classify a new code now goes red."""
    assert REJECTING_ISSUE_CODES | WARNING_ISSUE_CODES == set(FormatIssueCode)
    assert not REJECTING_ISSUE_CODES & WARNING_ISSUE_CODES


def test_every_lingbot_code_rejects() -> None:
    """All six mean the evaluator cannot load or cannot run it, so letting them
    through would burn a GPU slot for nothing."""
    for code in (
        FormatIssueCode.MISSING_CLI_CONFIG,
        FormatIssueCode.MISSING_MODEL_CONFIG,
        FormatIssueCode.MISSING_WEIGHT_SHARD,
        FormatIssueCode.MISSING_REQUIRED_TENSOR,
        FormatIssueCode.BASE_MODEL_MISMATCH,
        FormatIssueCode.IO_CONTRACT_MISMATCH,
    ):
        assert code in REJECTING_ISSUE_CODES
        assert code not in WARNING_ISSUE_CODES


def test_module_exports_are_pinned() -> None:
    """``__all__`` is what SemVer's promise is about; without it there is no
    criterion separating a patch from a major (AGENTS.md §1②)."""
    assert model_format.__all__ == [
        "INCOMPLETE_FILE_SUFFIXES",
        "LEGACY_NORM_STATS_RELPATHS",
        "LEGACY_PYTORCH_WEIGHTS_FILE",
        "LIBERO_LAYOUT",
        "LINGBOT_MODEL_CONFIG_FILE",
        "LINGBOT_REQUIRED_TENSOR_PREFIXES",
        "LINGBOT_WEIGHTS_INDEX_FILE",
        "LORA_ADAPTER_MARKERS",
        "MAX_CHECKPOINT_NESTING_DEPTH",
        "MIN_TOTAL_SIZE_BYTES",
        "REJECTED_PATH_SEGMENTS",
        "REJECTING_ISSUE_CODES",
        "WARNING_ISSUE_CODES",
        "CheckpointFile",
        "CheckpointKind",
        "FormatIssue",
        "FormatIssueCode",
        "FormatReport",
        "LingbotLayout",
        "OpenpiLayout",
        "check_checkpoint_layout",
        "check_lingbot_layout",
    ]
