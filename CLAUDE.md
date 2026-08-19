# CLAUDE.md

项目规范的**唯一来源**是 `AGENTS.md`（Claude Code / Codex / Cursor 共用一份，避免两份文档漂移）。

@AGENTS.md

---

## Claude Code 专属

- **这是公开仓：注释、docstring、commit message 全部英文**（AGENTS.md §4）。
  读它的人不在团队里 —— 矿工、评测方、外部验证者、任何 `pip install` 之后点进
  源码的人。中文注释对他们等于没有注释，而这个包的注释本身就是主要资产。
  ⚠️ 我默认会跟着仓库里已有的语言走，所以这条要写在这里。
- 文档与工程规范以 `~/Playground/quantitative-trading-agent-service/CLAUDE.md` 为准，写文档或建目录前先读它，不要凭记忆复述。
- 这个包的每一行都在钱路径上。动任何已发布函数之前，先说明理由，再动手。
- 文档说法冲突时的裁决顺序：**生产行为 > 可执行代码 > ADR > 旧计划**。到代码这一层仍含糊 —— 停下来问，不要写一个看起来合理的答案。
