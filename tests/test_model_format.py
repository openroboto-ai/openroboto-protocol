"""Edge behaviour of ``model_format``. Real miner repos live in
``test_model_golden_vectors.py``.

The cases split in two: the ones that **must be accepted** (accepting wrongly =
rejecting a miner who has already burned TAO) and the ones that **must be
rejected** (rejecting wrongly = burning a slot of GPU time for nothing).
"""

from __future__ import annotations

from openroboto_protocol.model_format import (
    LIBERO_LAYOUT,
    MIN_TOTAL_SIZE_BYTES,
    CheckpointFile,
    CheckpointKind,
    FormatIssueCode,
    check_checkpoint_layout,
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
