# CLAUDE.md

The **single source** of project conventions is `AGENTS.md` (shared by Claude Code /
Codex / Cursor, so the two do not drift apart).

@AGENTS.md

---

## Claude Code specifics

- **Read `../openroboto-backend/DECISIONS.md` first** — Cameron's ruling log, shared
  by the three repos. It outranks anything in this repo.
- **This is a public repo: comments, docstrings and commit messages are all in
  English** (AGENTS.md §4). Its readers are not on the team — miners, the
  evaluation party, external validators, anyone who clicks into the source after
  `pip install`. Written here as well because the default is to follow whatever
  language a repository already uses.
- Documentation and engineering conventions follow
  `~/Playground/quantitative-trading-agent-service/CLAUDE.md`. Read it before
  writing docs or creating directories; do not restate it from memory.
- Every line in this package is on the money path. State the reason before touching
  any released function.
- Order of authority when documents disagree: **production behaviour > executable
  code > ADR > old plans**. Still ambiguous at the code level — stop and ask, do not
  write an answer that merely looks reasonable.
