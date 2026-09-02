#!/usr/bin/env bash
#
# The static-check gate. Three tools cover three parts and none may be dropped:
#   ruff check          lint (including T201, no print — this package must not write
#                       to stdout)
#   ruff format --check formatting; checks only, use `uv run ruff format .` to fix
#   mypy --strict       type correctness
#
# mypy runs on src only: tests/ deliberately passes wrong types to construct illegal
# inputs, which strict mode necessarily flags — and that is exactly what those cases
# verify. ruff runs on the whole repo (tests/ and this script directory included).
#
# CI calls this script directly (the Lint step of .github/workflows/ci.yml), so green
# locally means green there. Writing two copies ends in "passes locally, red in CI",
# or worse, a CI copy that is quietly weaker.
#
# The `uv run` prefix means it works without activating the venv — a bare
# `bash scripts/lint.sh` is what AGENTS.md §3 promises.

set -e
set -x

uv run ruff check .
uv run ruff format --check .
uv run mypy src
