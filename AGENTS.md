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

消费方钉死精确版本（`openroboto-protocol==1.2.0`）。浮动版本和本地 vendored 副本
都由消费方 CI 拒绝。

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
uv run ruff check . && uv run mypy src
uv version --bump patch       发版前 bump
uv build && uv publish        或让 CI 在 tag 上发布
```

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
- [ ] `uv run ruff check . && uv run mypy src` 全绿
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
