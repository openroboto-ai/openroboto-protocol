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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
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
    evaluator searches.

    🔴 **The most expensive warning in this file, and the one a miner is most
    likely to hit by doing nothing wrong.** The vendor's own post-trained
    artifact (``robbyant/lingbot-vla-v2-6b-robotwin``) keeps its weights under
    ``checkpoints/global_step_50000/hf_ckpt/`` — three levels down, one past
    :data:`MAX_CHECKPOINT_NESTING_DEPTH` — so a miner who uploads the training
    output *as it comes out* is copying the published example. It passes
    admission, the TAO is burned, the queue slot is taken, and the evaluator
    then finds no weights. Failing at the last step costs more than being
    rejected at the first, which is why a consumer that runs before payment
    (the CLI's ``openroboto check``) should refuse to continue on this warning
    rather than print it and move on.
    """

    # ── Added in 0.7.0 for LingBot-VLA 2.0. Appended at the end on purpose: an
    #    enum member inserted in the middle changes no behaviour, but it makes
    #    the diff look as though the old codes moved. ────────────────────────

    MISSING_CLI_CONFIG = "missing_cli_config"
    """The export descriptor the competition's LingBot toolchain writes is not in
    the file list. Rejecting. Fix: upload the descriptor the official export
    script produced next to the weights.

    ⚠️ Only evaluated when the competition names the file
    (``LingbotLayout.cli_config_file``). See that field for why it is not a
    package constant.
    """

    MISSING_MODEL_CONFIG = "missing_model_config"
    """``config.json`` is not in the file list, so the base model family cannot be
    read at all. Rejecting. Fix: upload the whole checkpoint directory, not only
    the weight files."""

    MISSING_WEIGHT_SHARD = "missing_weight_shard"
    """``model.safetensors.index.json`` maps tensors onto shard files, and one of
    those shards is not in the file list. Rejecting — a missing shard means the
    weights cannot be loaded at all. Fix: re-upload the missing
    ``model-0000X-of-0000N.safetensors``."""

    MISSING_REQUIRED_TENSOR = "missing_required_tensor"
    """The weight index has no tensor under one of the LingBot action /state
    projection or action-expert prefixes, so this is not a LingBot-VLA
    checkpoint even if the file names look right. Rejecting. Fix: export the
    full weights, do not prune tensors."""

    BASE_MODEL_MISMATCH = "base_model_mismatch"
    """``config.json`` names a model family other than the one this competition
    froze, or the architecture fields do not match it. Rejecting. Fix: retrain or
    re-export on the base model named in this competition's announcement.

    Produced by the backend's CPU pre-check (it reads file contents, which this
    package does not do). The code lives here so that the backend does not
    hand-write the literal — that is the drift this package exists to prevent.
    """

    IO_CONTRACT_MISMATCH = "io_contract_mismatch"
    """The camera names or the joint/gripper field names the checkpoint was
    exported with differ from the ones this competition publishes, so the
    evaluator cannot feed it. Rejecting. Fix: re-export against the camera and
    joint names in this competition's announcement.

    Also produced by the backend's CPU pre-check; see
    :attr:`BASE_MODEL_MISMATCH`.
    """


WARNING_ISSUE_CODES: Final = frozenset(
    {
        FormatIssueCode.UNLOADABLE_WEIGHTS_FORMAT,
        FormatIssueCode.NON_CANONICAL_NORM_STATS,
        FormatIssueCode.NESTED_TOO_DEEP,
    }
)
"""Codes that never reject: admission accepts the submission, but the evaluator
is likely to trip over it, so the miner is told in advance."""

REJECTING_ISSUE_CODES: Final = frozenset(FormatIssueCode) - WARNING_ISSUE_CODES
"""Codes that reject: the evaluator cannot install or cannot run it, so letting
it through would burn a GPU slot for nothing.

Until 0.7.0 this split existed only as a ``(warning)`` prefix inside the
docstrings, with nothing guarding it. Deriving one set from the other makes the
partition true by construction: a new code is rejecting unless it is explicitly
listed above, which is the safe default to forget.
"""


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
``*/``, and ``*/*/``. Buried deeper than that, it will not find it.

⚠️ **The number is the evaluator's, not ours.** ``openroboto-evaluation`` is
maintained by the evaluation side; raising this constant would only make the
warning disappear, not make the weights findable. The one place it can be
absorbed is before the miner pays — see
:attr:`FormatIssueCode.NESTED_TOO_DEEP`.
"""


# ── LingBot-VLA 2.0 ────────────────────────────────────────────────────────
#
# A second base model, not a replacement. Everything below is additive: the
# openpi rules above are what miners have been submitting against since round 1
# and they are not touched.
#
# Every constant here was read off the vendor's published checkpoints and
# training repo, not inferred. **Two** checkpoints are referenced, because they
# are laid out differently and only reading both shows it:
#
#   repo=robbyant/lingbot-vla-v2-6b revision=11c703bf6a5c1f45b3b69168482da11fdbba53d7
#     The base model. Weights, config and tokenizer all sit at the repo root.
#
#   repo=robbyant/lingbot-vla-v2-6b-robotwin  published=2026-07-24
#     The official **post-trained** artifact — the thing a miner is shown as an
#     example of a finished model. Same file names, but three levels down under
#     `checkpoints/global_step_50000/hf_ckpt/`, with `lingbotvla_cli.yaml` alone
#     at the repo root. Full weights, not an adapter: the training entrypoint
#     has no lora/peft path at all and runs `freeze_vision_encoder: false`
#     under fsdp2.
#
#   code=github.com/Robbyant/lingbot-vla-v2@main   fetched=2026-08-25
#
# 🔴 Why the second one had to be added. These constants were first written
# from the base model alone, and that repo hides the one divergence that costs
# a miner money: the post-trained layout is nested one level deeper than
# MAX_CHECKPOINT_NESTING_DEPTH, so uploading the training output unchanged
# passes admission and *then* fails evaluation. "Read the reference checkpoint"
# is not the same discipline as "read the artifact miners will actually copy".
# Both trees are pinned in tests/test_model_golden_vectors.py.
#
# Anything these sources do not answer is deliberately left out rather than
# guessed — a guessed rule rejects miners who have already burned TAO, which is
# the exact shape of the 2026-08-14 incident (see REJECTED_PATH_SEGMENTS).

LINGBOT_MODEL_CONFIG_FILE: Final = "config.json"
"""Carries the base model family. In the reference checkpoint its entire content
is ``{"vlm_family": "qwen3_vl"}`` — which is why this package only checks that
the file **exists**; reading the family out of it is the caller's CPU pre-check
(:attr:`FormatIssueCode.BASE_MODEL_MISMATCH`)."""

LINGBOT_WEIGHTS_INDEX_FILE: Final = "model.safetensors.index.json"
"""Maps every tensor name onto the shard file holding it. A plain git blob
(207 KB in the reference checkpoint), so a caller can read it without pulling
any LFS content — that is what makes :func:`check_lingbot_layout` able to judge
shards and tensors while staying I/O free.

**Its presence is required** (unless the caller passes the parsed map itself).
Not for its own sake, but because it is the only thing in a file list that tells
a LingBot checkpoint from an openpi one: an openpi PyTorch submission is a bare
``model.safetensors`` next to a ``config.json``, which is exactly what an
index-less LingBot export would look like, and "one repo satisfies both rule
sets" is the failure the two rule sets exist to avoid.

⚠️ **What would make that unsafe.** The vendor's exporter writes the index only
when the state dict exceeds one shard (``save_model_weights(...,
shard_size=5_000_000_000)``), and ``robbyant/lingbot-vla-4b`` — a v1 model — is
indeed one 16.7 GB ``model.safetensors`` with no index. For the 2.0 base this
cannot happen (25.5 GB of bf16 weights over a 5 GB shard limit → six shards, as
in the reference checkpoint), so a competition that froze a **smaller** LingBot
variant has to re-check this before trusting the rule.
"""

LINGBOT_REQUIRED_TENSOR_PREFIXES: Final = frozenset(
    {
        "model.action_in_proj",
        "model.action_out_proj",
        "model.state_proj",
        "model.qwenvl_with_expert.qwen_expert.",
    }
)
"""Prefixes of the tensors that make a checkpoint a LingBot-VLA rather than a
bare Qwen3-VL: the action projection in and out, the state projection, and the
MoE action expert.

Taken verbatim from the ``weight_map`` of the reference checkpoint's
``model.safetensors.index.json`` (1708 tensors). They are **prefixes**, not full
names, so that renaming a leaf (``.weight`` → ``.w``) or adding a layer does not
turn into a false rejection; and they are limited to modules that every export
of the model class necessarily contains — the optional depth / video distillation
heads are not listed even though the reference checkpoint has them.
"""


@dataclass(frozen=True)
class LingbotLayout:
    """What a LingBot-VLA 2.0 submission looks like for one competition.

    Parallel to :class:`OpenpiLayout`, never a replacement. The caller picks
    which one to use from the base model frozen into this competition's row —
    the backend from ``competitions.params``, the CLI from ``miner.yaml`` — and
    **never by sniffing the file list**. Sniffing means guessing, and a wrong
    guess rejects a miner who has already paid.

    There is deliberately **no module-level singleton** for this class (unlike
    :data:`LIBERO_LAYOUT`). Three of its five fields come from a competition's
    parameters, so a singleton would freeze one season's configuration into a
    published package, and changing a camera count would mean a new release plus
    every miner upgrading the CLI again.
    """

    model_config_file: str
    """Name of the model config file to require. Normally
    :data:`LINGBOT_MODEL_CONFIG_FILE`."""

    weights_index_file: str
    """Name of the sharded-weights index to look for. Normally
    :data:`LINGBOT_WEIGHTS_INDEX_FILE`. Its absence alone is not a rejection —
    see that constant."""

    camera_names: tuple[str, ...]
    """Camera names this competition publishes, e.g.
    ``("camera_top", "camera_wrist")`` for two arms-eye views or
    ``("camera_top", "camera_wrist_left", "camera_wrist_right")`` for three
    (the vendor's own ``configs/vla/robotwin/robotwin.yaml`` uses the latter).

    A competition parameter, **not** a package constant: hard-coding either
    shape means republishing this package to change the camera count. Compared
    against the checkpoint's own I/O contract by the caller's CPU pre-check
    (:attr:`FormatIssueCode.IO_CONTRACT_MISMATCH`); the file list cannot show it.
    """

    joint_field_names: tuple[str, ...]
    """Joint / gripper field names this competition publishes. Same reasoning as
    :attr:`camera_names`: the arm decides how many there are, the competition
    decides what they are called, and both are its public contract rather than
    this package's."""

    cli_config_file: str | None = None
    """Name of the export descriptor the competition requires alongside the
    weights (the base-model PRD §5 rule 1 calls it ``lingbotvla_cli.yaml``), or
    ``None`` when this competition does not require one.

    🔴 **Defaults to ``None`` because the vendor's own artifacts disagree.**
    ``robbyant/lingbot-vla-v2-6b-robotwin``, the post-trained artifact, does
    carry ``lingbotvla_cli.yaml`` at its repo root; the base model
    ``robbyant/lingbot-vla-v2-6b``, ``robbyant/lingbot-vla-4b`` and the training
    repo have no file by that name, and the export path that writes the shards
    (``lingbotvla.models.save_model_weights``) does not emit one — so a miner who
    exports weights and uploads them has no reason to possess it. Requiring it by
    default would reject those miners after they burned — the 2026-08-14 shape
    again. A competition that has confirmed its own toolchain writes the file
    sets this field and gets :attr:`FormatIssueCode.MISSING_CLI_CONFIG`; until
    then the rule stays off.

    ⚠️ Turning it on has a second-order cost worth knowing before flipping it:
    in the post-trained artifact the descriptor sits at the repo root while the
    weights sit three levels below it, so the fix for
    :attr:`FormatIssueCode.NESTED_TOO_DEEP` — upload only the checkpoint
    subdirectory — leaves the descriptor behind. Requiring both at once tells
    the miner to do two contradictory things.
    """


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


def _scan_files(
    files: Iterable[CheckpointFile],
    allowed_path_segments: frozenset[str],
) -> tuple[list[FormatIssue], int, list[list[str]]]:
    """The part of the per-file scan that does not depend on the base model:
    reject repo-internal state, skip other dotfiles, count the size, reject
    unfinished uploads. Returns the issues, the counted size, and the split
    paths of the files that survived.

    ⚠️ This is a **second** implementation of the loop inside
    :func:`check_checkpoint_layout`, not a refactor of it. That function decides
    whether TAO a miner has already burned counts, it has been published since
    0.6.0, and both consumers call it; the cheapest way to prove it still judges
    round 1 exactly as it did is for its diff to be empty. The duplication is
    bought back with
    ``tests/test_model_format.py::test_both_checkers_scan_shared_rules_alike``,
    which runs both functions over the real repo trees and asserts the shared
    verdicts and byte counts are identical — if the two copies ever drift, that
    test goes red instead of a miner going missing.
    """
    issues: list[FormatIssue] = []
    counted_size = 0
    kept: list[list[str]] = []

    for file in files:
        parts = file.path.split("/")

        if any(
            p in REJECTED_PATH_SEGMENTS and p not in allowed_path_segments
            for p in parts
        ):
            issues.append(
                FormatIssue(
                    FormatIssueCode.LEFTOVER_UPLOAD_STATE,
                    f"leftover upload state in the repo: {file.path} — remove the "
                    "repository-internal directory and re-upload",
                )
            )
            continue

        if any(p.startswith(".") for p in parts):
            continue

        counted_size += file.size_bytes

        if _suffix(parts[-1]) in INCOMPLETE_FILE_SUFFIXES:
            issues.append(
                FormatIssue(
                    FormatIssueCode.INCOMPLETE_FILE,
                    f"incomplete or temporary file: {file.path} — "
                    "the upload did not finish",
                )
            )
            continue

        kept.append(parts)

    return issues, counted_size, kept


def check_lingbot_layout(
    files: Iterable[CheckpointFile],
    layout: LingbotLayout,
    *,
    weight_map: Mapping[str, str] | None = None,
    allowed_path_segments: frozenset[str] = frozenset(),
) -> FormatReport:
    """Judge whether a file list can be submitted as a LingBot-VLA 2.0
    checkpoint.

    Parallel to :func:`check_checkpoint_layout`, which keeps judging openpi
    submissions exactly as it did in 0.6.0. Nothing here sniffs which base model
    the repo holds: ``layout`` says which competition's rules to apply, and the
    caller already knows that because the competition row says so.

    ``weight_map`` is the ``weight_map`` object out of
    :data:`LINGBOT_WEIGHTS_INDEX_FILE` (``{tensor name: shard file name}``),
    which the caller has already parsed. It is passed as a mapping rather than as
    a path so that "this package performs no I/O" stays true at the type level
    rather than by everyone remembering. ``None`` means the caller did not read
    the index, so the shard and tensor rules are **not evaluated** — absence of
    evidence, not evidence of a missing shard.

    ``allowed_path_segments`` behaves as in :func:`check_checkpoint_layout`.

    Only structure is judged, never content: a file that is present but empty
    passes here and is caught when the evaluator loads it. Passing means "worth
    spending GPU time on", not "will load".
    """
    errors, counted_size, kept = _scan_files(files, allowed_path_segments)
    warnings: list[FormatIssue] = []

    has_cli_config = has_model_config = has_index = has_adapter = False
    shard_names: set[str] = set()
    weights_depths: list[int] = []

    for parts in kept:
        path = "/".join(parts)
        basename = parts[-1]

        if layout.cli_config_file and _matches(path, layout.cli_config_file):
            has_cli_config = True
        if _matches(path, layout.model_config_file):
            has_model_config = True
        if _matches(path, layout.weights_index_file):
            has_index = True
        if basename.endswith(".safetensors"):
            shard_names.add(basename)
            weights_depths.append(len(parts) - 1)
        if basename in LORA_ADAPTER_MARKERS:
            has_adapter = True

    # A bare adapter also ships `adapter_model.safetensors`, so "are there
    # weights" has to mean "are there weights that are not the adapter".
    #
    # The tensor inventory has to be visible too, either as the index file in
    # the repo or as one the caller already parsed. That is what tells a LingBot
    # checkpoint apart from an openpi PyTorch one: in a file list, openpi is a
    # bare `model.safetensors` next to a `config.json` and nothing else
    # distinguishes the two. See LINGBOT_WEIGHTS_INDEX_FILE for why requiring it
    # is safe for the 2.0 base model, and for the case that would make it unsafe.
    real_weights = shard_names - LORA_ADAPTER_MARKERS
    has_weights = bool(real_weights) and (has_index or weight_map is not None)

    if layout.cli_config_file and not has_cli_config:
        errors.append(
            FormatIssue(
                FormatIssueCode.MISSING_CLI_CONFIG,
                f"missing the export descriptor '{layout.cli_config_file}' — "
                "upload the file the official export script writes next to the "
                "weights",
            )
        )

    if not has_model_config:
        errors.append(
            FormatIssue(
                FormatIssueCode.MISSING_MODEL_CONFIG,
                f"missing '{layout.model_config_file}' — upload the whole "
                "checkpoint directory, not only the weight files",
            )
        )

    if not has_weights:
        if has_adapter and not real_weights:
            errors.append(
                FormatIssue(
                    FormatIssueCode.BARE_LORA_ADAPTER,
                    "this is a bare LoRA adapter, not a checkpoint — the "
                    "evaluator does no merging. Merge the adapter back into the "
                    "LingBot-VLA base and upload the full checkpoint "
                    f"('*.safetensors' plus '{layout.weights_index_file}').",
                )
            )
        else:
            errors.append(
                FormatIssue(
                    FormatIssueCode.MISSING_WEIGHTS,
                    "no LingBot-VLA weights found — expected safetensors shards "
                    f"together with the '{layout.weights_index_file}' that lists "
                    "them",
                )
            )

    if weight_map is not None and has_weights:
        for shard in sorted(set(weight_map.values()) - shard_names):
            errors.append(
                FormatIssue(
                    FormatIssueCode.MISSING_WEIGHT_SHARD,
                    f"'{layout.weights_index_file}' refers to shard '{shard}', "
                    "which is not in the repo — re-upload the missing shard",
                )
            )
        for prefix in sorted(
            p
            for p in LINGBOT_REQUIRED_TENSOR_PREFIXES
            if not any(name.startswith(p) for name in weight_map)
        ):
            errors.append(
                FormatIssue(
                    FormatIssueCode.MISSING_REQUIRED_TENSOR,
                    f"no tensor named '{prefix}...' in the weight index — this "
                    "is not a full LingBot-VLA checkpoint. Export the complete "
                    "weights without pruning tensors",
                )
            )

    # Reported only when nothing else is wrong, matching check_checkpoint_layout:
    # a repo with files missing is small anyway, and saying both is noise.
    if counted_size < MIN_TOTAL_SIZE_BYTES and not errors:
        errors.append(
            FormatIssue(
                FormatIssueCode.TOTAL_SIZE_TOO_SMALL,
                f"total size {counted_size / 1024 / 1024:.1f} MB is below the "
                f"{MIN_TOTAL_SIZE_BYTES // 1024 // 1024} MB minimum — the weights are "
                "probably git-lfs pointers, not the real files",
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

    # Sharded safetensors is the `pytorch` weight form; which base model it holds
    # is expressed by the layout the caller chose, not by a third enum member.
    return FormatReport(
        kind=CheckpointKind.PYTORCH if has_weights else None,
        errors=tuple(errors),
        warnings=tuple(warnings),
        counted_size_bytes=counted_size,
    )
