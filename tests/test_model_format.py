"""``model_format`` 的边界行为。真实矿工仓库在 ``test_model_golden_vectors.py``。

用例分两类：**必须放行的**（放行错了 = 拒了已经烧过 TAO 的矿工）和
**必须拒绝的**（拒错了 = 白烧一次 GPU 时间）。
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


# ── 布局本身 ──────────────────────────────────────────────────────────────


def test_norm_stats_path_is_derived_from_the_asset_id() -> None:
    """路径不是另写一个常量，是从 ``asset_id`` 推出来的，两者不可能漂。"""
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
    """openpi 加载 ``model.safetensors`` 并忽略 ``params/``，报告跟它走。"""
    report = check_checkpoint_layout(
        _f("model.safetensors", "params/_METADATA", NORM_STATS)
    )
    assert report.kind is CheckpointKind.PYTORCH


def test_a_file_literally_named_params_is_not_a_jax_checkpoint() -> None:
    """``params`` 得是路径中间的一层目录，不能是最后那个文件名。"""
    assert _codes(_f("params", NORM_STATS)) == [FormatIssueCode.MISSING_WEIGHTS]


def test_nested_layout_is_accepted_at_any_depth() -> None:
    """uid 130 把整棵 checkpoint 放在 ``merged/`` 下，是合法提交。"""
    assert check_checkpoint_layout(
        _f("merged/model.safetensors", f"merged/{NORM_STATS}")
    ).ok


def test_deeper_nesting_than_the_evaluator_searches_warns_but_passes() -> None:
    """准入放行（生产就是这么判的），但评测器只往下找两层 —— 提前告诉矿工。"""
    files = _f(f"a/b/c/{LIBERO_LAYOUT.pytorch_weights_file}", f"a/b/c/{NORM_STATS}")
    report = check_checkpoint_layout(files)
    assert report.ok
    assert [i.code for i in report.warnings] == [FormatIssueCode.NESTED_TOO_DEEP]


def test_a_shallow_copy_silences_the_nesting_warning() -> None:
    """评测器取最浅的那份，所以只要有一份够浅就没问题。"""
    files = _f("a/b/c/model.safetensors", "model.safetensors", NORM_STATS)
    assert check_checkpoint_layout(files).warnings == ()


# ── 必须拒绝 ──────────────────────────────────────────────────────────────


def test_no_weights_is_rejected() -> None:
    assert _codes(_f("README.md", NORM_STATS)) == [FormatIssueCode.MISSING_WEIGHTS]


def test_bare_lora_adapter_is_rejected_with_its_own_reason() -> None:
    """判定和「缺权重」一样是拒，但理由要说得清 —— 矿工得知道去合并。"""
    files = _f("adapter_config.json", "adapter_model.safetensors", NORM_STATS)
    report = check_checkpoint_layout(files)
    assert [i.code for i in report.errors] == [FormatIssueCode.BARE_LORA_ADAPTER]
    assert "merge" in report.errors[0].message.lower()
    assert report.kind is None


def test_merged_checkpoint_shipped_next_to_the_adapter_is_fine() -> None:
    """合并后的完整权重在，顺手把 adapter 也传上来不算裸 adapter。"""
    files = _f("adapter_model.safetensors", "model.safetensors", NORM_STATS)
    assert check_checkpoint_layout(files).ok


def test_missing_norm_stats_is_rejected() -> None:
    assert _codes(_f("model.safetensors")) == [FormatIssueCode.MISSING_NORM_STATS]


def test_leftover_upload_state_is_rejected() -> None:
    """``.cache/models/x.bin`` 的 basename 不是 dotfile，必须逐段查路径。"""
    files = _f("model.safetensors", NORM_STATS, ".cache/huggingface/x.bin")
    assert _codes(files) == [FormatIssueCode.LEFTOVER_UPLOAD_STATE]


def test_incomplete_file_is_rejected_even_with_a_multi_dot_name() -> None:
    """取第一个点时 ``checkpoint.001.tmp`` 的后缀会算成 ``.001.tmp``，漏判。"""
    files = _f("model.safetensors", NORM_STATS, "checkpoint.001.tmp")
    assert _codes(files) == [FormatIssueCode.INCOMPLETE_FILE]


def test_too_small_repo_is_rejected() -> None:
    """十几 KB 的"权重"基本只是 LFS 指针，没传真文件。"""
    files = _f("model.safetensors", NORM_STATS, size=1024)
    report = check_checkpoint_layout(files)
    assert [i.code for i in report.errors] == [FormatIssueCode.TOTAL_SIZE_TOO_SMALL]
    assert report.counted_size_bytes == 2048


def test_size_is_not_reported_on_top_of_a_real_problem() -> None:
    """缺文件的仓库本来就小，两条一起报是噪音。生产就是这么判的。"""
    assert _codes(_f("README.md", size=10)) == [
        FormatIssueCode.MISSING_WEIGHTS,
        FormatIssueCode.MISSING_NORM_STATS,
    ]


def test_size_threshold_is_ten_megabytes() -> None:
    files = _f("model.safetensors", NORM_STATS, size=MIN_TOTAL_SIZE_BYTES // 2)
    assert check_checkpoint_layout(files).ok


# ── dotfile：2026-08-14 误拒事故 ──────────────────────────────────────────


def test_gitattributes_is_not_a_problem() -> None:
    """HF 建仓库时自动生成它。按前缀拒等于拒绝所有矿工（8/8 的仓库都有）。"""
    files = _f("model.safetensors", NORM_STATS, ".gitattributes")
    report = check_checkpoint_layout(files)
    assert report.ok
    assert report.warnings == ()


def test_dotfiles_do_not_count_towards_the_size() -> None:
    """它们不是模型内容，算进体积会让「太小」这条判定失真。"""
    files = _f("model.safetensors", NORM_STATS, ".gitignore", size=1024)
    assert check_checkpoint_layout(files).counted_size_bytes == 2048


def test_rejected_segments_can_be_whitelisted() -> None:
    """误拒真实矿工时的现场逃生口（生产配置 ``scanner.hf_allow_dotfiles``）。"""
    files = _f("model.safetensors", NORM_STATS, ".cache/x.bin")
    allowed = frozenset({".cache"})
    assert check_checkpoint_layout(files, allowed_path_segments=allowed).ok


# ── 准入放行、但评测器加载不了的历史分歧 ──────────────────────────────────


def test_legacy_pytorch_bin_passes_admission_with_a_warning() -> None:
    """生产准入认 ``pytorch_model.bin``，评测器不认 —— 判定不动，先把话说清。"""
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
    """生产准入还认 ``assets/libero/`` 和裸 ``norm_stats.json`` 两个位置。"""
    files = _f("model.safetensors", "assets/libero/norm_stats.json")
    report = check_checkpoint_layout(files)
    assert report.ok
    assert [i.code for i in report.warnings] == [
        FormatIssueCode.NON_CANONICAL_NORM_STATS
    ]


def test_bare_norm_stats_at_the_root_also_passes_with_a_warning() -> None:
    files = _f("model.safetensors", "norm_stats.json")
    assert _warn_codes(files) == [FormatIssueCode.NON_CANONICAL_NORM_STATS]
