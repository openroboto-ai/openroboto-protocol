"""可提交的 checkpoint 必须长什么样 —— 布局层的准入契约。

契约含义
--------
矿工照它导出，后端照它准入，评测器照它拒绝。三方看同一份规则，矿工才能在
**烧 TAO 之前**知道自己会不会被拒。现在这条规则有两份实现（后端
``hf_validate.py`` 判 HF 仓库树，评测器 ``libero_eval/check_model.py`` 判本地目录），
矿工要 clone 第二个仓才能自查 —— 烧完才发现格式错，TAO 白扔。

输入是一份**文件清单**（路径 + 字节数），清单从哪来这里不管：后端从 HF tree API
拿，矿工从本地目录 ``os.walk`` 拿，评测器从下载完的目录拿。同一份清单，三方得到
同一个结论。

不负责（这些都要读文件内容，留在评测器里）
------------------------------------------
- safetensors 头、orbax ``params/_METADATA``、参数量区间、norm_stats 里的数值；
- 架构是不是 π0.5（``time_mlp_*`` vs ``state_proj``）；
- 下载、解析 HF API 响应、判断 revision 存不存在。

**布局过了不等于评测一定能加载。** 这里过了只说明「值得花 GPU 时间试一试」。

errors 与 warnings 的分工
-------------------------
``errors`` 复刻**生产准入**的判定（``hf_validate.validate_file_list``），
一条不多一条不少 —— 它决定一笔已经烧掉的 TAO 算不算数，不是能顺手收紧的地方。
``warnings`` 是已知的「准入放行、但评测器加载不了」的分歧，不改变判定，
只是把矿工会踩的坑提前说出来。分歧本身待裁决（见包内 openQuestions）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class CheckpointKind(StrEnum):
    """checkpoint 的两种权重形态。字面量和评测器的 ``checkpoint_type`` 一致。

    两种同时存在时 openpi 加载 PyTorch 权重、忽略 ``params/``，
    所以 :func:`check_checkpoint_layout` 也按这个优先级报。
    """

    PYTORCH = "pytorch"
    JAX = "jax"


class FormatIssueCode(StrEnum):
    """拒绝 / 提醒的稳定机器码。

    ``message`` 面向人（原样回传矿工，会变），``code`` 面向程序（矿工侧脚本、
    前端文案、告警规则都可以匹配它，**不许改**）。加新码是 minor，改旧码是 major。
    """

    MISSING_WEIGHTS = "missing_weights"
    """既没有 ``model.safetensors`` 也没有 ``params/`` —— 不是一个 checkpoint。"""

    BARE_LORA_ADAPTER = "bare_lora_adapter"
    """只有 LoRA adapter，没有合并后的完整权重。评测器不做合并。"""

    MISSING_NORM_STATS = "missing_norm_stats"
    """缺归一化统计量，推理时无法归一化输入 / 反归一化动作。"""

    LEFTOVER_UPLOAD_STATE = "leftover_upload_state"
    """``.git`` / ``.cache`` 这类误传上来的仓库内部状态。"""

    INCOMPLETE_FILE = "incomplete_file"
    """``.tmp`` / ``.partial`` 之类没传完的文件。"""

    TOTAL_SIZE_TOO_SMALL = "total_size_too_small"
    """整个仓库小于 10 MB —— 多半只传了 LFS 指针，没传权重。"""

    UNLOADABLE_WEIGHTS_FORMAT = "unloadable_weights_format"
    """（warning）准入认的权重文件名，评测器加载不了。"""

    NON_CANONICAL_NORM_STATS = "non_canonical_norm_stats"
    """（warning）norm_stats 在准入认的备用位置，评测器只读规范位置。"""

    NESTED_TOO_DEEP = "nested_too_deep"
    """（warning）checkpoint 嵌得比评测器搜索的层数还深。"""


@dataclass(frozen=True)
class OpenpiLayout:
    """openpi checkpoint 的布局。这几个字段必须同源，所以绑在一起。

    ``norm_stats_relpath`` 是**推导出来的**而不是另写一个常量 —— 评测器就是
    ``assets / asset_id / "norm_stats.json"`` 这么拼的，写成两个独立常量迟早会漂。
    """

    asset_id: str
    """norm stats 挂在 ``assets/<asset_id>/`` 下。LIBERO 是上游 openpi 的资产 id。"""

    pytorch_weights_file: str
    """openpi PyTorch checkpoint 的权重文件名。"""

    jax_params_dir: str
    """openpi JAX checkpoint 的 orbax OCDBT 目录名。"""

    @property
    def norm_stats_relpath(self) -> str:
        """归一化统计量相对 checkpoint 根的路径。"""
        return f"assets/{self.asset_id}/norm_stats.json"


LIBERO_LAYOUT: Final = OpenpiLayout(
    asset_id="physical-intelligence/libero",
    pytorch_weights_file="model.safetensors",
    jax_params_dir="params",
)
"""子网当前唯一接受的布局：openpi + LIBERO 资产。"""

LEGACY_PYTORCH_WEIGHTS_FILE: Final = "pytorch_model.bin"
"""生产准入历史上认这个名字。评测器**加载不了**它 —— 放行但出 warning。"""

LEGACY_NORM_STATS_RELPATHS: Final = ("assets/libero/norm_stats.json", "norm_stats.json")
"""生产准入历史上认的备用 norm_stats 位置。评测器只读规范位置 —— 放行但出 warning。"""

LORA_ADAPTER_MARKERS: Final = frozenset(
    {"adapter_config.json", "adapter_model.safetensors", "adapter_model.bin"}
)
"""裸 LoRA adapter 的特征文件名。识别它只为了给一条**说得清**的拒绝理由。"""

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
"""误传上来的仓库内部状态，明确列举。

⚠️ **不能改成「以 . 开头就拒」。** HF 建仓库时自动生成 ``.gitattributes``，
2026-08-14 按前缀拒的规则接上生产，误拒了 uid 221 / 231 两个**已经烧过 TAO**
的提交；当时抽查榜上 8 个已通过评测的仓库，8/8 都有这个文件 —— 规则一旦生效
等于拒绝所有人。加名字要有具体理由。
"""

INCOMPLETE_FILE_SUFFIXES: Final = frozenset(
    {".tmp", ".temp", ".partial", ".crdownload", ".download", ".lock", ".swp", ".swo"}
)
"""没传完 / 临时文件的后缀。"""

MIN_TOTAL_SIZE_BYTES: Final = 10 * 1024 * 1024
"""整仓最小体积。低于这个数基本只有 LFS 指针，没有真权重。"""

MAX_CHECKPOINT_NESTING_DEPTH: Final = 2
"""评测器只在根、``*/``、``*/*/`` 三层里找 checkpoint。埋得更深它找不到。"""


@dataclass(frozen=True)
class CheckpointFile:
    """清单里的一个文件。路径和体积必须同源 —— 判定同时用到这两个。

    ``path`` 是相对 checkpoint 根（或仓库根）的 POSIX 路径，不以 ``/`` 开头。
    只传文件；目录条目传进来无害（体积 0、匹配不上任何规则），但不要指望它被判定。
    """

    path: str
    size_bytes: int


@dataclass(frozen=True)
class FormatIssue:
    """一条判定结果。``message`` 是原样回传矿工的英文原因。"""

    code: FormatIssueCode
    message: str


@dataclass(frozen=True)
class FormatReport:
    """一次布局判定的完整结论。"""

    kind: CheckpointKind | None
    """识别出的权重形态；``None`` = 没识别出可加载的 checkpoint。"""

    errors: tuple[FormatIssue, ...]
    """非空 = 拒绝提交。顺序：逐文件问题 → 缺权重 → 缺 norm_stats → 体积过小。"""

    warnings: tuple[FormatIssue, ...]
    """不影响判定，但矿工大概率会在评测阶段踩到。"""

    counted_size_bytes: int
    """参与体积判定的字节数。dotfile 与被拒文件不计入。

    别在调用方重新求和 —— 求和口径不一样，「体积过小」这条判定就会对不上。
    """

    @property
    def ok(self) -> bool:
        """能不能提交。"""
        return not self.errors


def _suffix(basename: str) -> str:
    """取**最后**一个点之后的后缀（含点）；没有则空串。

    取第一个点时 ``checkpoint.001.tmp`` 的后缀会算成 ``.001.tmp``，匹配不上
    :data:`INCOMPLETE_FILE_SUFFIXES` —— 任何多点文件名都能绕过临时文件检查，
    而拦残留上传正是这条检查的全部目的。
    """
    idx = basename.rfind(".")
    return basename[idx:] if idx > 0 else ""


def _matches(path: str, relpath: str) -> bool:
    """路径是不是 ``relpath`` 本身，或嵌在任意层子目录下的 ``relpath``。

    嵌套必须认：uid 130 把整个 openpi checkpoint 放在 ``merged/`` 下面
    （10.8 GB，norm_stats 齐全，合法提交），只认仓库根会把它判成「缺模型文件」。
    """
    return path == relpath or path.endswith("/" + relpath)


def check_checkpoint_layout(
    files: Iterable[CheckpointFile],
    *,
    allowed_path_segments: frozenset[str] = frozenset(),
) -> FormatReport:
    """判定一份文件清单能不能作为 checkpoint 提交。

    ``allowed_path_segments`` 覆盖 :data:`REJECTED_PATH_SEGMENTS`
    （对应生产配置 ``scanner.hf_allow_dotfiles``）。正常不需要设 ——
    默认规则已经只拒明确有问题的名字；它是误拒真实矿工时的现场逃生口。
    """
    errors: list[FormatIssue] = []
    warnings: list[FormatIssue] = []
    counted_size = 0

    has_pytorch = has_jax = has_legacy_weights = False
    has_canonical_stats = has_legacy_stats = has_adapter = False
    # 权重可能在多个层级各有一份，评测器取最浅的那份，所以只有最浅的深度算数。
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

        # 其余 dotfile（.gitattributes / .gitignore …）放行，且不计入体积：
        # 它们不是模型内容，HF 自己会生成。
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

        # 目录型标记看路径中间那几段，文件型标记看整条路径。
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

    # 只在没有别的问题时才报体积。缺文件的仓库本来就小，两条一起报是噪音。
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
