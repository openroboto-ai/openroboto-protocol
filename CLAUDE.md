# CLAUDE.md

项目规范的**唯一来源**是 `AGENTS.md`（Claude Code / Codex / Cursor 共用一份，避免两份文档漂移）。

@AGENTS.md

---

## Claude Code 专属

- **commit message 一律英文**（AGENTS.md §4）。这条对我尤其重要：我默认会跟着
  仓库里已有的语言走，而这几个仓的注释和文档是中文的 —— 不专门写一句，我就会
  写出中文 commit，而那是**跨仓、对外、且改不动**的一层（改它要重写历史）。
- 文档与工程规范以 `~/Playground/quantitative-trading-agent-service/CLAUDE.md` 为准，写文档或建目录前先读它，不要凭记忆复述。
- 这个包的每一行都在钱路径上。动任何已发布函数之前，先说明理由，再动手。
- 文档说法冲突时的裁决顺序：**生产行为 > 可执行代码 > ADR > 旧计划**。到代码这一层仍含糊 —— 停下来问，不要写一个看起来合理的答案。
