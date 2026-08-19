"""每个 API 端点的请求 / 响应模型 —— 字段契约的唯一一份。

**为什么它必须存在**：「字段契约靠口头约定」是这个项目最贵的历史问题。
三个已经发生的后果：

1. ZCY-158 —— 进度上报的阶段词表四方不一致，按公开文档写的 worker 收 400。
   当时的"修复"是在 worker 里加一张手写翻译表（`benchmark_worker/backend_client.py`
   的 `_PROGRESS_STAGE_MAP`），**至今还在**。
2. 事故 ⑧ —— 进度上报的 `detail` 对象被入口手挑两个顶层键，其余字段全丢，
   队列页进度条整个消失，后端从此答不上「任务跑到哪一步」。
3. 前端 `web` 最后一次提交叫 *Tolerate rebuilt-backend field renames* ——
   消费方在为我们的改名写兼容层。

装同一个版本号之后，**禁止手挑字段**：响应就是这里声明的形状，多一个少一个都要改这个文件。

## 这个模块承诺什么

- 每个端点的**字段名、嵌套层级、可选性**。逐字段的出处（线上实测 / 生产代码行号）
  写在注释里，注释即裁决依据。
- 三处「不可表达」的守卫：空 `env_list`、榜位词混进生命周期词、progress 响应的
  `status`/`stage` 不一致。这些是生产真实咬过人的形状。
- 两侧共用的**归一化**：progress 的入口词表与 `detail` 提取
  （`ProgressUpdate.from_payload`）。
  worker 侧的 `_PROGRESS_STAGE_MAP` 接了这个包之后可以删掉。

## 这个模块不负责

HTTP 状态码、鉴权 scope、SQL、分页实现。校验失败抛 `ContractError` 并带一个
**稳定 code**，由后端决定映射到哪个状态码 —— 因为 4xx 对 worker 是「销毁一次
8 小时的评测结果」的按钮，5xx 是「重复写库」的按钮，哪个分支落哪个码只能由后端
按契约卡钉死（spec 07 §0.3 的错误分类表）。

## 为什么是 pydantic，而且只在这一个模块

主包 `dependencies` 是空的，pydantic 走 `[schemas]` optional-dependency
（见 `pyproject.toml` 里那段注释）。矿工装这个包只为推导 seed，不该被迫在 GPU
机器上编译一个 pydantic-core 轮子；`__init__.py` 不 re-export 任何东西，所以
`import openroboto_protocol` 永远不会触发 `import pydantic`。

选 pydantic 而不是 stdlib dataclass 的**唯一硬理由**（2026-08-17 用 pydantic
2.13.4 实测）：dataclass 版本的校验只能写在 `__post_init__` 里，而框架在那之前
已经把 JSON 值转过一轮了 —— lax 模式下 `{"score": true}` 会先变成 `1.0`，
于是「bool 不算数字」这条（生产 `_validate_env_scores` 的第 1 道检查）
在自动解析路径上**拦不住**。历史代价是 `{"score": 99.0, "samples": -5}` + 只交
1 个 suite → 200 → 直接夺擂拿 7% 排放。
用真模型可以把 `StrictFloat` / `StrictInt` 标上去，让这件事在**类型层**不可表达。
实测同一批值：`StrictFloat` 拒 `true` 和 `"1.0"`，但仍接受 JSON 整数 `1`
（worker 发 `total_score: 0` 不会被误伤）。

## 严格性只加在写路径，读路径一律不加值域约束

这不是偷懒，是权衡过的：

- **写路径**（`EnvScore` 的 `score` / `samples`）用 `Strict*`。这里的数字最快
  40 分钟后就是链上真实排放，宁可 4xx 拒收。
- **读路径**（把库里的历史行装回模型）**不加 `ge` / `le` / `allow_inf_nan=False`**。
  生产库里**真的存在** `score=99.0` 这种行（2026-08-14 那次就是这么进去的），
  给读模型加值域约束等于让「读一条历史脏数据」变成 500 ——
  而 5xx 对 worker 是「重复 POST」的按钮（spec 07 §0.3）。
  值域检查留在 `check_env_scores()`：它收**原始 JSON**、在写库之前跑、
  带稳定 code `INVALID_SCORE`。一处守卫，守在边界上。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import (
    Annotated,
    Any,
    Final,
    Generic,
    Literal,
    Protocol,
    TypeVar,
    get_args,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    ValidationError,
    computed_field,
    model_validator,
)

from .constants import REQUIRED_ENVS
from .status import normalize_stage

# ─────────────────────────────────────────────────────────────────────────────
# 契约违例
# ─────────────────────────────────────────────────────────────────────────────


class ContractError(ValueError):
    """请求不满足契约。`code` 是**稳定机器码**，措辞可以改，码不能改。

    刻意不带 HTTP 状态码：同一条违例在不同端点上的正确状态码不同，
    而选错的代价是矿工的 GPU 工时（4xx → worker `abandoned`，丢弃整份评测结果）。
    映射表在后端的契约卡里，不在这里。

    继承 `ValueError` 是为了能直接在 pydantic 校验器里抛 —— pydantic 会把它包成
    `ValidationError`，原对象仍可从 `err.errors()[0]["ctx"]["error"]` 取回。
    但**首选调用方式是显式调 `check_*()` / `from_payload()`**：那条路径上
    `.code` 不会被包掉，后端才能把它映射成契约卡里钉死的那个状态码。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        #: 稳定错误码，SCREAMING_SNAKE_CASE，只增不改。
        self.code = code


#: `env_scores` 里有非法分数（类型 / NaN / 越界 / samples 非法）。
CODE_INVALID_SCORE: Final[str] = "INVALID_SCORE"
#: `success=true` 但 6 个必需 suite 没交齐。
CODE_MISSING_ENVS: Final[str] = "MISSING_ENVS"
#: 进度上报的阶段词不在受控词表里。
CODE_INVALID_STAGE: Final[str] = "INVALID_STAGE"


class Contract(BaseModel):
    """本模块所有模型的基类。

    `frozen=True`：响应模型建好之后不该再被改。可变的响应对象意味着「谁改了这个
    字段」要靠通读调用链回答 —— 而这个仓库的历史问题恰恰是出口处被人手改字段
    （`row.pop("status")` 那一类）。

    `extra` 保持 pydantic 默认的 **ignore**，**不许改成 forbid**：
    spec 07 明确要求「payload 带一个后端不认识的新字段 → 200，忽略即可」。
    对 worker 来说 4xx = abandon = 销毁一次 8 小时的评测结果，
    「评测方加了个字段」不该是那个按钮。
    """

    model_config = ConfigDict(frozen=True)


# ─────────────────────────────────────────────────────────────────────────────
# 响应信封（ADR 02，推翻「不套响应信封」）
# ─────────────────────────────────────────────────────────────────────────────
#
# 形状（`../openroboto-backend/docs/adr/02-统一响应信封.md` 拍板，不在这里重新讨论）：
#
#     单对象  {"data": {...},  "meta": {request_id, generated_at}}
#     列表    {"data": [...],  "meta": {request_id, generated_at, page: {...}}}
#     错误    {"error": {code, message, retryable}, "meta": {request_id, generated_at}}
#
# 四条规则，**三条在类型层就不可表达**，不靠任何一条运行时校验：
#
# 1. 成功一定有 `data`、一定没有 `error`；错误反之 —— 两个信封是两个类，
#    各自只有自己那个字段，`Contract` 的 `extra=ignore` 会把多传的那个丢掉。
# 2. `meta.request_id` 每个响应都有（含错误）—— 必填字段，缺了构造不出来。
# 3. `meta.page` **只有列表端点有** —— `Envelope.meta` 的声明类型是 `Meta`，
#    那个类**根本没有 `page` 字段**；pydantic 按声明类型序列化，就算有人硬塞一个
#    `ListMeta` 进去，单对象响应里也不会多出这个键。
#    ⚠️ 这一条刻意**不用** `page: PageMeta | None = None` + `exclude_none` 实现：
#    那要求每个路由记得写 `response_model_exclude_none=True`，漏一个就多吐
#    `"page": null`。忘一次是静默的，类型层不会。
# 4. `data` 里只放业务字段，`total` / `generated_at` 这类元信息一律进 `meta`。
#    这条**无法在类型层强制**（`T` 是任意模型），由 ADR 02 和 code review 守。
#
# 探针（`/healthz` `/readyz`）和 `/metrics` 是**唯一的例外，不套信封**：
# 它们的消费方是 PM2 / 负载均衡 / Prometheus，套了对方直接解析不了。
# 所以 `LivenessResponse` / `ReadinessResponse` 就是裸模型，
# `tests/test_schemas.py::test_probes_are_never_enveloped` 显式钉住这一点。

T = TypeVar("T")


class Meta(Contract):
    """每个响应都带的元信息。**成功、错误、空列表，一个不落。**

    `request_id` 是必填的：它已经全链路贯穿（后端 `core/logging.py` 的
    contextvar + `X-Request-ID` 响应头），出问题时用户直接把它贴过来就能查日志。
    给它一个默认值等于允许「查不到的那个响应」存在，而那恰恰是最需要查的那个。

    `generated_at` **错误响应上也有**（ADR 02 的例子里省略了它）：一份 meta
    一套解析，调用方不需要为错误分支准备第二个 schema。
    """

    #: 后端 `get_request_id()` 的值，与 `X-Request-ID` 响应头同值。
    request_id: str
    #: 服务器 UTC 时刻。**唯一允许随调用变化的字段**（榜单不变量 4 的载体：
    #: 业务数据逐字段幂等，时间戳搬到这里之后 `data` 才真的能逐字节比对）。
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PageLike(Protocol):
    """`PageMeta.of()` 收的结构类型 —— 后端两个 `Page` 都**已经**满足它。

    - `app/repositories/pagination.py` 的 `Page[T]`（frozen dataclass，
      `items` 是 tuple）—— 全部仓储列表方法的返回值；
    - `app/api/schemas.py` 的 `Page[T]`（pydantic 模型，`items` 是 list）。

    写成结构类型而不是 import 其中一个：协议包零运行时依赖、也不该知道后端的分层。
    成员全声明成只读属性，dataclass 的字段和 pydantic 的字段都能满足。
    """

    @property
    def items(self) -> Sequence[Any]: ...
    @property
    def total(self) -> int: ...
    @property
    def limit(self) -> int: ...
    @property
    def offset(self) -> int: ...


class PageMeta(Contract):
    """分页元信息。**只挂在 `ListEnvelope.meta.page` 上，不进 `data`。**

    线上现在是 `{total, rows}` / `{success, submissions, total, limit, offset}` 等
    五种自定义形状，分页数字和业务字段混在同一层 —— 前端因此对每个端点写一套解析。

    `total` 是**过滤后的总数**，不是本页条数。新骨架写过 `total=len(page)`：
    前端拿它算页数，于是页码永远是 1。
    """

    total: int
    limit: int
    offset: int
    #: 还有下一页。**别让调用方自己拿 `offset + len(data) < total` 去算** ——
    #: 那个表达式在 8 个端点上复制 8 遍，写错一个是静默翻页丢行。
    has_more: bool

    @classmethod
    def of(cls, page: PageLike) -> PageMeta:
        """从一页查询结果算出分页元信息。**`has_more` 的公式只有这一份。**"""
        return cls(
            total=page.total,
            limit=page.limit,
            offset=page.offset,
            has_more=page.offset + len(page.items) < page.total,
        )


class ListMeta(Meta):
    """列表端点的 meta：比 `Meta` 多且只多一个 `page`，且**必填**。

    必填是有意的：少了它前端就算不出页码，而「忘了带分页信息」这件事
    在响应体里看不出来 —— 看起来就像一个短列表。
    """

    page: PageMeta


class Envelope(Contract, Generic[T]):
    """单对象成功响应。`response_model=Envelope[LeaderboardRow]` 直接用。

    `data` 是**单个对象**。列表用 `ListEnvelope` —— 它多一个必填的
    `meta.page`，所以「列表端点忘了给分页信息」在类型层就过不去。
    """

    data: T
    meta: Meta


class ListEnvelope(Contract, Generic[T]):
    """列表成功响应。

    空列表是 `data: []` + `page.total = 0`，**不是 404、也不是 `null`**。
    """

    data: list[T]
    meta: ListMeta


class ErrorBody(Contract):
    """错误详情。**没有 `data` 字段，成功和失败在结构上就是两个类型。**"""

    #: 稳定机器码（`ContractError.code` / 后端 `AppError.code` 同一套）。
    #: 客户端只允许按它分支。
    code: str
    #: 给人看的。会改措辞、会翻译，**禁止**按它做分支。不得含服务器内部路径。
    message: str
    #: **没有默认值，必须显式给。** 猜错这个布尔的代价是不对称的：
    #: 少标一次 `True`，worker 把一次基建抖动当永久失败，销毁一份 8 小时的评测结果，
    #: 矿工烧掉的 TAO 不退。默认 `False` 等于替每个忘了想这件事的人选了那一边。
    retryable: bool


class ErrorEnvelope(Contract):
    """错误响应。**没有 `data`**，`meta.request_id` 照样有 —— 报障就靠它。"""

    error: ErrorBody
    meta: Meta


class ValidationErrorBody(ErrorBody):
    """422 专用：比 `ErrorBody` 多且只多一个 `fields`，且**必填**。

    ADR 02 §8 未决问题 ② 的落地形状（2026-08-18）。那条问题是：
    `detail` 今天是 `[{loc, msg, type}, …]` 一个列表，而 `ErrorBody.message` 是
    一个字符串，逐字段错误没有位置。两个候选是「加可选字段」和「合并成一行文本」。

    选了加字段，但**不是 `fields: … | None = None`** —— 那要求每个路由记得写
    `response_model_exclude_none=True`，漏一个就多吐 `"fields": null`，
    而忘一次是静默的（同一个理由让 `meta.page` 也走了子类，见 `ListMeta`）。
    走子类的话「422 忘了带逐字段信息」在类型层就构造不出来，其余错误码则
    连这个键都不会出现，`ErrorBody` 的已定案形状一个字节没动。

    没选「合并成一行文本」：422 是全部错误码里**唯一以结构为内容**的那个，
    把结构拍平成散文，等于在最需要机器可读的地方丢掉机器可读性。

    ⚠️ `fields` 里**只有 `loc` / `msg` / `type`，没有 `input`**。pydantic 默认会把
    收到的值放进 `input`：worker 发来 `NaN`（Python 的 `json.dumps` 默认就输出这个
    字面量）时那个字段序列化不了，于是「干净的 422」变成未捕获异常 + 500，
    发错数据的人看不到任何原因；而且提交体里有 hotkey 和完整评测结果，
    回显等于把 payload 抄进日志和响应。
    """

    #: `[{"loc": "body.env_scores", "msg": "…", "type": "missing"}, …]`
    #:
    #: ⚠️ 与裸形状的 `detail` **有一处不同**：那边 `loc` 是数组
    #: `["body", "env_scores"]`，这里拼成点号路径。信息量相同，
    #: 但拿到就能直接显示，而且整个 `fields` 的类型收得住（全是字符串）——
    #: 数组那种写法的类型是 `dict[str, list[str] | str]`，
    #: 每个消费方都得为一个键写一次窄化。
    #: 两边的差异只有这一处，迁移说明 §2 里逐条列了。
    fields: list[dict[str, str]]


class ValidationErrorEnvelope(Contract):
    """422 的错误响应。`error` 是 `ValidationErrorBody`，其余与 `ErrorEnvelope` 同。"""

    error: ValidationErrorBody
    meta: Meta


# ─────────────────────────────────────────────────────────────────────────────
# 时间与共用片段
# ─────────────────────────────────────────────────────────────────────────────
#
# **所有 datetime 字段一律 ISO8601 带时区（`+00:00` 结尾）。**
# 0002 之前 `submitted_at` / `created_at` / `updated_at` 在生产还是 TEXT 列且已有
# 两种格式（25 字符 / 32 字符带微秒），仓储层必须归一化后再填进模型 —— 排名重放按
# 时间排序，在 TEXT 上是靠 ISO 字符串比较**恰好**正确，少个 T 或换个时区写法就静默错。
#
# 这里**不用 `AwareDatetime` 强制时区**：读路径同上一节的理由 —— 一条 naive 的
# 历史时间戳不该把整个榜单变成 500。归一化是仓储层的责任，这里只写死要求。
#
# `commit_block_timestamp` 是例外：它在库里是 `bigint`（Unix 秒），线上也按整数下发，
# 保持整数不动。同一个响应里两种时间编码是既成事实，改它没有收益。
#
# 三种时间语义不许互相顶替：
#   链上时间   submissions.submitted_at / commit_block_timestamp  ← 排队、先后判定只认它
#   评测时间   eval_scores.evaluated_at                           ← 排名排序依据
#   本地时间   created_at / updated_at                            ← 只做审计展示


class MinerRef(Contract):
    """矿工的对外身份。"""

    hotkey: str
    #: `hotkey[:12]`，**不是**用户设置的昵称（线上实测 `"5FQxZBhriyAv"`）。
    display_name: str


class ModelRef(Contract):
    name: str
    hf_repo: str
    #: 得分那次任务的 `hf_commit`（不变量 6）。
    #: **禁止回落到该矿工的其它提交** —— 那是事故 C 的形状。
    #:
    #: ⚠️ **查不到是 `null`，不是空串。** 唯一来源是 40 位 `hf_commit`
    #: （CLI `preflight.py` 强制校验后才上链），所以 `""` 从来不是一个合法的
    #: revision，它只可能表示「后端没查到」。而 `""` 在 HuggingFace 的 URL 语义里
    #: 是「用默认分支」—— 拿它去拼 `.../tree/{revision}` 会静默跳到 main，
    #: 审计方核对的就不是得分那次的 commit 了。`null` 让「没有值」无处可藏。
    #: `None` = 没钉 commit。**空串不行**：前端拼
    #: `huggingface.co/{repo}/tree/{revision}` 时 `""` 会静默落到默认分支
    #: （看起来正常、指向的却是另一份代码），`None` 至少是个响亮的 404。
    revision: Annotated[str, Field(min_length=1)] | None = None


class ScoreStat(Contract):
    """`std` 在只跑过一次的提交上是 `None`，**不是 0**。"""

    mean: float
    std: float | None = None
    #: ⚠️ 口径未定（榜单给 1、`/submissions/{id}` 给 6，前端两处都显示）。
    #: 见 spec 04 §9 Q4，未裁决前不要在实现里替它选一个含义。
    trials: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# 拒绝原因（四个公开端点共用）
# ─────────────────────────────────────────────────────────────────────────────

#: `Reason.code` 的受控词表。**只增不改**，和 OpenAPI schema 同等对待。
#:
#: 今天同一件事有三套读法：`rejected` 读 `reject_reason`、`eval_failed` 读
#: `result.error`、`superseded` **无处可读** —— 前端因此在 `QueuePage.tsx:438`
#: 硬编码 `unavailable`（ZCY-162）。这张表是那句硬编码的解药。
#:
#: 写成 `Literal` 而不是 frozenset 校验：OpenAPI 里直接是 enum，
#: 消费方生成的类型就带上了这张表，不用再读文档。
ReasonCode = Literal[
    "BURN_INSUFFICIENT",
    "BURN_TX_NOT_FOUND",
    "BURN_TX_REPLAY",
    "BURN_TX_TOO_OLD",
    "ROUND_MISMATCH",
    "HF_STRUCTURE_INVALID",
    "PLAGIARISM_DETECTED",
    "EVAL_PRECHECK_FAILED",
    "EVAL_CRASHED",
    "SEED_UNAVAILABLE",
    "SUPERSEDED",
    "INFRA_ERROR",
]

#: `Reason.source` 的取值：告诉人去哪一步排查。
ReasonSource = Literal["scan", "eval", "supersede"]

#: 词表的集合形态，给需要做集合运算的调用方和测试用。**与 `Literal` 同源**，
#: 不可能出现「注解里有、词表里没有」。
REASON_CODES: Final[frozenset[str]] = frozenset(get_args(ReasonCode))
REASON_SOURCES: Final[frozenset[str]] = frozenset(get_args(ReasonSource))


class Reason(Contract):
    """非成功终态的原因。老字段（`reject_reason` / `result.error`）原样保留，这是加法。

    契约：`eval_status` 是终态且非 `evaluated` 时 `reason` **必须非 null**，
    `superseded` 也必须有（`code="SUPERSEDED"`）。`evaluated` / `pending` /
    `evaluating` 时为 `null`。
    """

    #: 机器码。客户端只允许按它分支。
    code: ReasonCode
    #: 给人看的。会改措辞、会翻译，**禁止**客户端按它做分支。
    #: **不得含服务器内部路径** —— 线上 `result.error` 里的
    #: `/data2/fs_home/cod/subnet-ws/validator/…` 是既有泄漏，搬迁时切掉。
    message: str
    #: 矿工 / CLI 唯一需要分支的布尔。**基建故障不是业务拒绝**：
    #: `burn_error: failed_to_create_subtensor` 这类必须是 `True`，
    #: 否则矿工烧掉的 TAO 被一次链 RPC 抖动白扔。
    retryable: bool
    source: ReasonSource


# ─────────────────────────────────────────────────────────────────────────────
# worker 契约组 —— GET /api/v1/benchmark/queue
# ─────────────────────────────────────────────────────────────────────────────


class QueueTask(Contract):
    """派发给 GPU worker 的一条任务。

    字段集逐字对齐生产 `handlers/benchmark.py:290-305` 的 15 个键。
    worker 侧读它的是 `benchmark_worker/backend_client.py:fetch_queue()`。
    **删任何一个都是破坏性变更**，且对方在别人的 GPU 机器上，我们通知不到。
    """

    task_id: str
    #: worker 认 `miner_uid`，库里列名是 `uid` —— 出口只发 `miner_uid`
    #: （生产 `t.get("uid",0) or t.get("miner_uid",0)`，双读在入口不在出口）。
    miner_uid: int
    miner_hotkey: str
    hf_repo_id: str
    hf_commit: str
    round_num: int
    #: 红线「种子派生」的对外面。worker 的 `select_init_seed()` 直接读它；
    #: 下面三个是矿工独立复现种子派生的依据，**一个都不能省**。
    seed: int
    block_hash: str
    drand_random: str
    drand_round: int
    #: **绝不能是空数组** —— `min_length=1` 让它在模型层就不可表达。
    #: 库里存的是字符串 `'[]'`，非空字符串是 truthy，`or` 兜底和
    #: `.get(k, default)` 都兜不住 —— 必须先 `json.loads` 再判空，空则回填 6 个
    #: suite。顺序反了就无效：2026-08-14 uid 221/231 的提交进了队列，
    #: worker 一直空转就是卡在这里。
    #: 也**不能是 JSON 字符串**：直接迭代字符串会拆成单字符，曾把任务错报成
    #: "invalid env names"（worker `scoring.parse_env_list` 的兜底是事故遗留，
    #: 不是设计）。
    env_list: Annotated[list[str], Field(min_length=1)]
    #: 只有 `pending` 会被派发（不变量 7）。取值来自 `status.ALL_STATUSES`。
    #: ⚠️ 故意**不写成 `Literal`**：库里冒出一个词表外的状态词时，
    #: 正确反应是这一行不被派发 + 告警，而不是整个队列端点 500 让所有 worker 饿死。
    eval_status: str
    #: 链上提交时间，**排队顺序只认它**（`ORDER BY submitted_at ASC`）。
    #: 用 `created_at` 排等于让矿工替后端故障买单：2026-08-14 重扫后 uid 221 的
    #: 排队依据从链上 16:56 变成入库 19:36，直接掉队（事故 E）。
    submitted_at: datetime | None = None
    #: 本地入库时间。**不可用于排序 / 排队判断**，只做审计。
    created_at: datetime | None = None
    #: `hf_commit[:8] or "v0"`。worker 不读、前端不读；私有 `validator` 仓是否读
    #: 无法查证，删除待评测方确认（spec 07 §10 Q12），在那之前原样保留。
    task_version: str = "v0"


class QueueResponse(Contract):
    """`{"queue_size": N, "tasks": [...]}`。

    这**不是**响应信封，是队列这个资源自己的形状（队列有大小）。worker 读
    `data["tasks"]`、不读 `queue_size`，但两个都别删 —— 加法安全，改形状不安全。
    空队列是 `{"queue_size": 0, "tasks": []}`，不是 404、不是 `null`。
    """

    queue_size: int
    tasks: list[QueueTask] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# worker 契约组 —— POST /api/v1/benchmark/task/{task_id}/score
# ─────────────────────────────────────────────────────────────────────────────


class EnvScore(Contract):
    """一个 suite 的分数。**这里的数字最快 40 分钟后就是链上真实排放。**

    `base_suite` / `perturbation` 必须显式声明并**原样回显** —— worker 的
    `remote_score_matches`（`worker.py:592-595`）逐条比对它们，被 Pydantic 当未声明
    的额外字段丢掉的话，本地有值 / 远端 None → 核对永远失败 → 每次超时都重复 POST。
    """

    env_name: str
    #: `StrictFloat` 而不是 `float`：lax 模式下 `true` 会被静默转成 `1.0`
    #: （实测），而「bool 不算数字」是生产 `_validate_env_scores` 的第 1 道检查。
    #: 仍然接受 JSON 整数（`0` / `1`）—— 实测 strict 模式对 int→float 是放行的。
    #: **值域（0~1）不在这里查**，见模块 docstring 最后一节；写路径由
    #: `check_env_scores()` 在解析之前拦。
    score: StrictFloat
    #: 同上，`StrictInt` 拒 `true`。≥ 0 的检查同样留给 `check_env_scores()`。
    samples: StrictInt
    duration_sec: float | None = None
    error: str | None = None
    #: 非 `libero_pro_custom_1` profile 时 worker 会发。
    base_suite: str | None = None
    perturbation: str | None = None


class ScoreSubmission(Contract):
    """POST /score 的请求体 = **落库后 `result` 字段的形状**（生产原样存 body）。

    两条不许改的行为，改了 worker 的落库核对会永远失败 → 每次超时/5xx 都重复 POST：

    - **`total_score` 原样存，不许重算。** worker 按 profile 权重加权算出来
      （`libero_plus` 的 `PLUS_SUITE_TASK_COUNTS` 不等权），后端一重算两个值就不等，
      而核对用的是 `abs_tol=1e-9`（`worker.py:568`）。生产 patched
      `evaluator.py:1370` 是原样存 ✅，`prototype` 的 `handle_score:229` 重算 ❌，
      按裁决顺序①取生产。
    - **`env_scores` 整条原样回显**（含 `base_suite` / `perturbation`）。

    收到不认识的新字段一律**忽略**（`Contract` 保持 pydantic 默认的 extra=ignore），
    不要 422：评测方加一个字段不该变成销毁 GPU 工时的按钮。

    ⚠️ **读回来的时候**：库里 `result` 可能是 `{}` 或 `""`（还没评完）。
    那种情况在出口归一成 `None`，**不要**给 `success` / `total_score` 加默认值让
    `{}` 也能解析成功 —— 那等于凭空造一个 `total_score=0.0` 出来。
    """

    #: 语义是「**完整执行了 benchmark 协议**」，不是「成功率 > 0」。
    #: 这里用普通 `bool` 不用 `StrictBool`：bool 冒充数字才是攻击面，
    #: 反过来（`1` 冒充 `true`）既不改变语义也没有历史事故。
    success: bool
    #: 见类 docstring。`success=false` 时通常是 0.0，仍然原样存。
    total_score: float
    #: `success=true` 时必填且必须齐 6 个 suite（`check_required_envs`）。
    env_scores: list[EnvScore] = Field(default_factory=list)
    #: `success=false` 时必填。
    error: str | None = None
    #: 端到端墙钟。
    duration_sec: float | None = None
    #: 身份字段：后端以 DB 里的值为准（生产 `sub.get("hotkey", miner_hotkey)`），
    #: body 只作兜底。但 worker 的落库核对会逐个比对，所以**必须原样读得回来**。
    miner_hotkey: str | None = None
    hf_repo_id: str | None = None
    hf_commit: str | None = None
    #: ≤ 0 时后端回落到 DB 的 `round_num`，不写 0。
    round_num: int | None = None
    #: `libero` / `libero_pro` / `libero_pro_custom_1` / `libero_plus`。
    #: worker 的核对「缺失可容忍，存在则必须一致」—— 今天后端根本不存它，
    #: 于是核对永远走「旧后端缺失容忍」分支。生产实际跑哪个 profile 待确认
    #: （spec 07 §10 Q8）。
    benchmark: str | None = None
    #: 队列条目自带的 seed 回传作确认，矿工据此独立复现初始状态。
    #: 红线「种子派生」的对外面。
    init_seed: int | None = None
    expected_trials_per_task: int | None = None
    #: 生产实际发的是 `[]`（`prepare_submit_payload()` 主动清空）：完整 LIBERO-100
    #: 有 130 条，后端逐条同步写库会超过 Cloudflare 代理超时，**稳定返回 524**。
    per_task_scores: list[dict[str, Any]] = Field(default_factory=list)


class ScoreAccepted(Contract):
    """POST /score 的响应。**worker 完全不读响应体**，所以形状是本组最低风险的一处。

    高风险的是状态码：任务已 `superseded` / `rejected` 时必须 **200 + `ignored=True`**
    —— 4xx 会让 worker 丢结果（无所谓），但 5xx 会让它无限退避重试、一直卡在这个
    永远不会再有效的任务上领不到新活。200 是唯一让它「记为已提交、继续往下走」的答案。
    """

    task_id: str
    ok: bool = True
    #: 终态守卫命中：整条丢弃（不写 `eval_scores`、不改状态、不触发排名）。
    ignored: bool = False
    message: str | None = None


def check_env_scores(env_scores: object) -> None:
    """出分入口的四道检查。**必须在写库之前**跑，任务保持原状态好让 worker 重试。

    照搬生产 `evaluator.py` / `handlers/benchmark.py:88 _validate_env_scores`：

    1. `score` 必须是数字，**`bool` 不算**
       （Python 里 `isinstance(True, int)` 是 True）；
    2. NaN 单独拦 —— NaN 和任何数比较都是 False，`0 <= x <= 1` 漏得掉；
    3. `0 <= score <= 1`；
    4. `samples` 必须是**非负整数**（同样排除 bool）。

    ⚠️ **收原始 JSON，不收 `EnvScore`**。两个理由，都不是洁癖：

    - 第 1、2 条只有在**没被框架转过一手**的值上才成立。`EnvScore` 的
      `StrictFloat` 已经能挡住 bool，但那是第二道锁，不是第一道 ——
      任何绕过模型直接读 body 的路径（比如先落审计日志）仍然需要这里。
    - 第 3、4 条的值域检查**故意不放进模型**：读路径要能装回
      `score=99.0` 这样的历史脏数据而不 500（见模块 docstring）。

    历史代价：`{"score": 99.0, "samples": -5}` + 只交 1 个 suite → 200 →
    直接夺擂拿 7% 权重。
    """
    if not isinstance(env_scores, list) or not env_scores:
        raise ContractError(CODE_INVALID_SCORE, "env_scores must be a non-empty list")
    for item in env_scores:
        if not isinstance(item, dict):
            raise ContractError(
                CODE_INVALID_SCORE,
                f"env_score entry must be an object, got {type(item).__name__}",
            )
        env = item.get("env_name", "")
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ContractError(
                CODE_INVALID_SCORE,
                f"invalid score type for {env}: {type(score).__name__}",
            )
        if math.isnan(score):
            raise ContractError(CODE_INVALID_SCORE, f"NaN score for {env}")
        if not 0.0 <= score <= 1.0:
            raise ContractError(
                CODE_INVALID_SCORE, f"score out of range for {env}: {score}"
            )
        samples = item.get("samples", 0)
        if isinstance(samples, bool) or not isinstance(samples, int) or samples < 0:
            raise ContractError(
                CODE_INVALID_SCORE, f"invalid samples for {env}: {samples!r}"
            )


def check_required_envs(env_scores: object) -> None:
    """6 个必需 suite 必须齐全，缺一个整条拒收。

    **数量是 6，不是 4。** worker 的 `--benchmark libero` profile 只产出 4 个 base env
    （`profiles.py:14 PRO_BASE_SUITES`）：生产的 6-env gate 会拒它，
    4-env gate 会**收下** ——
    而 4 个 suite 的均值和 6 个的均值不可比，混进同一张榜就是送分。这正是这条校验
    存在的全部理由。词表来自 `constants.REQUIRED_ENVS`，与 `LIBERO_TASK_SUITES` 同源。

    一份 4-suite 的结果永远不会变成合法的 6-suite 结果，所以重试无意义 ——
    后端应当映射到 4xx（worker 会 `abandoned`），这是正确的。
    """
    names: set[str] = set()
    if isinstance(env_scores, list):
        for item in env_scores:
            if isinstance(item, dict):
                names.add(str(item.get("env_name", "")))
    missing = REQUIRED_ENVS - names
    if missing:
        raise ContractError(
            CODE_MISSING_ENVS, f"missing required envs: {', '.join(sorted(missing))}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# worker 契约组 —— POST /api/benchmark-progress · POST /api/v1/benchmark/progress
# ─────────────────────────────────────────────────────────────────────────────

#: 对外的规范阶段词。**与 `status.ALL_STAGES` 同源**，
#: `tests/test_schemas.py` 钉住两者相等 —— 这个模块不许有第二套阶段词表。
EvalStage = Literal["claimed", "downloading", "prechecking", "running"]

#: worker 上报的进度明细里可能出现的键。前端 `QueueProgressDetail`
#: （`web/src/api/types.ts`）就是按这些画进度条的。
#:
#: ⚠️ 这个元组**只用于把平铺在顶层的写法补进 `detail`**，
#: **不是白名单** —— `detail` 对象整个原样透传，见 `extract_progress_detail`。
PROGRESS_DETAIL_KEYS: Final[tuple[str, ...]] = (
    "suites_done",
    "suites_total",
    "current_suite",
    "last_completed_suite",
    "episodes_done",
    "episodes_total",
    "progress",
    "current_env",
)


def extract_progress_detail(body: dict[str, Any]) -> dict[str, Any]:
    """取出进度明细。**`detail` 对象整个原样存，顶层只补缺。**

    事故 ⑧ 的守卫。老生产是 `detail = body.get("detail", {})` 原样透传；
    2026-08-14 的部署改成只挑 `progress` / `current_env` 两个**顶层**键 ——
    而 worker 根本不发这两个顶层字段，于是存进库的永远是
    `{"progress": null, "current_env": null}`。实测对照：部署前队列页显示
    `7/16 SUITES / libero_goal_lan`，部署后只剩光秃秃的 `EVALUATING`，
    **后端也因此答不上「任务跑到哪一步、还要多久」。**

    四条已验证行为（`prototype/tests/test_progress_detail.py` 8 条）：
    `detail` 是对象 → 整个原样存；同时兼容平铺写法且 `detail` 优先；
    `detail` 不是对象 / 是 `None` / 缺失 → 退回顶层，**不抛异常**；
    完全没有进度信息 → 空对象 `{}`，绝不再产生
    `{"progress":null,"current_env":null}` 这种空壳。
    """
    raw = body.get("detail")
    detail: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    for key in PROGRESS_DETAIL_KEYS:
        if detail.get(key) is None and body.get(key) is not None:
            detail[key] = body[key]
    return detail


class ProgressUpdate(Contract):
    """进度上报请求体。worker 只发 `task_id` / `stage` / `detail` / `worker_id` 四个。

    归一化（词表别名 + `detail` 提取）写成 `model_validator(mode="before")`，
    所以**两条路径共用同一份**：`/api/benchmark-progress` 与
    `/api/v1/benchmark/progress` 无论谁先接，都不可能各自复制一份再漂掉
    （spec 07 §5：新骨架现在两个路由函数体就是各自复制的）。

    后端有两种接法，都安全：

    - `body: ProgressUpdate` 直接交给 FastAPI —— 未知 stage 走 422；
    - 收原始 dict 再调 `ProgressUpdate.from_payload(body)` —— 未知 stage 抛
      `ContractError(INVALID_STAGE)`，可以映射成契约卡钉死的 400。
      进度上报是 best-effort（worker 吞掉错误只打 warning），
      **400 不会丢任何评测结果**，所以这里用 4xx 是安全的。
    """

    task_id: str
    #: 已归一化的对外规范词。存储时才加 `benchmark_` 前缀，
    #: **响应和这里都不带前缀** —— 前端 `ACTIVE_STAGES` 认不出带前缀的写法。
    stage: EvalStage
    #: **开放字典，故意不收窄。** 挑键就是事故 ⑧。
    detail: dict[str, Any] = Field(default_factory=dict)
    worker_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        """把任何一方的写法归一成规范形态。

        - **`status` 与 `stage` 都接受，`status` 优先。** 后端一直读 `status`，
          而前端类型（`QueueProgress`）和公开文档都叫 `stage`，按那边写的人会收到
          `Unknown status: ""`（2026-08-14 实际发生）。worker 现在发的是 `stage`。
        - 阶段词经 `status.normalize_stage()` 归一（大小写与首尾空白先 strip/lower），
          文档词 `running`、前端词 `precheck`、worker 内部词 `evaluating`、
          库里的 `benchmark_*` 存储形态全收下 —— 少收一个就是 400，
          而 `_report_progress` 是 best-effort、400 会被静默吞掉没人发现。
        - 未知 / 空 stage → `ContractError(INVALID_STAGE)`。这与生产一致（旧实现
          对未知和空 stage 都是 400），**不要放宽成默认空串**：打错字的 stage 静默
          入库，前端就渲染不出进度条，而且没有任何人会发现。

        归一化是幂等的：已经是规范词的输入原样通过，所以
        `ProgressUpdate(task_id=..., stage="running")` 这种直接构造也走同一条路。
        """
        if not isinstance(data, dict):
            return data
        body: dict[str, Any] = data
        word = body.get("status")
        if not isinstance(word, str) or not word.strip():
            word = body.get("stage")
        wire = normalize_stage(word) if isinstance(word, str) else None
        if wire is None:
            raise ContractError(CODE_INVALID_STAGE, f"unknown stage: {word!r}")
        return {
            "task_id": str(body.get("task_id", "")),
            "stage": wire,
            "detail": extract_progress_detail(body),
            "worker_id": str(body.get("worker_id", "")),
        }

    @classmethod
    def from_payload(cls, body: dict[str, Any]) -> ProgressUpdate:
        """从原始 JSON 解析，**违例抛 `ContractError` 而不是 `ValidationError`**。

        后端要拿 `.code == "INVALID_STAGE"` 去映射 400，走 pydantic 的
        `ValidationError` 会把它埋进 `ctx`。归一化本身在 `_normalize` 里，
        这里只负责把异常还原成带稳定 code 的那一个。
        """
        try:
            return cls.model_validate(body)
        except ValidationError as exc:
            for err in exc.errors():
                inner = err.get("ctx", {}).get("error")
                if isinstance(inner, ContractError):
                    raise inner from exc
            raise


class ProgressAccepted(Contract):
    """进度上报响应。worker 不读它，前端读 `stage`，旧调用方读 `status`。

    **两个键都给且值相同** —— 这正是 ZCY-158 的形状：同一件事两个字段名，
    只给一个就总有一方拿到 `undefined`。

    `status` 做成 `computed_field` 而不是第二个字段：两者不一致这件事因此
    **在类型层不可表达**，不需要一条校验去防（也就不存在「谁绕过了那条校验」）。
    """

    task_id: str
    stage: EvalStage
    success: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> EvalStage:
        """与 `stage` 恒等。旧调用方读这个键，不许删。"""
        return self.stage


# ─────────────────────────────────────────────────────────────────────────────
# worker 契约组 —— GET /api/submission/{task_id}
# ─────────────────────────────────────────────────────────────────────────────


class SubmissionRecord(Contract):
    """worker 用它决定「那份 8 小时的评测结果到底落库了没有」。**本组风险最高的响应。**

    `worker.py:530 remote_score_matches` 逐字段核对，任何一项不满足都判「未落库」→
    退避后**重新 POST 同一份分数** → 撞终态守卫 / 唯一索引 → 500 → 再核对 → 再重试。

    🔴 **已确认的静默故障**：worker 只接受 `status ∈ {"done","scored","failed"}`，
    而后端现在写的是 `evaluated` / `eval_failed`，0002 之后 `done` 已被 CHECK 禁止。
    `protocol/status.py` 里那个 `unified_to_legacy_score()` 写了转换却没有任何地方调用。
    推断核对当前**恒为 False**，且这条链路上没有任何告警（worker 只 warning，
    那条日志在评测方的机器上）。**这里刻意不做任何转换** —— 两条修法（后端出口转
    `done` / 请评测方接受 `evaluated`）都要人拍板，见 spec 07 §10 Q2。
    ⚠️ 落地时**留一个转换函数并标 TODO**，不要把词表分歧藏进查询里。

    查不到时返回 **200 + `{}`**，不是 404：404 对 worker 是 permanent，会让它把
    「任务不存在」当成理由 abandon 掉一份可能有效的结果。
    """

    task_id: str
    #: ⚠️ 见类 docstring。取值今天是 `status.ALL_STATUSES` 里的词。
    #: 同 `QueueTask.eval_status`，故意不写成 `Literal` —— 5xx 是「重复写库」的按钮。
    status: str
    #: `hotkey` 与 `miner_hotkey` 双写：worker 两个都认，生产行里叫 `hotkey`。
    #: **一个都不能删。**
    hotkey: str
    miner_hotkey: str
    hf_repo_id: str
    hf_commit: str
    round_num: int
    #: 落库的评测结果 = POST 时的 body 原值。没评完时为 `None`
    #: （库里是 `{}` 或 `""` —— 出口归一成 `null`，worker 的核对因此返回 False，
    #: 这是**正确**结果：确实没落库）。
    #: worker 两种编码都接受（dict 或 JSON 字符串），出口固定给对象。
    result: ScoreSubmission | None = None
    reason: Reason | None = None


#: worker 的落库核对只接受的三个词（`worker.py:553`）。
#: 这里**只做记录**，不做转换 —— 见下面那个函数的 TODO。
WORKER_ACCEPTED_STATUSES: Final[frozenset[str]] = frozenset(
    {"done", "scored", "failed"}
)


def worker_status_alias(status: str) -> str:
    """规范状态词 → worker 认的老词。**⚠️ 今天没有任何地方调用它，这是故意的。**

    TODO(裁决后接线)：`GET /api/submission/{task_id}` 的 `status` 到底吐哪一套词，
    是**阻断级未决问题**（spec 07 §10 Q2），两条路都要人拍板：

    (a) 后端在这个端点的出口调本函数，把 `evaluated → done` 吐出去。
        对 worker 是修复，对其它读这个端点的人是破坏性变更。
    (b) 请评测方在 worker 侧加 `evaluated` / `eval_failed`。
        要对方配合，而 `SCOPE.md` 写明「不替对方决定接入时间」。

    在拍板之前 `SubmissionRecord.status` **原样直出库里的规范词**。
    把这个函数放在这里而不是偷偷接进查询里，是为了让词表分歧留在明面上 ——
    ZCY-158 的教训就是翻译表被藏在消费方（worker 的 `_PROGRESS_STAGE_MAP` 至今还在），
    于是没人知道两边其实对不上。

    背景（推断，未实测）：worker 的核对**当前恒为 False**。后端现在写
    `evaluated` / `eval_failed`，0002 之后 `done` 已被 CHECK 禁止。
    于是每一次 POST /score 超时或 5xx 都会走完整重试路径，而这条链路上
    没有任何告警 —— worker 只 `logger.warning`，那条日志在评测方的机器上。

    不在表里的状态词**原样返回**，与 `status.normalize_status()` 的做法一致：
    这个函数不负责判合法性，那是 `ALL_STATUSES` 的事。
    """
    return {"evaluated": "done", "eval_failed": "failed"}.get(status, status)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/benchmark/meta
# ─────────────────────────────────────────────────────────────────────────────


class BenchmarkSpec(Contract):
    """⚠️ `tasks_per_round` / `trials_per_task` 是**对外展示的硬编码常量，
    与 worker 实际跑的东西已经不一致**（worker profile 是 `expected_task_count=160`，
    `--num-trials` 由运维给）。只喂前端展示、不进钱路径，改数值要同时对齐前端和
    评测方，见 spec 07 §10 Q9 —— 在那之前**不改数值**。
    """

    suite: str
    tasks_per_round: int
    trials_per_task: int
    sim_engine: str
    timestep_ms: int
    control: str
    observations: str


class BenchmarkMeta(Contract):
    """纯静态常量，零 DB 访问，**真·匿名**（实测带任意错 key 也 200）。

    文档 `api_reference_en.md:381` 里写的 `-H "X-API-Key: ***"` 是文档错误，
    按裁决顺序①以生产行为为准。
    """

    name: str
    version: str
    phase: str
    #: **手工维护的发布时间**，不是记录更新时间 —— 保持字符串字面量，不要接 `now()`。
    updated_at: str
    maintainer: str
    spec: BenchmarkSpec


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/queue/status
# ─────────────────────────────────────────────────────────────────────────────


class QueueSummary(Contract):
    """全表状态计数。

    ⚠️ 两条实测事实：

    - **`superseded` 桶是新增的，不加就永远对不上 history 的总数**：线上
      history 117 条、queue tasks 85 条，差的 32 条正好是 superseded。
    - **计数必须用 `+=` 不能用赋值。** ZCY-130 的机制：`GROUP BY` 出 `done` 和
      `evaluated` 两行、都映射到 `evaluated`，后到的把先到的**盖掉**，少算 45 条，
      而且没有任何一行日志。0002 的 CHECK 让它进入休眠，**契约不靠 CHECK 兜底**。

    `received` / `burn_checking` / `burn_passed` / `seed_failed` 今天**没有桶**
    （线上 summary 只有这 5 个状态）—— 它们会落进 `unknown`。那是有意的：
    遇到桶外的状态词记 `logger.error` 并计入 `unknown`，
    **静默丢弃正是 ZCY-130 的教训**。
    """

    pending: int = 0
    evaluating: int = 0
    evaluated: int = 0
    eval_failed: int = 0
    rejected: int = 0
    superseded: int = 0
    #: 桶外状态词的去处。恒 0 是正常，非 0 说明状态机漏了一个词，要告警。
    unknown: int = 0
    total: int = 0


#: `QueueSummary` 里代表状态桶的字段名。与 `status.ALL_STATUSES` 的对应关系由
#: `tests/test_schemas.py` 钉住 —— 多一个桶或改一个名字都会在那里红。
QUEUE_SUMMARY_BUCKETS: Final[tuple[str, ...]] = (
    "pending",
    "evaluating",
    "evaluated",
    "eval_failed",
    "rejected",
    "superseded",
)


class QueueStatusTask(Contract):
    """队列里的一条任务（对前端 / 矿工 curl）。

    ⚠️ 字段名是 **`eval_status`** 不是 `status`：线上返回的就是 `eval_status`，
    矿工文档和他们的 curl 按它写。改名的收益为零，风险是矿工脚本静默拿到
    `undefined`。（库里的**列名**叫什么是另一件事，且尚未确认 —— spec 06 §7 Q1。）
    """

    task_id: str
    hotkey: str
    uid: int
    eval_status: str
    burn_status: str
    #: 链上区块。排序键，`commit_block = 0` 的行（实测存在）要按
    #: `commit_block_timestamp` 兜底，**不能回落到 `created_at`**。
    commit_block: int
    burn_block: int
    hf_repo_id: str
    hf_commit: str
    submitted_at: datetime | None = None
    #: SQL 里已经查了但线上没放进响应，契约要求补上。
    #:
    #: ⚠️ **没有默认值，必填。** 队列里的每一条任务**必然属于某一轮** ——
    #: 「不知道是哪一轮」不是一个合法状态，`round_num=0` 更不是：第 0 轮不存在，
    #: 而 0 会被前端和矿工的 curl 当成一个真实轮次去过滤，静默捞回空列表。
    #: 生产列是 `NOT NULL`、后端恒填、2026-08-19 副本 119 行里 0 条为 0 ——
    #: 这个默认值一行都触发不到，留着只会让「忘了填」变得可表达。
    round_num: int
    reason: Reason | None = None
    #: 进度条数据。契约卡里管它叫 "progress"，但 history 里同一份数据叫 `detail` ——
    #: 同一件事两个名字正是本文件要消灭的东西，统一叫 `detail`。
    stage: str | None = None
    detail: dict[str, Any] | None = None
    #: ⚠️ 下面两个**只在 `eval_status == "pending"` 时出现，且是「键缺失」不是 `null`**
    #: （线上实测；前端 `types.ts:146` 按 optional 写）。
    #: 序列化时必须 `exclude_none`（FastAPI 路由上写
    #: `response_model_exclude_none=True`），否则非 pending 行会多出两个 `null` 键。
    queue_position: int | None = None
    #: ⚠️ **单位未定**：新骨架叫 `EVAL_SECONDS`、旧实现叫 `EVAL_TIME_PER_TASK_MIN`，
    #: 值都是 90，一个说秒一个说分钟，差 60 倍；而线上实测单次评测 `duration_sec`
    #: 达 6111 秒 ≈ 102 分钟，**两个常量都不对**。见 spec 06 §7 Q8，未裁决前不要在
    #: 实现里替它选一个单位。
    evaltime: int | None = None


class QueueStatusResponse(Contract):
    """`{summary, tasks}`。空队列是 summary 全 0 + `tasks: []`，不是 404、不是 `{}`。"""

    summary: QueueSummary
    tasks: list[QueueStatusTask] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/submissions/history
# ─────────────────────────────────────────────────────────────────────────────


class SubmissionHistoryItem(Contract):
    """一次提交。队列页和排行榜页**真正的数据源**（queue/status 只是个 status map）。

    白名单来自线上实测的 33 个键，减去 5 个、加上 `reason`：

    - 删 `legacy_task_id` / `repo_hash` / `hotkey_tag` / `worker_id`：内部字段。
      `repo_hash` 是**抄袭判定的模型指纹**，公开它等于把判定依据交出去。
    - 删 `eval_detail`：`detail` 已经是它的解析版，两份重复占了响应体一大截。
    - **永不返回 `status`**：前端 `normalizeHistoryStatus()` 写的是
      `submission.status || submission.eval_status || …`，**先读 `status`** ——
      同一个响应里出现两个状态键，先读到的恰好是没归一的那个，
      95 条里 33 条状态显示错误就是这么来的（生产两个来源 2026-08-19 副本实测
      80 行不一致）。这个响应只许有**一个**状态键。旧实现是在出口
      `row.pop("status", None)` 才躲过一劫；这里靠「模型里没有这个字段」保证，
      并由 `tests/test_schemas.py` 钉死。

    三个**别顺手删**的字段：`result`（前端 `normalizeHistoryStatus` 读
    `result?.success` **先于**读状态词，删了状态渲染直接崩）、`stage`（进度条唯一
    数据源）、`detail`（`suites_done/suites_total`，`test_progress_detail.py` 整个
    文件就是守它的）。
    """

    id: int
    task_id: str
    uid: int
    hotkey: str
    round_num: int
    hf_repo_id: str
    hf_commit: str
    #: 链上区块与**链上时间（Unix 秒整数）**。翻页排序必须是全序
    #: （`commit_block DESC, id DESC`），单键排序在同值行上会重复 / 漏行。
    commit_block: int
    commit_block_timestamp: int
    burn_tx_hash: str
    burn_block: int
    burn_status: str
    block_hash: str
    eval_status: str
    #: 数组，**不是 JSON 字符串**（0002 已把列转成 jsonb）。
    env_list: list[str] = Field(default_factory=list)
    burn_amount_tao: float | None = None
    #: 落库的评测结果原值。库里的 `{}` / `""` 在出口归一成 `null`。
    result: ScoreSubmission | None = None
    #: 进度明细（`eval_detail` 的解析版）。开放字典，见 `extract_progress_detail`。
    detail: dict[str, Any] | None = None
    #: 原样保留 —— 矿工文档教的就是读它。`reason` 是加法，不是替换。
    reject_reason: str = ""
    #: 派种子的三件套。⚠️ **没派过种子是 `null`，不是 0 / 空串**，三个字段
    #: 必须一起有或一起没有 —— 否则会发出 `{seed: null, drand_random: "",
    #: drand_round: 0}` 这种一个字段说「没有」、隔壁说「有」的响应。
    #:
    #: 为什么 0 不行，`drand_round` 是最锋利的那条：drand 官方 API 的
    #: `/public/0` 返回 **HTTP 200**，内容是**当天最新的那一轮**（`latest` 的别名，
    #: 2026-08-19 实测）。审计方拿着我们发布的 `drand_round: 0` 照着查，
    #: 不报错、不 404，会拿到一份今天的信标，然后 `verify_seed()` 必然 False ——
    #: 而他无从判断是我们作弊还是数据缺失。本包的 `seed.drand_round_url()`
    #: 已经对 `<= 0` 直接 `raise`，这里再把 0 当默认值发出去就是自相矛盾。
    #:
    #: `seed` 同理且更隐蔽：0 是 `derive_seed()` 的**合法输出**
    #: （`int.from_bytes(sha256[-4:])`，概率 1/2³²），靠「0 一定是假的」区分不了。
    #: 生产 2026-08-19 副本里 20 条 `seed=0` 全部是「没派过种子」，其中 11 条
    #: 已经出分进过榜单 —— 它们的评测结果不可复现，而响应里看不出来。
    #:
    #: 🔴 **约束只加在能加的两个上，`seed` 故意没有。**
    #: 去掉默认值只改变「字段被省略时给什么」，**并不拒绝显式传进来的 0** ——
    #: 而真正把 0 发上线的是生产方（后端仓储层曾写 `seed=m.seed or 0`，
    #: 主动把 NULL 抹成 0）。所以这里必须是**约束**不是默认值，否则这一整段
    #: 注释描述的失败全都拦不住，只是看起来拦住了。
    #:
    #: `seed` 不能加 `gt=0`：0 是 `derive_seed()` 的**合法输出**
    #: （`int.from_bytes(sha256[-4:])` 值域含 0，概率 1/2³²）。给它加约束等于
    #: 有朝一日拒绝一个真实的种子，而那条提交会因此永远进不了榜 —— 比它想防的更贵。
    #: `seed` 靠的是下面那条三元组校验：单独出现 0 而另外两个是 None，会被拒。
    seed: int | None = None
    drand_random: Annotated[str, Field(min_length=1)] | None = None
    drand_round: Annotated[int, Field(gt=0)] | None = None
    #: ⚠️ 抄袭指纹。是否该继续公开待产品判断（spec 06 §7 Q5），
    #: 在裁决之前保持线上现状（返回），**不要顺手删也不要顺手加 `repo_hash`**。
    model_hash: str = ""
    #: 实测线上全 `null` 而 `result.total_score` 有值 —— 两个分数字段一真一假。
    avg_score: float | None = None
    stage: str = ""
    submitted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def _seed_triple_is_all_or_nothing(self) -> SubmissionHistoryItem:
        """种子三元组必须**一起有或一起没有**。

        这是 `seed` 唯一的守法 —— 它不能像 `drand_round` 那样加 `gt=0`
        （0 是 `derive_seed()` 的合法输出），所以只能靠它和同伴的一致性来判。

        挡的是这个形状：`{seed: 0, drand_round: null, drand_random: null}`。
        单看 `seed` 分不出「种子真的是 0」和「没派过种子」，但配上另外两个就能分：
        **真派过种子的那次，三个字段必然同时有值**（`derive_seed` 的输入就是
        block_hash + round + drand_random，缺一个都算不出来）。

        生产 2026-08-19 副本里 20 条 `seed=0` 全部是这个形状，其中 11 条已经出分
        进过榜单 —— 它们的评测结果不可复现，而响应里看不出来。这条校验让那 11 条
        在序列化那一刻就炸，而不是等审计方去查 drand 才发现。
        """
        present = [
            self.seed is not None,
            self.drand_random is not None,
            self.drand_round is not None,
        ]
        if any(present) and not all(present):
            missing = [
                name
                for name, ok in zip(
                    ("seed", "drand_random", "drand_round"), present, strict=True
                )
                if not ok
            ]
            raise ValueError(
                f"种子三元组不完整：缺 {missing}。"
                "有值的那几个说「派过种子」、缺的那几个说「没派过」，"
                "而 derive_seed 的输入三者缺一不可 —— 这条记录不可复现"
            )
        return self


class SubmissionHistoryResponse(Contract):
    """`{success, submissions, total, limit, offset}`。

    `success` 这层历史外壳**保留不动**（前端 `types.ts:182` 已按它写死），
    但**不再往任何新端点上加**。`limit` / `offset` 是补齐的（`scan-rejections`
    已经有，三个列表端点的外壳应该一样；前端已把它们标成 optional，加了不破坏）。

    ⚠️ `total` 是**过滤后的总数**，不是本页条数。新骨架写的 `total=len(page)` 是错的：
    前端拿 `total` 算页数，本页条数会让页码永远是 1。
    """

    submissions: list[SubmissionHistoryItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
    success: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/submissions/{submission_id}（含 /score.json）
# ─────────────────────────────────────────────────────────────────────────────


class PerTaskScore(Contract):
    """⚠️ **`task_id` 在这里是 `env_name`**（`libero_spatial` 等），不是提交的 task_id。

    同一个响应里 `task_id` 有两个含义。字段名保留（前端已按它写），
    但这句话必须进 OpenAPI description。
    """

    task_id: str
    success_rate: float
    #: episode 数（实测 2000 / 500）。与外层 `ScoreStat.trials`（suite 数，实测 6）
    #: **不同量纲**，两个都叫 trials 是既成事实。
    trials: int


class EvalEnvironment(Contract):
    env_hash: str = ""
    #: ⚠️ 旧实现硬编码 `"mujoco-3.2.1"`，会随评测环境升级变成假话。
    #: 要么从元数据取、要么删字段 —— 未裁决，保持现状不要再硬编码一次。
    sim: str = ""
    eval_commit: str = ""
    #: ⚠️ 与 `SubmissionHistoryItem.seed` 同一件事：没派过种子是 `null` 不是 0。
    #: 这个字段是审计方复现评测的输入，`0` 会被照着跑出一份不一样的结果而不报错。
    #: 外层 `SubmissionDetail.environment` 本来就是 `| None`，内层跟上才自洽。
    seed: int | None = None


class SubmissionArtifacts(Contract):
    score_json_url: str = ""
    logs_url: str = ""


class SubmissionDetail(Contract):
    """单次提交详情。三个前端页面按需拉，`/score.json` 后缀返回逐字节相同的响应。

    **`eval_status` 直出库里的规范词，不做展示态映射。** 旧实现的
    `_SM_STATUS`（`frontend.py:30-32`）把 `eval_failed → "evaluating"`、
    `rejected → "evaluating"` —— 一个被拒的提交，详情接口告诉你它「正在评估中」。
    **这就是 ZCY-150 的后端一半。**

    查不到 → **404 `SUBMISSION_NOT_FOUND`**，不是 200 + `{}`：`{}` 无法与「存在但
    还没跑完」区分，矿工分不清「ID 打错了」和「还没出分」，而且会污染 15 秒缓存
    和监控（错误率永远 0）。
    """

    submission_id: str
    round_id: int
    miner: MinerRef
    model: ModelRef
    eval_status: str
    #: 得分那次任务的 `MAX(eval_scores.evaluated_at)`（数据库权威）。
    #: **不取 `result.timestamp`** —— 那是 worker 进程自己写的时间戳，
    #: 时钟漂移或时区写错就直接进响应。
    scored_at: datetime | None = None
    submitted_at: datetime | None = None
    #: `mean` 由 `eval_scores` 聚合得出。与 `result.total_score` 不一致时
    #: 记 `logger.error` 并**以库为准**（今天没有任何地方会发现两者对不上）。
    score: ScoreStat | None = None
    per_task: list[PerTaskScore] = Field(default_factory=list)
    environment: EvalEnvironment | None = None
    artifacts: SubmissionArtifacts | None = None
    reason: Reason | None = None


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/scan-rejections
# ─────────────────────────────────────────────────────────────────────────────


class ScanRejection(Contract):
    """一条被拒记录。**对矿工白纸黑字承诺过 "No API key required"**，挂 key = 毁约。

    实现上它 = `submissions WHERE eval_status='rejected'` 的一个别名视图
    （`scan_rejections` 表已停写，扫链器只打日志）。**不要为它单独写一套 SQL** ——
    旧实现那么干了，于是 `limit` 校验、排序、字段白名单三处都要各修一遍。

    ⚠️ 线上**不返回 `id`**（新骨架多加了一个），这里也不加。
    """

    uid: int
    hotkey: str
    round_num: int
    hf_commit: str
    hf_repo_id: str
    commit_block: int
    burn_tx_hash: str
    burn_block: int
    #: Unix 秒整数（与同响应里 ISO 字符串的 `created_at` 两种编码并存，是既成事实）。
    commit_block_timestamp: int
    task_id: str
    #: **不允许是空串** —— 矿工烧了 TAO 必须拿到原因；
    #: 也不允许是裸 `"invalid"` / `"error"`。
    reject_reason: str
    created_at: datetime | None = None
    reason: Reason | None = None


class ScanRejectionsResponse(Contract):
    """四个公开列表端点里外壳最完整的一个，**以它为准统一另外两个**。"""

    rejections: list[ScanRejection] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
    success: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/leaderboard
# ─────────────────────────────────────────────────────────────────────────────

#: 榜单 `status` 的取值：**只承载榜位角色，一套词。**
#:
#: `rank == 1 → "champion"`，`rank >= 2 → "scored"`。能进榜的行按定义都是
#: 「6 个环境分齐全且已评完」，生命周期值恒定、零信息量 —— 而生产因为一句没有
#: `ORDER BY` 的 `LIMIT 1` 会随机把 `evaluating` 混进来，同一份数据两次请求给出
#: 不同的词。字面量不许改：前端联合类型和 CSS 类（`.bench-pill--champion`）都按它
#: 写死，而这个值是**直接渲染成用户可见文字**的。
#:
#: `challenger` / `eliminated`（新骨架 mock 的现值）必须去掉：前端联合类型里没有，
#: 而且 `eliminated` 语义上就不成立 —— 挑战失败的矿工**根本不在 rows 里**
#: （King-of-the-Hill 不是「排在后面」）。
LeaderboardStatus = Literal["champion", "scored"]

#: 轮次 `status` 的取值：**只承载轮次生命周期。**
#:
#: 生产把它算成「该轮最新一条 submission 的 eval_status 的映射」，后果是轮次状态
#: 跟着最后一条提交来回翻：2026-08-17 实测 `/rounds/current` 返回 `settled`，
#: 而同一时刻 `submission_count=117` 仍在涨、擂主 09:33 刚换过 ——
#: **一个正在跑的轮次对外宣称已结算。** 轮次状态是轮次的属性，不是某条提交的属性。
#: （`scoring` 第三态没有站得住的定义，见 spec 04 §9 Q2，未裁决前不进词表。）
RoundStatus = Literal["live", "settled"]

#: 集合形态，与上面两个 `Literal` 同源。测试用它断言**这两套词和生命周期词零交集**
#: —— 「一个响应一套词」是这个模块存在的理由之一。
LEADERBOARD_STATUSES: Final[frozenset[str]] = frozenset(get_args(LeaderboardStatus))
ROUND_STATUSES: Final[frozenset[str]] = frozenset(get_args(RoundStatus))


class TasksPassed(Contract):
    """**只统计得分那次任务**的环境分：`passed = count(score >= 0.5)`，`total = 6`。

    生产统计的是该矿工该轮**所有尝试**的行数，实测 uid 218 显示 `12/12` 而同表
    其他行 `6/6` —— 同一列两种量纲。`total` 也不许兜底成 40
    （那是 `benchmark/meta.tasks_per_round`，和 6 个 suite 不是一回事）。
    """

    passed: int = 0
    total: int = 0


class LeaderboardAudit(Contract):
    #: `"/api/v1/submissions/{submission_id}/score.json"`，
    #: 跟随修正后的 `submission_id`。
    score_json_url: str = ""
    #: 没有就空字符串，**不要填假值**。
    logs_url: str = ""
    env_hash: str = ""


class LeaderboardRow(Contract):
    """一行榜单。**每个展示字段都取自「得分那次任务」**（不变量 6）。

    生产按 hotkey 取「最新一条提交」的 task_id 来填 `submission_id`，属事故 C 同型
    缺陷：矿工提交 3 次、得分的是第 2 次时，展示的是第 3 次的 commit。
    """

    rank: int
    #: 该行所属轮次。生产有、新骨架的模型漏了 —— 删掉会让读它的人拿到 undefined。
    round_num: int
    #: 得分那次任务的 `task_id`。**不许兜底拼 `task_{hotkey}_r{round}_v1`**：
    #: 写死的 `_v1` 与真实 attempt 号对不上，审计链接会指向不存在的提交。
    submission_id: str
    miner_uid: int
    miner: MinerRef
    model: ModelRef
    score: ScoreStat
    delta_vs_base: float
    tasks_passed: TasksPassed
    #: 只承载榜位角色。生命周期词落到这里会被 `Literal` 直接拒掉。
    status: LeaderboardStatus
    audit: LeaderboardAudit
    #: 得分那次任务的**链上提交时间**。生产填的是 `MAX(evaluated_at)`（评测完成时间），
    #: 字段名在撒谎 —— 前端表头写的是 `Submitted (UTC)`，而同一条提交在
    #: `/submissions/{id}` 是另一个值（实测差了 2 小时 16 分）。
    submitted_at: datetime | None = None
    #: 新增：得分那次任务的 `MAX(evaluated_at)`。加它是为了让 `submitted_at`
    #: 归位后不丢信息（纯加法，老调用方不受影响）。
    scored_at: datetime | None = None


class Baseline(Contract):
    """基线模型。与 `/rounds/current` 的 `round.base_model` **同源同值**。"""

    model_name: str
    hf_repo: str
    score: ScoreStat
    #: ⚠️ 与 `ModelRef.revision` 同一件事：基线模型从没人钉过 commit，
    #: 那是「没有值」不是「空串这个值」。`""` 拼进 HF URL 会跳默认分支 ——
    #: 基线换了模型，历史榜单会安静地指向新的权重。
    #: `None` = 没钉 commit。**空串不行**：前端拼
    #: `huggingface.co/{repo}/tree/{revision}` 时 `""` 会静默落到默认分支
    #: （看起来正常、指向的却是另一份代码），`None` 至少是个响亮的 404。
    revision: Annotated[str, Field(min_length=1)] | None = None


class LeaderboardResponse(Contract):
    """顶层键恰好是这 5 个。不存在的轮次返回空 rows，**不是 404**。"""

    round_id: int
    #: 服务器 UTC 时刻，**唯一允许随调用变化的字段**（不变量 4：其余部分逐字段幂等）。
    generated_at: datetime
    baseline: Baseline
    #: 该轮榜单的总行数（过滤后），与 `limit` / `offset` 无关。
    total: int = 0
    rows: list[LeaderboardRow] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/rounds/current · GET /api/v1/rounds
# ─────────────────────────────────────────────────────────────────────────────


class Champion(Contract):
    """当前擂主 = 榜单 rank1 的投影。无人上榜时整个对象为 `null`。"""

    miner_hotkey: str
    miner_name: str
    model_name: str
    #: **必须等于 `/leaderboard` rank1 的 `score.mean`**（跨端点一致性断言 2）。
    score: float
    #: 本轮领跑分 − **上一轮（id-1，更早那轮）**冠军分。生产在 DESC 遍历里让 `prev`
    #: 指向更新一轮，于是全体反号（前端实测 `-0.029`，应为 `+0.026`）。
    delta_vs_prev_champion: float | None = None
    #: 得分那次任务的 `MAX(evaluated_at)`。
    settled_at: datetime | None = None
    #: 恒 `True`，是「现任」的意思，**不是「保持了多久」**。
    held: bool = True


class RoundSummaryEntry(Contract):
    """轮次列表里的一项。"""

    id: int
    #: `f"Round {id:02d}"`，`id >= 100` 时 `f"G{id:02d}"`。别改，前端历史表直接显示。
    label: str
    status: RoundStatus
    champion: Champion | None = None


class RoundDetail(Contract):
    """当前轮详情。

    `round.id` 来自 `settings.CURRENT_ROUND`（唯一来源 `backend.yaml`），
    **不读 control.json**（ADR 01）。
    """

    id: int
    label: str
    status: RoundStatus
    network: str
    base_model: ModelRef
    #: 该轮 `submissions` 行数，**含被拒 / superseded**（实测 117，而榜单 `total=3`
    #: —— 两个数不是一回事，前端分别显示）。
    submission_count: int = 0
    #: 没有轮次表，编不出来 → 恒 `null`。**不要拿本地时间凑。**
    started_at: datetime | None = None
    ends_at: datetime | None = None
    champion: Champion | None = None


class CurrentRoundResponse(Contract):
    """`{"round": {...}}`。

    这是数据结构不是信封，前端按它写死了 —— **不要「优化」成裸对象**。
    整库为空时是 `{"round": null}` + 200，**不是** `{"error": "no rounds found"}` + 200
    （用 200 表达失败，前端已被迫在边界做归一化），**也不是** 404。
    """

    round: RoundDetail | None = None


class RoundsSummary(Contract):
    """⚠️ 两个字段都是**全量口径，不随 `limit` 变**。生产在返回页内统计，改个 `limit`
    汇总数就变。

    `cumulative_improvement = 最新已结算轮冠军分 − 最早轮冠军分`，
    **正向提升为正数**；不足 2 轮时 `0.0`。生产写的是 `scores[-1] - scores[0]`
    而序列是 DESC，等于「最早 − 最新」，同样反号（前端实测 `-0.122`，应为 `+0.122`）。
    """

    rounds_settled: int = 0
    cumulative_improvement: float = 0.0


class RoundHistoryResponse(Contract):
    """`{summary, rounds, total}`，`rounds` 按 `id` **降序**。

    `rounds` 必须包含**当前轮，即使零提交** —— 生产只取
    `SELECT DISTINCT round_num FROM submissions`，新开一轮还没人提交时该轮不出现，
    而 `/rounds/current` 又有它，两个端点互相矛盾。
    """

    summary: RoundsSummary
    rounds: list[RoundSummaryEntry] = Field(default_factory=list)
    total: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/weights —— 钱的出口
# ─────────────────────────────────────────────────────────────────────────────

#: `{hotkey: 份额}`，**份额是 0~1 的 float，不是 u16 整数**。
#:
#: 线上实测（2026-08-17）：
#: `{burn_hotkey: 0.9, …: 0.07, …: 0.02, …: 0.01}`，和为 1。
#: u16 归一化发生在**调用方**：`openroboto-cli/validator.py:normalize_weights()`
#: 把它转成 `[0, 65535]` 再 `set_weights`（链上快照 122：
#: `0.9→58981, 0.07→4587, 0.02→1310, 0.01→655`）。
#:
#: 🔴 这个别名存在的唯一理由：新骨架 `legacy.py:244` 把响应模型写成了
#: `dict[str, int]`。接上真实数据的那一刻 Pydantic 拿 `0.9` 去满足 `int` 会抛
#: `ResponseValidationError` → 500 → 外部验证者的 `fetch_weights()` 把异常吞成
#: `{}` → `normalize_weights({}, uids)` 返回空列表 → **发不出 set_weights，
#: 全网排放停摆，日志上只有一行 warning。**
#: 键集合 ⊆ `{burn_hotkey} ∪ 榜前三`，长度 ≤ 4（不变量 5）。
Weights = dict[str, float]


# ─────────────────────────────────────────────────────────────────────────────
# 运维探针 —— GET /healthz · GET /readyz
# ─────────────────────────────────────────────────────────────────────────────


class LivenessResponse(Contract):
    """存活探针。**不碰任何外部依赖。**

    混进 DB 检查 → DB 抖一下 → 探针失败 → 进程管理器判定进程死了 → 重启 →
    连接池重建 → 再抖 → 再重启，抖动被放大成崩溃循环（`owner` 进程 5 天崩 74 次
    无人发现）。DB 有问题该**摘流量**（`/readyz` 503），不是重启进程。

    键集合恰好是这三个。⚠️ `round` / `netuid` 是**本进程的配置回显**，不是领域真值 ——
    消费方**不得**把它当当前轮次用（那个在 `GET /api/v1/rounds/current`）。
    保留它们是为了在运行中也能一眼看出进程实际加载的是哪份配置。

    旧 `/health` 返回的 `timestamp`（naive UTC、无时区后缀）**不要加回来**：
    探针不需要时间戳，带一个语义不明的时间戳正是「链上时间 vs 本地时间混用」的入口。
    """

    round: int
    netuid: int
    status: Literal["ok"] = "ok"


class ReadinessCheck(Contract):
    """`detail` **只在失败时有值，且不含连接串** —— 异常细节进日志，响应固定措辞。"""

    ok: bool
    detail: str | None = None


class ReadinessResponse(Contract):
    """就绪探针。就绪 200 / 任一项不 ok 503，**body 形状两种情况完全一致**
    （调用方不该为失败准备第二套解析），且不走统一错误处理器。

    `migration` 比对 alembic 版本，是 2026-08-14 索引漂移事故的直接产物：
    生产的 `idx_submission_pending` 建在废弃的 `status` 列上、代码按 `eval_status`
    腾位，`CREATE INDEX IF NOT EXISTS` 遇到同名索引会**静默跳过**，错配一整天，
    直到 9 个矿工插不进提交。

    ⚠️ 键集合恰好是这四个（契约稳定性测试钉住）。所以「期望版本取不到」这件事
    **不能靠新增一个顶层 `expected_head` 键**来表达 —— 它必须体现在
    `migration.ok` / `migration.detail` 上。今天的实现是取不到就跳过比对且
    `migration.ok` 仍为 `True`：**这个为事故加的检查在最容易出事的时候会自己关掉，
    而且响应里完全看不出来。**
    """

    ready: bool
    database: ReadinessCheck
    migration: ReadinessCheck
    alembic_version: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# 词表自检
# ─────────────────────────────────────────────────────────────────────────────

#: 值域来自 `status.ALL_STATUSES` 的字段（`模型, 字段名`）。
#:
#: 这些字段**故意注解成 `str` 而不是 `Literal`**：它们装的是从库里读出来的
#: 生命周期状态词，冒出一个词表外的值时正确反应是告警 + 那一行降级，
#: **不是整个端点 500** —— 5xx 对 worker 是「重复写库」的按钮（spec 07 §0.3）。
#: `tests/test_schemas.py` 按这张表逐个核对：字段确实存在、确实是 `str`，
#: 而且这个模块里的另外三套词表（榜位 / 轮次 / 阶段）与生命周期词零交集，
#: 防止有人在某个模型上悄悄引入第五套词汇。
STATUS_VALUED_FIELDS: Final[tuple[tuple[type[Contract], str], ...]] = (
    (QueueTask, "eval_status"),
    (QueueStatusTask, "eval_status"),
    (SubmissionHistoryItem, "eval_status"),
    (SubmissionDetail, "eval_status"),
    (SubmissionRecord, "status"),
)

__all__ = [
    "CODE_INVALID_SCORE",
    "CODE_INVALID_STAGE",
    "CODE_MISSING_ENVS",
    "LEADERBOARD_STATUSES",
    "PROGRESS_DETAIL_KEYS",
    "QUEUE_SUMMARY_BUCKETS",
    "REASON_CODES",
    "REASON_SOURCES",
    "ROUND_STATUSES",
    "STATUS_VALUED_FIELDS",
    "WORKER_ACCEPTED_STATUSES",
    "Baseline",
    "BenchmarkMeta",
    "BenchmarkSpec",
    "Champion",
    "Contract",
    "ContractError",
    "CurrentRoundResponse",
    "EnvScore",
    "Envelope",
    "ErrorBody",
    "ErrorEnvelope",
    "EvalEnvironment",
    "EvalStage",
    "LeaderboardAudit",
    "LeaderboardResponse",
    "LeaderboardRow",
    "LeaderboardStatus",
    "ListEnvelope",
    "ListMeta",
    "LivenessResponse",
    "Meta",
    "MinerRef",
    "ModelRef",
    "PageLike",
    "PageMeta",
    "PerTaskScore",
    "ProgressAccepted",
    "ProgressUpdate",
    "QueueResponse",
    "QueueStatusResponse",
    "QueueStatusTask",
    "QueueSummary",
    "QueueTask",
    "ReadinessCheck",
    "ReadinessResponse",
    "Reason",
    "ReasonCode",
    "ReasonSource",
    "RoundDetail",
    "RoundHistoryResponse",
    "RoundStatus",
    "RoundSummaryEntry",
    "RoundsSummary",
    "ScanRejection",
    "ScanRejectionsResponse",
    "ScoreAccepted",
    "ScoreStat",
    "ScoreSubmission",
    "SubmissionArtifacts",
    "SubmissionDetail",
    "SubmissionHistoryItem",
    "SubmissionHistoryResponse",
    "SubmissionRecord",
    "TasksPassed",
    "ValidationErrorBody",
    "ValidationErrorEnvelope",
    "Weights",
    "check_env_scores",
    "check_required_envs",
    "extract_progress_detail",
    "worker_status_alias",
]
