#!/usr/bin/env bash
#
# 静态检查门禁。三个工具各管一段，都不能删：
#   ruff check          lint（含 T201 禁 print —— 这个包不该往 stdout 写东西）
#   ruff format --check 格式；只查不改，改用 `uv run ruff format .`
#   mypy --strict       类型正确性
#
# 只对 src 跑 mypy：tests/ 里为了构造非法输入会故意传错类型，strict 下必然报错，
# 而那正是用例要验证的东西。ruff 则查全仓（tests/ 和这个脚本目录也算）。
#
# CI 直接调这个脚本（.github/workflows/ci.yml 的 Lint 步骤），所以本地绿 = CI 绿。
# 分开写两套的下场是：本地过了、CI 红，或者更糟 —— CI 那套悄悄比本地弱。
#
# 用 `uv run` 前缀，这样不激活 venv 也能跑 —— AGENTS.md §3 承诺的就是
# 裸 `bash scripts/lint.sh`。

set -e
set -x

uv run ruff check .
uv run ruff format --check .
uv run mypy src
