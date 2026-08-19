"""What a submittable checkpoint has to look like — the admission contract at
the layout level.

Contract meaning
----------------
Miners export according to it, the backend admits according to it, the
evaluator rejects according to it. Only when all three look at the same set of
rules can a miner know **before burning TAO** whether they will be rejected.
Right now this rule has two implementations (the backend's ``hf_validate.py``
judges the HF repo tree, the evaluator's ``libero_eval/check_model.py`` judges a
local directory), and a miner has to clone a second repo to self-check — find
out the format is wrong only after burning, and the TAO is thrown away.

The input is a **file list** (path + byte size); where the list comes from is
not this module's business: the backend gets it from the HF tree API, a miner
gets it from ``os.walk`` over a local directory, the evaluator gets it from the
directory it finished downloading. Same list, and all three arrive at the same
conclusion.

Not responsible for (all of these need to read file contents, they stay in the
evaluator)
------------------------------------------------------------------------------
- The safetensors header, orbax ``params/_METADATA``, the parameter-count
  range, the numbers inside norm_stats;
- Whether the architecture is π0.5 (``time_mlp_*`` vs ``state_proj``);
- Downloading, parsing HF API responses, deciding whether a revision exists.

**Passing the layout check does not mean evaluation will actually load it.**
Passing here only says "this is worth spending GPU time trying".

The division of labour between errors and warnings
--------------------------------------------------
``errors`` reproduces the judgement of **production admission**
(``hf_validate.validate_file_list``), not one condition more and not one fewer
— it decides whether a burn of TAO that has already happened counts, which is
not the place to tighten things up in passing.
``warnings`` are the known divergences where "admission lets it through but the
evaluator cannot load it"; they do not change the judgement, they only tell the
miner in advance about the pit they are going to fall into. The divergence
itself is still to be adjudicated (see openQuestions inside the package).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class CheckpointKind(StrEnum):
    """The two weight forms a checkpoint can take. The literals match the
    evaluator's ``checkpoint_type``.

    When both are present openpi loads the PyTorch weights and ignores
    ``params/``, so :func:`check_checkpoint_layout` reports them in the same
    order of precedence.
    """

    PYTORCH = "pytorch"
    JAX = "jax"


class FormatIssueCode(StrEnum):
    """Stable machine codes for a rejection / a heads-up.

    ``message`` is for humans (it is passed back to the miner verbatim, and it
    will change), ``code`` is for programs (miner-side scripts, frontend copy,
    and alerting rules may all match on it, so it **must not** be changed).
    Adding a new code is minor, changing an old one is major.
    """

    MISSING_WEIGHTS = "missing_weights"
    """Neither ``model.safetensors`` nor ``params/`` — this is not a
    checkpoint."""

    BARE_LORA_ADAPTER = "bare_lora_adapter"
    """Only a LoRA adapter, without the merged full weights. The evaluator does
    no merging."""

    MISSING_NORM_STATS = "missing_norm_stats"
    """Normalization stats are missing, so inference cannot normalize the input
    or unnormalize the actions."""

    LEFTOVER_UPLOAD_STATE = "leftover_upload_state"
    """Repository-internal state such as ``.git`` / ``.cache`` uploaded by
    mistake."""

    INCOMPLETE_FILE = "incomplete_file"
    """Files that did not finish uploading, such as ``.tmp`` / ``.partial``."""

    TOTAL_SIZE_TOO_SMALL = "total_size_too_small"
    """The whole repo is under 10 MB — most likely only the LFS pointers were
    uploaded, not the weights."""

    UNLOADABLE_WEIGHTS_FORMAT = "unloadable_weights_format"
    """(warning) A weights filename that admission accepts but the evaluator
    cannot load."""

    NON_CANONICAL_NORM_STATS = "non_canonical_norm_stats"
    """(warning) norm_stats sits at one of the alternative locations that
    admission accepts, but the evaluator reads only the canonical one."""

    NESTED_TOO_DEEP = "nested_too_deep"
    """(warning) The checkpoint is nested deeper than the number of levels the
    evaluator searches."""


@dataclass(frozen=True)
class OpenpiLayout:
    """The layout of an openpi checkpoint. These fields must come from the same
    source, so they are bound together.

    ``norm_stats_relpath`` is **derived** rather than written as yet another
    constant — the evaluator assembles it exactly as
    ``assets / asset_id / "norm_stats.json"``, and writing it as two independent
    constants would drift sooner or later.
    """

    asset_id: str
    """Norm stats hang under ``assets/<asset_id>/``. For LIBERO this is the
    asset id from upstream openpi."""

    pytorch_weights_file: str
    """Filename of the weights in an openpi PyTorch checkpoint."""

    jax_params_dir: str
    """Directory name of the orbax OCDBT store in an openpi JAX checkpoint."""

    @property
    def norm_stats_relpath(self) -> str:
        """Path of the normalization stats relative to the checkpoint root."""
        return f"assets/{self.asset_id}/norm_stats.json"


LIBERO_LAYOUT: Final = OpenpiLayout(
    asset_id="physical-intelligence/libero",
    pytorch_weights_file="model.safetensors",
    jax_params_dir="params",
)
"""The only layout the subnet currently accepts: openpi + the LIBERO asset."""

LEGACY_PYTORCH_WEIGHTS_FILE: Final = "pytorch_model.bin"
"""Production admission has historically accepted this name. The evaluator
**cannot load** it — let it through, but emit a warning."""

LEGACY_NORM_STATS_RELPATHS: Final = ("assets/libero/norm_stats.json", "norm_stats.json")
"""The alternative norm_stats locations production admission has historically
accepted. The evaluator reads only the canonical path — let it through, but
emit a warning."""

LORA_ADAPTER_MARKERS: Final = frozenset(
    {"adapter_config.json", "adapter_model.safetensors", "adapter_model.bin"}
)
"""Characteristic filenames of a bare LoRA adapter. They are recognized only so
that the rejection can come with a reason that **makes sense**."""

REJECTED_PATH_SEGMENTS: Final = frozenset(
    {
        ".git",
        ".cache",
        ".locks",
        ".no_exist",
        ".ipynb_checkpoints",
        ".DS_Store",
        ".Trash",
    }
)
"""Repository-internal state uploaded by mistake, listed explicitly.

⚠️ **This must not be turned into "reject anything starting with a dot".** HF
generates ``.gitattributes`` automatically when a repo is created; on
2026-08-14 a prefix-based rejection rule was wired up to production and falsely
rejected uid 221 / 231, two submissions that **had already burned TAO**; a spot
check of 8 repos on the board that had already passed evaluation found the file
in 8 out of 8 — the rule taking effect is equivalent to rejecting everyone.
Adding a name requires a specific reason.
"""

INCOMPLETE_FILE_SUFFIXES: Final = frozenset(
    {".tmp", ".temp", ".partial", ".crdownload", ".download", ".lock", ".swp", ".swo"}
)
"""Suffixes of unfinished / temporary files."""

MIN_TOTAL_SIZE_BYTES: Final = 10 * 1024 * 1024
"""Minimum size of the whole repo. Below this number there are basically only
LFS pointers and no real weights."""

MAX_CHECKPOINT_NESTING_DEPTH: Final = 2
"""The evaluator looks for the checkpoint only at three levels: the root,
``*/``, and ``*/*/``. Buried deeper than that, it will not find it."""


@dataclass(frozen=True)
class CheckpointFile:
    """One file in the list. Path and size must come from the same source — the
    judgement uses both at once.

    ``path`` is a POSIX path relative to the checkpoint root (or the repo root),
    and does not start with ``/``.
    Only pass files; directory entries do no harm if passed in (size 0, they
    match no rule), but do not expect them to be judged.
    """

    path: str
    size_bytes: int


@dataclass(frozen=True)
class FormatIssue:
    """One judgement result. ``message`` is the English reason passed back to
    the miner verbatim."""

    code: FormatIssueCode
    message: str


@dataclass(frozen=True)
class FormatReport:
    """The complete conclusion of one layout judgement."""

    kind: CheckpointKind | None
    """The weight form that was recognized; ``None`` = no loadable checkpoint
    was recognized."""

    errors: tuple[FormatIssue, ...]
    """Non-empty = the submission is rejected. Order: per-file problems →
    missing weights → missing norm_stats → size too small."""

    warnings: tuple[FormatIssue, ...]
    """They do not affect the judgement, but the miner will very likely hit
    them during the evaluation stage."""

    counted_size_bytes: int
    """The number of bytes that took part in the size judgement. Dotfiles and
    rejected files are not counted.

    Do not re-sum this in the caller — a different way of summing makes the
    "size too small" judgement disagree.
    """

    @property
    def ok(self) -> bool:
        """Whether it can be submitted."""
        return not self.errors


def _suffix(basename: str) -> str:
    """Take the suffix after the **last** dot (dot included); empty string if
    there is none.

    Taking the first dot would make the suffix of ``checkpoint.001.tmp`` come
    out as ``.001.tmp``, which matches nothing in
    :data:`INCOMPLETE_FILE_SUFFIXES` — any filename with several dots could then
    get past the temporary-file check, and catching leftover uploads is the
    entire purpose of that check.
    """
    idx = basename.rfind(".")
    return basename[idx:] if idx > 0 else ""


def _matches(path: str, relpath: str) -> bool:
    """Whether the path is ``relpath`` itself, or ``relpath`` nested under any
    number of subdirectories.

    Nesting must be accepted: uid 130 put the whole openpi checkpoint under
    ``merged/`` (10.8 GB, norm_stats complete, a legitimate submission), and
    accepting only the repo root would have judged it as "model file missing".
    """
    return path == relpath or path.endswith("/" + relpath)


def check_checkpoint_layout(
    files: Iterable[CheckpointFile],
    *,
    allowed_path_segments: frozenset[str] = frozenset(),
) -> FormatReport:
    """Judge whether a file list can be submitted as a checkpoint.

    ``allowed_path_segments`` overrides :data:`REJECTED_PATH_SEGMENTS`
    (it corresponds to the production setting ``scanner.hf_allow_dotfiles``).
    Normally there is no need to set it — the default rules already reject only
    names that are clearly wrong; it is the on-the-spot escape hatch for when a
    real miner is falsely rejected.
    """
    errors: list[FormatIssue] = []
    warnings: list[FormatIssue] = []
    counted_size = 0

    has_pytorch = has_jax = has_legacy_weights = False
    has_canonical_stats = has_legacy_stats = has_adapter = False
    # There may be one copy of the weights at each of several levels; the
    # evaluator takes the shallowest one, so only the shallowest depth counts.
    weights_depths: list[int] = []

    for file in files:
        parts = file.path.split("/")

        if any(
            p in REJECTED_PATH_SEGMENTS and p not in allowed_path_segments
            for p in parts
        ):
            errors.append(
                FormatIssue(
                    FormatIssueCode.LEFTOVER_UPLOAD_STATE,
                    f"leftover upload state in the repo: {file.path} — remove the "
                    "repository-internal directory and re-upload",
                )
            )
            continue

        # Other dotfiles (.gitattributes / .gitignore …) are let through, and
        # are not counted towards the size: they are not model content, and HF
        # generates them itself.
        if any(p.startswith(".") for p in parts):
            continue

        counted_size += file.size_bytes
        basename = parts[-1]

        if _suffix(basename) in INCOMPLETE_FILE_SUFFIXES:
            errors.append(
                FormatIssue(
                    FormatIssueCode.INCOMPLETE_FILE,
                    f"incomplete or temporary file: {file.path} — "
                    "the upload did not finish",
                )
            )
            continue

        # Directory-shaped markers are looked for in the middle segments of the
        # path, file-shaped markers in the whole path.
        if LIBERO_LAYOUT.jax_params_dir in parts[:-1]:
            has_jax = True
            weights_depths.append(parts.index(LIBERO_LAYOUT.jax_params_dir))
        if _matches(file.path, LIBERO_LAYOUT.pytorch_weights_file):
            has_pytorch = True
            weights_depths.append(len(parts) - 1)
        if _matches(file.path, LEGACY_PYTORCH_WEIGHTS_FILE):
            has_legacy_weights = True
        if _matches(file.path, LIBERO_LAYOUT.norm_stats_relpath):
            has_canonical_stats = True
        if any(_matches(file.path, rel) for rel in LEGACY_NORM_STATS_RELPATHS):
            has_legacy_stats = True
        if basename in LORA_ADAPTER_MARKERS:
            has_adapter = True

    if not (has_pytorch or has_jax or has_legacy_weights):
        if has_adapter:
            errors.append(
                FormatIssue(
                    FormatIssueCode.BARE_LORA_ADAPTER,
                    "this is a bare LoRA adapter, not a checkpoint — the "
                    "evaluator does no merging. Merge the adapter back into the "
                    "pi0.5 base and upload the full "
                    f"checkpoint ('{LIBERO_LAYOUT.pytorch_weights_file}' or "
                    f"'{LIBERO_LAYOUT.jax_params_dir}/').",
                )
            )
        else:
            errors.append(
                FormatIssue(
                    FormatIssueCode.MISSING_WEIGHTS,
                    "no model weights found — expected openpi PyTorch weights "
                    f"('{LIBERO_LAYOUT.pytorch_weights_file}') or a JAX orbax "
                    "checkpoint "
                    f"('{LIBERO_LAYOUT.jax_params_dir}/')",
                )
            )
    elif has_legacy_weights and not (has_pytorch or has_jax):
        warnings.append(
            FormatIssue(
                FormatIssueCode.UNLOADABLE_WEIGHTS_FORMAT,
                f"'{LEGACY_PYTORCH_WEIGHTS_FILE}' passes submission admission but the "
                f"evaluator loads only '{LIBERO_LAYOUT.pytorch_weights_file}' or "
                f"'{LIBERO_LAYOUT.jax_params_dir}/' — it will be rejected before "
                "the GPU runs",
            )
        )

    depth = min(weights_depths, default=0)
    if depth > MAX_CHECKPOINT_NESTING_DEPTH:
        warnings.append(
            FormatIssue(
                FormatIssueCode.NESTED_TOO_DEEP,
                f"the checkpoint is nested {depth} levels deep; the evaluator only "
                f"searches {MAX_CHECKPOINT_NESTING_DEPTH} levels below the repo root",
            )
        )

    if not (has_canonical_stats or has_legacy_stats):
        errors.append(
            FormatIssue(
                FormatIssueCode.MISSING_NORM_STATS,
                f"missing normalization stats: {LIBERO_LAYOUT.norm_stats_relpath} — "
                "inference cannot normalize the state or unnormalize the "
                "actions without them",
            )
        )
    elif not has_canonical_stats:
        warnings.append(
            FormatIssue(
                FormatIssueCode.NON_CANONICAL_NORM_STATS,
                "normalization stats are not at the canonical path "
                f"{LIBERO_LAYOUT.norm_stats_relpath}; the evaluator reads only "
                "that path",
            )
        )

    # Only report the size when there is no other problem. A repo with files
    # missing is small anyway, and reporting both at once is noise.
    if counted_size < MIN_TOTAL_SIZE_BYTES and not errors:
        errors.append(
            FormatIssue(
                FormatIssueCode.TOTAL_SIZE_TOO_SMALL,
                f"total size {counted_size / 1024 / 1024:.1f} MB is below the "
                f"{MIN_TOTAL_SIZE_BYTES // 1024 // 1024} MB minimum — the weights are "
                "probably git-lfs pointers, not the real files",
            )
        )

    kind = (
        CheckpointKind.PYTORCH
        if has_pytorch
        else CheckpointKind.JAX
        if has_jax
        else None
    )
    return FormatReport(
        kind=kind,
        errors=tuple(errors),
        warnings=tuple(warnings),
        counted_size_bytes=counted_size,
    )
