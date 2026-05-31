# CLAUDE.md — coverloop

## What this is
coverloop finds untested Python code, writes targeted pytest tests for it using free LLM tiers, runs them, repairs failures, and keeps only tests that raise coverage. The "self-improving" property comes from real execution (pytest + coverage.py), not from an LLM narrating a plan.

## Commands
- Install deps: `uv sync` (or `pip install -e .`)
- Run tests: `pytest`
- Coverage (the machine signal this tool consumes): `coverage run -m pytest && coverage json -o coverage.json`
- Lint/format: `ruff check . && ruff format .`
- Run the tool: `python -m coverloop run --path <dir>`
- Run a single test: `pytest tests/test_x.py::test_name -q`

## Stack
- Python 3.11, pytest, coverage.py
- Generation: Gemini Flash via `google-genai` (large context, drafts whole test files)
- Triage + repair: Groq via `groq` (Llama 3.1 8B for parse/rank/first repairs, 3.3 70B for hard repairs)
- Escalation (v2 only): `claude-agent-sdk`
- CLI: typer. Format: ruff.

## Free-stack routing (do not violate)
- Orchestration is deterministic Python. Do NOT turn the main loop into an LLM agent.
- Coverage parsing / target ranking -> Groq 8B.
- Test generation from full source -> Gemini Flash.
- Test repair from traceback -> Groq (8B, then 70B).
- Claude is the escalation path only (v2), never the main loop. Keep Claude calls rare so the project stays within free/subscription usage.

## Data model (see SPEC.md for full)
CoverageTarget(file_path, qualified_name, missing_lines, priority, status) -> GeneratedTest(target, test_file_path, source_code, model_used, assertion_count) -> RepairAttempt(...) -> RunReport(coverage_before, coverage_after, tests_kept, tests_discarded, repair_attempts_used, stop_reason). In-memory dataclasses, serialized to `coverloop-report.json`. All types live in `src/coverloop/models.py` — never duplicate them elsewhere.

## Hard rules (these are the product's integrity; never relax them)
1. NEVER edit, overwrite, or delete a human-written test file. Generated tests go ONLY in `tests/_coverloop/test_coverloop_<module>_<n>.py`.
2. EVERY kept test must pass the assertion gate (real, non-trivial `assert`s, checked via AST) AND the coverage-delta gate (total coverage strictly increased). A passing test that does not move coverage is discarded.
3. Repair attempts per target are capped by `--max-repairs` (default 2, the 2-failure rule). On exhaustion, mark the target `failed`/`escalated`, never loop forever.
4. Generated tests run in a subprocess with a timeout. Never run them in-process.
5. Secrets live in env (`GEMINI_API_KEY`, `GROQ_API_KEY`) loaded from `.env` (gitignored). Never commit, log, or put keys in the report.
6. Only run coverloop against the user's own repositories. The free Gemini tier may train on inputs.

## Architecture gotchas
- The coverage signal comes from parsing `coverage json`, not stdout text. Always re-run `coverage json` after writing a test to measure the delta.
- Map missed line numbers back to enclosing functions via the source AST, not regex.
- Test file names use a monotonic per-module counter determined by scanning existing `_coverloop` files at write time — no manifest needed, names never collide across runs.
- Free tiers rate-limit (429) and have daily caps. All provider calls go through `src/coverloop/llm/provider.py` — a wrapper with exponential backoff and a `BudgetExhausted` exception. Never call provider SDKs directly from feature code.

## Repository structure
Follow a senior-engineer layout: flat, discoverable, no clever nesting.

```
coverloop/
├── src/
│   └── coverloop/
│       ├── __init__.py          # version only
│       ├── __main__.py          # entry point: python -m coverloop
│       ├── models.py            # all dataclasses (single source of truth)
│       ├── cli.py               # typer commands: run / clean / report
│       ├── loop.py              # main orchestration loop
│       ├── coverage_report.py   # extract_targets: coverage.json -> CoverageTarget list
│       ├── selection.py         # select_next: ranks pending targets
│       ├── generate.py          # generate_test: calls Gemini, writes _coverloop file
│       ├── runner.py            # run_test: subprocess + timeout
│       ├── repair.py            # repair_test: Groq repair loop
│       ├── gates.py             # passes_assertion_gate, passes_delta_gate
│       ├── budget.py            # BudgetTracker: stop before hitting daily cap
│       ├── report.py            # write/read coverloop-report.json
│       ├── prompts.py           # prompt templates (no logic, just strings)
│       └── llm/
│           ├── __init__.py
│           ├── provider.py      # retry wrapper + BudgetExhausted (all calls go here)
│           ├── gemini.py        # Gemini Flash client
│           └── groq.py          # Groq client
├── tests/
│   ├── __init__.py
│   ├── test_smoke.py
│   ├── test_models.py
│   ├── test_coverage_report.py
│   ├── test_selection.py
│   ├── test_provider.py
│   ├── test_generate.py
│   ├── test_gates_assertion.py
│   ├── test_gates_delta.py
│   ├── test_runner.py
│   ├── test_repair.py
│   ├── test_loop_happy.py
│   ├── test_loop_budget.py
│   ├── test_cli.py
│   └── _coverloop/              # generated tests only — never edit by hand
├── examples/
│   └── sample_project/          # fixture used by the walking skeleton and CLI demo
│       ├── calc.py
│       └── tests/
│           └── test_calc_partial.py
├── docs/
│   └── BUILD-PLAN.md            # narrative phase guide (not the task list)
├── .claude/
│   └── settings.json
├── .env.example
├── .gitignore
├── pyproject.toml
├── SPEC.md
├── PLAN.md                      # the authoritative task list — update after every task
└── CLAUDE.md
```

One module per concern. If a file is hard to name, the abstraction is probably wrong. No `utils.py` catch-alls.

## Git workflow — commit like a senior engineer
Every completed unit of work gets its own commit and push. Never accumulate a day's work into one large commit; reviewers (and your future self) cannot reason about it.

**Branching:**
- `main` is always green and deployable. Never commit broken code directly to `main`.
- Create a feature branch for every task in PLAN.md: `git checkout -b feat/<short-slug>`.
- Branch naming: `feat/<slug>` for new features, `fix/<slug>` for bug fixes, `refactor/<slug>` for refactors, `chore/<slug>` for tooling/docs.
- Merge to `main` only when the task's verify criteria pass and tests are green.

**Commit discipline:**
- One logical change per commit. If you can describe a commit with "and", split it.
- Commit message format: `<type>: <what changed in imperative mood>`. Examples:
  - `feat: assertion gate rejects trivially-true asserts`
  - `fix: per-module counter scans _coverloop dir at write time`
  - `test: add repair loop ceiling regression test`
  - `chore: add ruff to pyproject dev deps`
- Include the task number in the message body when it maps 1-to-1 to a PLAN.md task.
- Push the branch after every commit. Never leave local-only commits overnight.

**After each PLAN.md task is done:**
1. Run `ruff check . && pytest` — both must pass.
2. Commit with the message from the task's "Commit:" line.
3. Push the branch: `git push -u origin <branch>`.
4. Mark the task `[x]` in PLAN.md and commit that too: `chore: mark task N done in PLAN.md`.
5. Open a PR or merge to `main` depending on review preference.

**What not to do:**
- Do not `git add .` blindly — stage specific files.
- Do not amend published commits.
- Do not force-push `main`.
- Do not leave `TODO` comments as a substitute for filing a task.

## Repo etiquette
- `/clear` between tasks. Stay under ~70% context.
- Test-first wherever it fits; this codebase is about tests, so write coverloop's own test first, watch it fail, then implement.
- Update PLAN.md (mark tasks done, add discovered sub-tasks) before ending a session.

## Hooks
PostToolUse hook auto-formats edited Python with ruff. Sketch for `.claude/settings.json`:
```jsonc
"hooks": {
  "PostToolUse": [
    { "matcher": "Edit|Write",
      "hooks": [{ "type": "command", "command": "ruff format \"$CLAUDE_FILE_PATH\" 2>/dev/null || true" }] }
  ]
}
```

## Pointers
- Spec and contracts: SPEC.md
- Task sequence and status: PLAN.md
- Narrative phase guide: docs/BUILD-PLAN.md
