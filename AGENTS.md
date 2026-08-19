# openroboto-protocol

> # 🚫 这个包里的每一行都是红线
>
> 它的输出决定：哪次评测可复现、哪笔 burn 算数、谁拿到排放。
> 改错一个常量，代价是矿工的钱和整个子网的可信度。
>
> **允许**：新增模块、加测试、加类型标注、改注释与措辞。
> **禁止**：改任何已发布函数的输出、删或改黄金向量、引入运行时依赖、加任何 I/O。

OpenRoboto 子网（Bittensor netuid 80）的协议契约包。
**双方共享的唯一真源**：私有后端与公开的矿工 / 评测代码都从这里装同一个版本号。

发布名 `openroboto-protocol` · import 名 `openroboto_protocol` · Python **3.11+**。

## 0. 为什么存在

种子派生、commitment 编解码、状态词表，两边都要用。此前它们是**四个仓库里的手工副本**，
没有版本号，没有任何机制保证一致 —— 而且已经漂了：`protocol/types.py` 差 105 行、
`payment.py` 差 313 行。种子派生本身当时还没漂，**这个包是趁它还没漂的时候抽出来的**。

spec §5 写明：种子派生改了 = 历史评测不可复现。

## 1. 三条纪律

### ① 零 I/O、零运行时依赖

```toml
dependencies = []   # 保持这样
```

纯函数和纯数据。**一旦这个包发起网络请求或读文件，两边就无法再被证明一致** ——
而"能被证明一致"是它唯一的存在理由。

需要 I/O 的那半留在调用方：`derive_seed()` 在这里，`fetch_drand()` 在后端。

引入依赖前先问：矿工的环境要不要为它付代价？（`schemas.py` 若需要 pydantic，
单独讨论，不要顺手 `uv add`。）

### ② 版本号就是契约版本

| bump | 含义 |
|---|---|
| `patch` | 修 bug，行为不变 |
| `minor` | 加**可选**字段。老数据缺这个键必须有默认值 |
| `major` | 破坏性变更 —— 需要链上数据迁移方案，走评审 |

> ⚠️ **`0.x` 阶段这张表还没生效。** 版本号是 `0.x` 时**不承诺兼容** ——
> `schemas.py` 的形状和 `status.py` 的词表都还可能不走 major 就改。
>
> 这是有意的，而且它的结束条件是一个事件不是一个日期。给一份从没被真正消费过的
> 契约冻结版本号，冻住的是它**碰巧长成的**形状，不是集成之后证明它**需要**的形状。
>
> **`1.0.0` 在下面四条全部打勾那一刻发。** 每一条都不是"应该做的好事" ——
> 缺任何一条，上面那张 bump 表都是一句**无法执行**的话。
>
> - [ ] **`openroboto-backend` 和 `openroboto-cli` 各自钉死上线版本。**
>   `major` 的定义是"破坏性变更，需要迁移方案"。没有上线版本就没有"要迁移的那一方"，
>   于是任何改动都能被论证成不破坏任何人 —— review 时 `major` 和 `minor` 的分界线
>   没有判据可依，那张表只是三行好听的话。
>
> - [ ] **backend 的 3 处手抄副本删干净，改成从子模块 import。**
>   `app/api/envelope.py`（345 行）· `app/domain/reasons.py`（244 行）·
>   `app/domain/worker_reports.py`（316 行），合计 905 行。
>   这个包唯一的卖点是"两边装的能被证明是同一份"。副本还在，这句话**字面为假**。
>   更糟的是守副本的 parity 测试（`test_envelope_parity` / `test_worker_reports` /
>   `test_worker_contract_parity` 等五个文件）在协议包缺失时**整体 skip** ——
>   backend 的 CI 今天是绿的，而那份绿什么都没证明。
>   1.0 冻结的必须是一份**真被 import 过**的形状。
>
> - [ ] **`normalize_weights` 收进本包，两边的副本删掉。**
>   今天两份：`openroboto-backend/app/services/chain_writer.py:82` 和
>   `openroboto-cli/src/openroboto/chain/weights.py:31`。
>   这是链上排放的**最后一道换算**，而它根本不在这个包的表面上 ——
>   1.0 的兼容承诺覆盖不到子网最关键的那一步。
>   两份今天数值一致（2010 组输入实测 0 分歧，含链上快照 122），但**没有任何东西
>   守着它继续一致**：返回类型（`tuple` vs `NormalizedWeights` dataclass）、
>   上限常量（字面量 `65535` vs 具名 `U16_MAX`）、日志语言、参数名
>   （`uids` vs `hotkeys`）已经各走各的，测试也是两套独立的；而"快照里有、
>   metagraph 里没有的 hotkey 被静默丢掉"这件事**只有 backend 会打 WARNING**。
>   三处反直觉细节（`w > 0` 严格大于 · 先除后乘 · `int()` 截断不四舍五入）
>   任何一处单边被"修"，两边算出的 u16 就不同，链上共识被平均掉 ——
>   **不报错、不告警、无法回溯**。
>   ⚠️ 搬运时**不要**把 `chain_writer.py` 那个截断例子一起搬进来：它写的
>   `1/3 → int(21844.999…) = 21844` 是错的（`(1/3)*65535` 精确等于 `21845.0`，
>   三份相加正好 65535）。真正的证据是链上快照 122 自己：`0.9*65535 == 58981.5`，
>   `int` 给 58981、`round` 给 58982 —— 改成 `round()` 会直接改掉一个历史值。
>
> - [ ] **每个模块声明 `__all__`，并有一条测试钉住这张表。**
>   今天只有 `schemas.py` 有（72 项），`constants` / `status` / `seed` /
>   `model_hash` / `model_format` / `commitment` **6 个模块一个都没有**。
>   SemVer 承诺的对象是"公开表面"。表面没有定义，`patch`（行为不变）和
>   `major`（破坏性）之间就没有判据 —— 删掉 `status.py` 里一个没人知道算不算
>   公开的辅助函数，算哪一种？`tests/test_schemas.py::test_every_exported_model_is_pinned`
>   已经是这条的样板，补的是其余 6 个模块。
>   **顶层 `__init__.py` 的 `__all__` 保持空**：消费方一律从子模块 import
>   （见下面「导入形状」）。
>
> 1.0 发出去之后**不许再退回 `0.x`**：那等于把已经生效的承诺收回，而消费方
> `==` 钉着的版本不会自己知道这件事。`tests/test_version.py` 是执行者 ——
> 它同时守两个方向：0.x 却没写"不承诺兼容"、以及 1.0 了还留着这句话。

**导入形状：一律从子模块导入，顶层不 re-export 任何符号。**

```python
from openroboto_protocol.seed import derive_seed  # ✅
from openroboto_protocol.status import normalize_stage  # ✅
from openroboto_protocol import derive_seed  # ❌ 顶层没有这个名字
```

这不是风格偏好，是零依赖承诺的实现方式：`schemas.py` 是唯一需要 pydantic 的模块
（走 `[schemas]` extra）。顶层一旦 re-export，`import openroboto_protocol` 就会
拖进 pydantic —— 矿工只为推导 seed 装这个包，不该在 GPU 机器上编译 pydantic-core。
`tests/test_schemas.py::test_miner_facing_imports_do_not_require_pydantic` 钉住这一条
（它断言 `openroboto_protocol.__all__ == []`）。
实测：`import openroboto_protocol` 0.24 ms，`openroboto_protocol.schemas` 74 ms。
两仓今天 24 条真实 import 全是子模块形状，**从顶层拿符号的 0 条** —— 迁移成本为零。

消费方钉死精确版本（`openroboto-protocol==0.3.0`）。浮动版本和本地 vendored 副本
都由消费方 CI 拒绝（两条检查的原文在 README「What consumers must add to their own CI」，
已经跑在 `openroboto-backend` 和 `openroboto-cli` 上）。

**谁 bump：改动的作者，在同一个 PR 里。** 不留给"发版的人"事后补 ——
那意味着有人要回头判断这次改动算 patch 还是 minor，而唯一知道答案的是写它的人。
review 时版本号和改动一起看，是这条表格唯一能被执行的时机。

**怎么发**（`git tag` 是唯一的发布动作，本地不许 `uv publish`）：

```bash
uv version --bump patch       # 或 minor / major，改 pyproject 的 version
git commit -am "release: 1.0.1" && gh pr create   # 版本号和改动同一个 PR
# PR 合进 main、CI 全绿之后：
git tag v1.0.1 && git push origin v1.0.1
```

tag 一推，`.github/workflows/release.yml` 接手：先跑一遍**和 PR 完全相同**的门槛
（黄金向量 / 3.11+3.12 / 覆盖率 100% / 打包与装包），再停在 `pypi` environment
等一次人工审批，然后用 PyPI Trusted Publishing（OIDC，无长期 token）上传。
tag 与 `pyproject.toml` 的 version 对不上会在发布前中止 —— 消费方钉的是版本号，
对不上账等于这个包唯一的存在理由作废。

**发出去的版本号不可回收。** PyPI 不允许重传同一个版本，删了也不能再用。
发错了只能往前发下一个 patch，所以那次人工审批不是形式。

### ③ 黄金向量是历史，不是期望

`tests/test_golden_vectors.py` 里是**链上已经发生过的**输入输出对。
改一条就是改历史，而那段历史决定了谁拿到钱。

⚠️ 3 条历史 seed（uid 60 / 194 / 192）无法从存储的输入复现，已获认可。
它们**不能**进黄金向量 —— 单独记进「不可复现清单」并注明原因，否则测试永远红。

## 2. 结构

```
src/openroboto_protocol/
├── seed.py           种子派生：block hash + round + drand → uint32
├── commitment.py     commitment payload 编解码
├── model_hash.py     模型指纹
├── model_format.py   可提交的 checkpoint 必须长什么样
├── status.py         任务状态 + 阶段词表
├── schemas.py        每个 API 端点的请求/响应模型
├── constants.py      CHAMPION_MARGIN、REQUIRED_ENVS…
└── py.typed          标记为带类型的包，消费方 mypy 才认
tests/
├── test_golden_vectors.py    链上事实，改了就是改历史
└── test_<module>.py          镜像 src/ 结构
docs/
```

**一个模块一个契约。** 判断某段代码该不该进来，只有一个标准：
**双方是否必须对它有一致的理解？** 是 → 进来；只有一边用 → 不进来。

## 3. 命令

```bash
uv sync                       装依赖（含 dev 组）
uv run pytest                 测试，覆盖率门槛 100%
bash scripts/lint.sh          ruff check + ruff format --check + mypy strict（CI 调的就是这个脚本）
uv run ruff format .          按格式改文件（lint.sh 只查不改）
uvx pre-commit install        提交前自动跑上面这些；pre-commit 不进 dev 组，只在人的机器上装
uv version --bump patch       发版前 bump，见 §1②
git tag v1.0.1 && git push origin v1.0.1     发布的唯一动作
```

不要在本地 `uv publish`。发布走 `release.yml`：本地发等于绕开门槛、绕开审批，
而且 PyPI 上会出现一个没人能复现是从哪个 commit 打出来的包。

覆盖率门槛是 **100%**，不是 90% —— 这个包一共几百行，且每行都在钱路径上，没有留缺口的理由。

## 4. 约定

- **语言：看这个仓会不会公开。**
  - **公开仓（`openroboto-protocol` · `openroboto-cli`）：注释、docstring、
    commit message 全部英文。** 读它们的人不在团队里 —— 矿工、评测方、
    外部验证者、以及任何 `pip install` 之后点进源码的人。
    中文注释对他们等于没有注释，而这两个包的注释本身就是主要资产
    （"为什么不能改回去" 那类信息，代码本身表达不了）。
    ⚠️ 判据是**会不会公开**，不是**此刻是不是 public**：`openroboto-cli`
    现在私有、上线时转公开，按公开写，免得到时候整仓重来一遍。
  - **私有仓（`openroboto-backend`）：注释与文档用中文，commit message 仍然英文。**
    读它的只有团队，中文的信息密度更高；而 commit 是跨仓、对外、且改不动的那一层
    （`git log` / PR 列表 / release notes 会被外部消费者读到，改它要重写历史）。
  - 三种仓都一样：代码标识符 / 命令 / 字段名 / 包名保持英文。
  - commit 格式沿用 Conventional Commits：`type(scope): summary`。summary 用祈使句、
    不超过 72 字符；body 讲**为什么**不是复述 diff。
    可以引用中文文件名和中文标题（`docs/specs/07-worker契约组行为契约.md`），
    那是标识符不是叙述。
  - 格式沿用 Conventional Commits：`type(scope): summary`。summary 用祈使句、不超过
    72 字符；body 讲**为什么**不是复述 diff。
- 导出的函数、常量、dataclass、schema 必须有注释，说明**契约含义**而不是复述名字
  （`status` 的状态机语义、`CHAMPION_MARGIN` 是绝对值不是百分比）。
- 一组必须同源的字段绑成 `frozen=True` dataclass，让错配在类型层就不可能发生。
- 新增模块时同时写清：它承诺什么、不负责什么、谁消费它、最小验证方式。
- 常量集中定义，不许在消费方散落字面量 —— 状态词表打架已经咬过人（队列页进度条整个消失）。

## 5. 提交前

- [ ] `uv run pytest` 全绿，覆盖率 100%
- [ ] `bash scripts/lint.sh` 全绿
- [ ] 改了 `src/` 里任何东西 → `pyproject.toml` 的 `version` 在**同一个 PR**里 bump 了（§1②）
- [ ] 改了任何已发布函数的**输出** → 在描述里单独说明，并说清是 major 还是 patch
- [ ] 加了运行时依赖 → 说明为什么矿工的环境该为它付代价
- [ ] 动了黄金向量 → 说明这段历史为什么需要被改写

## 6. 明确不做

| 不做 | 理由 |
|---|---|
| 任何 I/O、数据库、密钥 | 有 I/O 就无法证明两边一致 |
| 后端业务逻辑 | 排名、准入、权重计算属于 backend |
| 便利工具函数 | 不是契约就不该进来，否则这个包会变成第二个 `utils/` |
| 为了好看做破坏性重命名 | 消费方钉死了版本，改名是 major |

## 7. 参考

- 工程与文档规范：`~/Playground/quantitative-trading-agent-service/CLAUDE.md`
- 重构工作区说明：`../README.md`
- 相关决策：`../openroboto-backend/docs/adr/`
