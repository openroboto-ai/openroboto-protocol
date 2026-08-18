"""任务状态与阶段词表 —— 子网里唯一的一份。

**为什么它必须只有一份**：2026-08-14 事故里同一件事有四套写法 —— worker 内部叫
`evaluating`、公开文档叫 `running`、后端只认 `status` 字段、前端类型叫 `stage`。
结果是按文档写 worker 的人收到 `400 Unknown status`，队列页的进度条整个消失。
当时的"修复"不是统一词表，而是在 worker 里加了一张手写翻译表
（`benchmark_worker/backend_client.py` 的 `_PROGRESS_STAGE_MAP`）。这个模块是那张
翻译表的正解：词表只有一份，装同一个版本号的包就不可能对不上。

**两套词表不是一回事，混用是事故本身**：

- **status**（`submissions.eval_status` 这一列）：提交走到哪一步。生命周期状态。
- **stage**（`submissions.stage` 这一列）：worker 认领任务后在干什么。进度明细，
  只在 status 为 `evaluating` 期间有意义。

`evaluating` 是 **status**；`running` 是 **stage**。它们描述的是同一段时间，
但不是同一个字段，任何一边写成另一边的词都会被对方判为非法。

**线上事实**（2026-08-17 curl `GET /api/v1/submissions/history?limit=500`，117 条）：

- `eval_status` 出现过：`evaluated` 65 / `superseded` 32 / `eval_failed` 13 /
  `rejected` 7；`GET /api/v1/queue/status` 的 summary 另有 `pending` / `evaluating`。
- `stage` 出现过：`running` 61 / `""` 47 / `downloading` 8 / `prechecking` 1。
  **没有出现过 `evaluating`** —— 对外的规范阶段词是 `running`，这条是裁决依据。

**这个模块不负责**：状态怎么持久化、谁有权改状态、超时怎么判。那些是后端的事。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

# ─────────────────────────────────────────────────────────────────────────────
# 提交状态（submissions.eval_status）
# ─────────────────────────────────────────────────────────────────────────────

# 扫链刚看到这条 commitment，还没做任何校验。建行时的默认值（schema.py 的 DEFAULT）。
STATUS_RECEIVED: Final[str] = "received"

# 正在核对链上 burn 交易。⚠️ 瞬时值：一个扫链周期内就会离开，不该有行长期停在这里。
STATUS_BURN_CHECKING: Final[str] = "burn_checking"

# burn 校验通过，等待派种子。同样是瞬时值。
STATUS_BURN_PASSED: Final[str] = "burn_passed"

# 已入评测队列，等 worker 来领。
STATUS_PENDING: Final[str] = "pending"

# 派种子失败（drand 拿不到）。**可重试**，不是终态 —— 扫链每轮结束会重试一次，
# 成功则回到 pending。drand 不可用时宁可卡住也不能退化成只用 block_hash 派种子，
# 否则历史评测不可复现（spec §5）。
STATUS_SEED_FAILED: Final[str] = "seed_failed"

# worker 已认领并在跑。此期间 stage 字段才有意义。
STATUS_EVALUATING: Final[str] = "evaluating"

# 出分成功，分数进 eval_scores，参与排名。
STATUS_EVALUATED: Final[str] = "evaluated"

# 评测失败（worker 报错 / 模型跑不起来）。终态，不重试。
STATUS_EVAL_FAILED: Final[str] = "eval_failed"

# 被拒（burn 不合格 / HF 仓结构不合格 / 重复提交 / 轮次不匹配）。
STATUS_REJECTED: Final[str] = "rejected"

# 同一 (hotkey, round) 有了新版本，这条被顶掉。
# ⚠️ 老 `protocol/status.py` 的 ALL_STATUSES **漏了它**，`is_terminal()` 对它返回
# False —— 当时零消费方所以没出事，这里补上（incident-20260814-context.md 收尾项 7）。
STATUS_SUPERSEDED: Final[str] = "superseded"


# 终态：不再往前走。
# 注意与 FROZEN_STATUSES 的区别 —— 终态说的是"流程结束"，冻结说的是"DB 拒绝任何写入"。
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {STATUS_EVALUATED, STATUS_EVAL_FAILED, STATUS_REJECTED, STATUS_SUPERSEDED}
)

# 冻结态：**任何**迟到的写入都必须被拒绝，连同值重写也不行。
#
# 这是 2026-08-14 事故 ⑤ 的守卫，代价用 GPU 小时计：worker 本地队列里压着一条已被
# superseded 的旧任务，跑完出分把该行复活成 evaluated → 它重新落入
# `idx_sub_hotkey_round_commit` 的谓词（该索引排除 rejected/superseded）→ 撞唯一约束
# → 出分接口 500，worker 几小时 GPU 白跑；就算不撞，一个被顶掉的版本也重新参与了排名。
#
# 生产落地形态是两处 SQL 谓词 `eval_status NOT IN ('superseded', 'rejected')`
# （prototype/backend/database.py:898 与 :1099）以及出分接口的整条丢弃
# （api/handlers/benchmark.py:182，返回 200 而非错误码，避免 worker 无限重试）。
FROZEN_STATUSES: Final[frozenset[str]] = frozenset({STATUS_REJECTED, STATUS_SUPERSEDED})


# 合法状态转移。**写成数据，不是散在各处的 if** —— 状态机本身要能被测。
#
# 每条边都有生产代码出处；没有出处的边不写进来（宁可少，别猜）：
#   received      → burn_checking            verify_submission.py:560
#   burn_checking → burn_passed              verify_submission.py:569
#   burn_passed   → pending                  database.py enqueue_eval
#   burn_passed   → seed_failed              verify_submission.py:627（drand 不可用）
#   seed_failed   → pending                  scanner_loop.py:_retry_seed_failed
#   pending       → evaluating               database.py:update_task_progress
#                                            （CASE WHEN eval_status='pending'，单向）
#   pending       → evaluated / eval_failed  历史真实路径：修复前 update_task_progress
#                                            只写 stage 不推状态，全库 0 条 evaluating，
#                                            65 条 evaluated 全部是从 pending 直接落的。
#                                            **删掉这条边等于宣布线上历史非法。**
#   pending       → superseded               submission_db.py:supersede_pending —— 它的
#                                            WHERE 只有一条 eval_status='pending'
#   evaluating    → evaluated / eval_failed  database.py:update_submission_status
#   * → rejected                             扫链侧任何一步都可能判拒
#                                            （verify_submission.py 有 8 处）
_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    STATUS_RECEIVED: frozenset({STATUS_BURN_CHECKING, STATUS_REJECTED}),
    STATUS_BURN_CHECKING: frozenset({STATUS_BURN_PASSED, STATUS_REJECTED}),
    STATUS_BURN_PASSED: frozenset(
        {STATUS_PENDING, STATUS_SEED_FAILED, STATUS_REJECTED}
    ),
    STATUS_SEED_FAILED: frozenset({STATUS_PENDING, STATUS_REJECTED}),
    STATUS_PENDING: frozenset(
        {
            STATUS_EVALUATING,
            STATUS_EVALUATED,
            STATUS_EVAL_FAILED,
            STATUS_REJECTED,
            STATUS_SUPERSEDED,
        }
    ),
    STATUS_EVALUATING: frozenset(
        {STATUS_EVALUATED, STATUS_EVAL_FAILED, STATUS_REJECTED}
    ),
    # 终态没有出边。spec 不变量 7：单向，不许回退。
    STATUS_EVALUATED: frozenset(),
    STATUS_EVAL_FAILED: frozenset(),
    STATUS_REJECTED: frozenset(),
    STATUS_SUPERSEDED: frozenset(),
}

#: 状态机本体：`{当前状态: 允许迁往的状态集合}`。只读，消费方不可改。
STATUS_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(_TRANSITIONS)

#: 全部合法状态。**与转移表同源**（就是它的键集），不可能出现"表里有、词表没有"。
ALL_STATUSES: Final[frozenset[str]] = frozenset(STATUS_TRANSITIONS)


def is_terminal(status: str) -> bool:
    """这个状态是不是终态（流程结束，不会再往前走）。

    ⚠️ 与老 `backend/protocol/status.py` 的同名函数有一处差异：那里 `superseded`
    返回 False（词表本身就漏了它）。老函数当时零消费方，这里按事实修正。
    """
    return status in TERMINAL_STATUSES


def can_transition(current: str, new: str) -> bool:
    """`current → new` 这次状态变更合不合法。未知状态一律 False（fail-closed）。

    `current == new` 的同值重写按合法处理 —— worker 的出分 POST 超时后会重投同一份
    分数（`backend_client.py` 先 `fetch_submission` 核对再决定是否重试），
    落库就是一次 `evaluated → evaluated`。但冻结态除外：对 rejected / superseded
    连同值重写都必须挡住，生产的 SQL 谓词就是这么写的。
    """
    if current not in STATUS_TRANSITIONS or new not in STATUS_TRANSITIONS:
        return False
    if current == new:
        return current not in FROZEN_STATUSES
    return new in STATUS_TRANSITIONS[current]


# ─────────────────────────────────────────────────────────────────────────────
# 评测阶段（submissions.stage）
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Stage:
    """一个评测阶段在三处的写法，绑成一条记录。

    分开写就是 ZCY-158：三个名字散在三个仓库里，改一处漏两处，错配不报错、数据悄悄错。
    绑在一起之后，"对外说 running、库里存 benchmark_running、worker 叫 evaluating"
    这三件事要么一起对要么一起错，不可能只错一半。
    """

    #: 对外的规范词。worker 该发这个，API 该返回这个，前端该按这个渲染。
    #: 生产 `GET /api/v1/submissions/history` 返回的就是这一列（2026-08-17 实测）。
    wire: str
    #: 库里的历史存储值（带 `benchmark_` 前缀）。出口翻译前的样子，读老数据时会遇到；
    #: **不要**把它吐给前端 —— 前端的 ACTIVE_STAGES 里没有带前缀的写法，认不出就不渲染。
    stored: str
    #: 输入侧还接受的同义词。收下它们纯粹是加法：按公开文档或前端类型写的调用方
    #: 曾经因此收到 400（2026-08-14 实际发生）。
    aliases: tuple[str, ...] = ()


STAGE_DOWNLOADING: Final[str] = "downloading"
STAGE_PRECHECKING: Final[str] = "prechecking"
STAGE_RUNNING: Final[str] = "running"

#: 三个阶段的完整词表。顺序即 worker 的实际执行顺序。
STAGES: Final[tuple[Stage, ...]] = (
    Stage(wire=STAGE_DOWNLOADING, stored="benchmark_downloading"),
    # `precheck` 是前端类型里的写法（web/src/api/types.ts 的 QueueProgressStage）。
    Stage(
        wire=STAGE_PRECHECKING,
        stored="benchmark_prechecking",
        aliases=("precheck",),
    ),
    # `evaluating` 是 worker 内部的写法（run_eval.py 往进度文件里写的就是它），
    # 也是老后端唯一认的输入词。**对外规范词是 running**：线上 117 条记录里
    # stage 出现过 running / downloading / prechecking，从没出现过 evaluating。
    # worker 侧的 `_PROGRESS_STAGE_MAP` 就是在做这一步翻译，接了这个包之后可以删掉。
    Stage(wire=STAGE_RUNNING, stored="benchmark_running", aliases=("evaluating",)),
)

#: 全部合法阶段词（对外规范形态）。与 STAGES 同源。
ALL_STAGES: Final[frozenset[str]] = frozenset(s.wire for s in STAGES)

_STAGE_LOOKUP: Final[dict[str, str]] = {
    word: stage.wire
    for stage in STAGES
    for word in (stage.wire, stage.stored, *stage.aliases)
}


def normalize_stage(word: str) -> str | None:
    """任何一方的阶段写法 → 对外规范词。不认识返回 None，由调用方决定怎么拒。

    大小写与首尾空白按生产入口的做法先归一（`.strip().lower()`）。
    """
    return _STAGE_LOOKUP.get(word.strip().lower())


# ─────────────────────────────────────────────────────────────────────────────
# 历史遗留状态词
# ─────────────────────────────────────────────────────────────────────────────

#: 老状态词 → 现行状态词。
#:
#: 生产库里这些值还在：`submissions` 有一列迁移前的遗留 `status`，与 `eval_status`
#: 有 52 行不一致（`status='done'` 而 `eval_status='superseded'` 有 22 行）。
#: 前端读的是 `submission.status || submission.eval_status`，先读到的是错的那个 ——
#: 2026-08-14 队列页 95 条里 33 条状态显示错误，根因就是这两套词混在同一个响应里。
#:
#: **真相只有 eval_status 一个。** 这张表只用于读老数据，不要用它生成新值。
LEGACY_STATUS_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "enqueued": STATUS_PENDING,
        "waiting": STATUS_EVALUATING,
        "benchmark_downloading": STATUS_EVALUATING,
        "benchmark_prechecking": STATUS_EVALUATING,
        "benchmark_running": STATUS_EVALUATING,
        "benchmark_done": STATUS_EVALUATED,
        "benchmark_failed": STATUS_EVAL_FAILED,
        "done": STATUS_EVALUATED,
        "failed": STATUS_EVAL_FAILED,
    }
)


def normalize_status(status: str) -> str:
    """老状态词 → 现行状态词。不在表里的原样返回（与老实现行为一致）。

    原样返回是故意的：调用方拿到的可能是一个未知的新词，这里不该替它判非法 ——
    合法性由 `ALL_STATUSES` 判。
    """
    return LEGACY_STATUS_ALIASES.get(status, status)
