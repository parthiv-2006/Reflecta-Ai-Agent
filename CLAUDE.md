# CLAUDE.md — reflecta

## What this is
reflecta finds untested Python code, writes targeted pytest tests for it using free LLM tiers, runs them, repairs failures, and keeps only tests that raise coverage. The "self-improving" property comes from real execution (pytest + coverage.py), not from an LLM narrating a plan.

## Commands
- Install deps: `uv sync` (or `pip install -e .`)
- Run tests: `pytest`
- Coverage (the machine signal this tool consumes): `coverage run -m pytest && coverage json -o coverage.json`
- Lint/format: `ruff check . && ruff format .`
- Run the tool: `python -m reflecta run --path <dir>`
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
CoverageTarget(file_path, qualified_name, missing_lines, priority, status) -> GeneratedTest(target, test_file_path, source_code, model_used, assertion_count) -> RepairAttempt(...) -> RunReport(coverage_before, coverage_after, tests_kept, tests_discarded, repair_attempts_used, stop_reason). In-memory dataclasses, serialized to `reflecta-report.json`. All types live in `src/reflecta/models.py` — never duplicate them elsewhere.

## Hard rules (these are the product's integrity; never relax them)
1. NEVER edit, overwrite, or delete a human-written test file. Generated tests go ONLY in `tests/_reflecta/test_reflecta_<module>_<n>.py`.
2. EVERY kept test must pass the assertion gate (real, non-trivial `assert`s, checked via AST) AND the coverage-delta gate (total coverage strictly increased). A passing test that does not move coverage is discarded.
3. Repair attempts per target are capped by `--max-repairs` (default 2, the 2-failure rule). On exhaustion, mark the target `failed`/`escalated`, never loop forever.
4. Generated tests run in a subprocess with a timeout. Never run them in-process.
5. Secrets live in env (`GEMINI_API_KEY`, `GROQ_API_KEY`) loaded from `.env` (gitignored). Never commit, log, or put keys in the report.
6. Only run reflecta against the user's own repositories. The free Gemini tier may train on inputs.

## Architecture gotchas
- The coverage signal comes from parsing `coverage json`, not stdout text. Always re-run `coverage json` after writing a test to measure the delta.
- Map missed line numbers back to enclosing functions via the source AST, not regex.
- Test file names use a monotonic per-module counter determined by scanning existing `_reflecta` files at write time — no manifest needed, names never collide across runs.
- Free tiers rate-limit (429) and have daily caps. All provider calls go through `src/reflecta/llm/provider.py` — a wrapper with exponential backoff and a `BudgetExhausted` exception. Never call provider SDKs directly from feature code.
- **O(1) Incremental Coverage Checking**: `measure_coverage` supports an optional `test_file` parameter to run only the new test with the `--append` flag, avoiding O(T) full-suite coverage runs. The `.coverage` database is backed up before running and restored if the test is discarded to keep the state clean.
- **Dynamic PYTHONPATH Injection**: To handle scripts in non-package layout folders (e.g. `scripts/`), the subprocess runner dynamically discovers source directories and injects them into the child's `PYTHONPATH`.
- **Large Source File Trimming**: Large target source files (exceeding 15,000 characters) are trimmed using the AST to include only the target function/method and the top 100 lines (imports/setup) before calling Groq to avoid `HTTP 413 Payload Too Large` developer tier limits.
- **Heavy Directory Exclusions**: Sandbox copying ignores `node_modules`, `build`, `dist`, and `.omc` to optimize disk I/O.
- **Mocking Convention**: Test generation prompts mandate Python's built-in `unittest.mock` module over the third-party `pytest-mock` `mocker` fixture, as the latter might not be installed in the target codebase.
- **Conversational Response Extraction**: `strip_fences` concatenates **every** ```` ```python ```` block in the LLM response (not just the first). Gemini interleaves prose between multiple fences; taking only the first block truncated the file and dropped imports, leaving dangling `@mock.patch` → `NameError` at collection. Empty fenced blocks are ignored.
- **Generation Validation + Regeneration**: A draft can parse cleanly yet be unrunnable (empty module, no `test_*` function, or a decorator using an unimported name). `validation.validate_test_source` catches these; `generate_test` regenerates once with the concrete reason; an irrecoverable draft is marked `TargetStatus.SKIPPED` and never enters the (futile) repair path. `ast.parse` alone is insufficient — empty files and dangling-decorator fragments are syntactically valid.
- **Target Interpreter / venv Auto-Detection**: Generated tests run under the **target repo's** virtualenv (`.venv`/`venv`/`env`, Windows `Scripts/` or POSIX `bin/`) when present, falling back to reflecta's own interpreter. Detection happens on the original repo *before* `copytree` (the isolated copy excludes the venv). Override with `--python <path>`. Without this, any repo whose deps aren't installed in reflecta's own env failed every `import` with `ModuleNotFoundError`.
- **Import Preflight**: Before the loop, `environment.preflight_imports` checks the targets' third-party imports with `importlib.util.find_spec` (which never executes module code — safe for modules that do I/O at import) and reports missing packages once, clearly, instead of failing every target.
- **pytest Exit-Code Classification**: `runner._classify_failure` maps non-zero exits to `RunResult.failure_kind` (`no_tests`=5, `import_error`=ModuleNotFoundError/ImportError in traceback, `collection_error`=2, else `test_failure`). The loop routes `no_tests`/`import_error` straight to `SKIPPED` — repair can't fix an empty suite or a missing dependency.
- **Entrypoint Skipping**: `coverage_report._detect_entrypoints` flags `main` and functions called under `if __name__ == "__main__"`. `select_next` ranks them last and `run_loop` skips them by default (`--no-skip-entrypoints` to attempt). They drive the whole program from argv and aren't unit-testable.
- **UTF-8 Test Writes**: Generated/repaired tests are written with `encoding="utf-8"`. They routinely contain non-ASCII (sample strings for text-processing code); the platform default (cp1252 on Windows) raised `UnicodeEncodeError`, swallowed by the loop as a silent FAILED.


## Repository structure
Follow a senior-engineer layout: flat, discoverable, no clever nesting.

```
reflecta/
├── src/
│   └── reflecta/
│       ├── __init__.py          # version only
│       ├── __main__.py          # entry point: python -m reflecta
│       ├── models.py            # all dataclasses (single source of truth)
│       ├── config.py            # .env loading + API-key preflight (require_api_keys)
│       ├── cli.py               # typer commands: run / clean / report
│       ├── loop.py              # main orchestration loop
│       ├── coverage_report.py   # extract_targets: coverage.json -> CoverageTarget list
│       ├── selection.py         # select_next: ranks pending targets (entrypoints last)
│       ├── generate.py          # generate_test: Gemini + validate/regenerate, writes _reflecta file
│       ├── validation.py        # validate_test_source: reject empty/no-test/missing-import drafts
│       ├── environment.py       # detect_interpreter (target venv) + preflight_imports
│       ├── runner.py            # run_test: subprocess + timeout + exit-code classification
│       ├── repair.py            # repair_test: Groq repair loop
│       ├── gates.py             # passes_assertion_gate, passes_delta_gate
│       ├── budget.py            # BudgetTracker: stop before hitting daily cap
│       ├── report.py            # write/read reflecta-report.json
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
│   ├── test_config.py
│   ├── test_coverage_isolation.py
│   └── _reflecta/              # generated tests only — never edit by hand
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
  - `fix: per-module counter scans _reflecta dir at write time`
  - `test: add repair loop ceiling regression test`
  - `chore: add ruff to pyproject dev deps`
- Include the task number in the message body when it maps 1-to-1 to a PLAN.md task.
- Push the branch after every commit. Never leave local-only commits overnight.

**After each PLAN.md task is done:**
1. Run `ruff check . && pytest` — both must pass.
2. Commit with the message from the task's "Commit:" line.
3. Push the branch: `git push -u origin <branch>`.
4. Mark the task `[x]` in PLAN.md and commit that too: `chore: mark task N done in PLAN.md`.
5. Merge to `main` and push directly — no PR or confirmation needed.

**What not to do:**
- Do not `git add .` blindly — stage specific files.
- Do not amend published commits.
- Do not force-push `main`.
- Do not leave `TODO` comments as a substitute for filing a task.

## Repo etiquette
- `/clear` between tasks. Stay under ~70% context.
- Test-first wherever it fits; this codebase is about tests, so write reflecta's own test first, watch it fail, then implement.
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
