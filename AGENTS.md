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
> 这是有意的，而且它的结束条件是一个事件不是一个日期：
> **`openroboto-backend` 和 `openroboto-cli` 还没有确定各自的上线版本。**
> 今天它俩甚至装不上这个包 —— backend 里还留着 3 处手抄副本
> （`app/domain/worker_reports.py`、`app/domain/reasons.py`、
> `app/api/envelope.py` 的复制段），而那正是这个包要消灭的漂移本身。
> 给一份从没被真正消费过的契约冻结版本号，冻住的是它**碰巧长成的**形状，
> 不是集成之后证明它**需要**的形状。
>
> **`1.0.0` 在 backend 和 cli 各自钉死上线版本那一刻发。** 从那一版起这张表生效，
> 而且不许再退回 `0.x` —— `tests/test_version.py` 就是执行者。

消费方钉死精确版本（`openroboto-protocol==0.1.0`）。浮动版本和本地 vendored 副本
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

- 语言：面向团队的注释与文档用中文，代码标识符 / 命令 / 字段名 / 包名保持英文。
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
