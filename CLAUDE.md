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
- Test generation from full source -> **`llm/router.py`** (cache → Gemini Flash → Claude Haiku overflow).
- Test repair from traceback -> Groq (8B, then 70B).
- Claude Haiku is the **generation overflow** path — activated only when Gemini's 250 RPD is exhausted mid-run. Capped at 20 calls/run (env: `REFLECTA_CLAUDE_OVERFLOW`). Uses the same OAuth/API-key auth as escalation (`ANTHROPIC_API_KEY`). No new keys needed.
- Claude Sonnet is the **repair escalation** path only (`--escalate` flag). Keep both Claude paths rare so the project stays within free/subscription usage.

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
- **Generation Routing via `llm/router.py`**: `generate.py` calls `router.generate()`, NOT `gemini.generate()` directly. The router sequence is: (1) disk cache hit (`llm/cache.py`, sha256 key, 7-day TTL, stored in `{repo}/.reflecta/gen_cache/`) → return immediately; (2) Gemini Flash; (3) on `BudgetExhausted` from Gemini → Claude Haiku overflow (`llm/claude_generate.py`), capped at `REFLECTA_CLAUDE_OVERFLOW` calls (default 20) per process. Overflow counter and cap check live in `router.py` (not in `claude_generate.generate`) so test doubles injected via monkeypatch still participate in budget accounting. When writing tests for `generate.py`, monkeypatch `"reflecta.llm.router.generate"`, NOT `"reflecta.generate.gemini.generate"` — gemini is no longer imported by generate.py.
- **Mocking Convention**: Test generation prompts mandate Python's built-in `unittest.mock` module over the third-party `pytest-mock` `mocker` fixture, as the latter might not be installed in the target codebase.
- **Conversational Response Extraction**: `strip_fences` concatenates **every** ```` ```python ```` block in the LLM response (not just the first). Gemini interleaves prose between multiple fences; taking only the first block truncated the file and dropped imports, leaving dangling `@mock.patch` → `NameError` at collection. Empty fenced blocks are ignored.
- **Generation Validation + Regeneration**: A draft can parse cleanly yet be unrunnable (empty module, no `test_*` function, or a decorator using an unimported name). `validation.validate_test_source` catches these; `generate_test` regenerates once with the concrete reason; an irrecoverable draft is marked `TargetStatus.SKIPPED` and never enters the (futile) repair path. `ast.parse` alone is insufficient — empty files and dangling-decorator fragments are syntactically valid.
- **Target Interpreter / venv Auto-Detection**: Generated tests run under the **target repo's** virtualenv (`.venv`/`venv`/`env`, Windows `Scripts/` or POSIX `bin/`) when present, falling back to reflecta's own interpreter. Detection happens on the original repo *before* `copytree` (the isolated copy excludes the venv). Override with `--python <path>`. Without this, any repo whose deps aren't installed in reflecta's own env failed every `import` with `ModuleNotFoundError`.
- **Import Preflight**: Before the loop, `environment.preflight_imports` checks the targets' third-party imports with `importlib.util.find_spec` (which never executes module code — safe for modules that do I/O at import) and reports missing packages once, clearly, instead of failing every target.
- **pytest Exit-Code Classification**: `runner._classify_failure` maps non-zero exits to `RunResult.failure_kind` (`no_tests`=5, `import_error`=ModuleNotFoundError/ImportError in traceback, `collection_error`=2, else `test_failure`). The loop routes `no_tests`/`import_error` straight to `SKIPPED` — repair can't fix an empty suite or a missing dependency.
- **Entrypoint Skipping**: `coverage_report._detect_entrypoints` flags `main` and functions called under `if __name__ == "__main__"`. `select_next` ranks them last and `run_loop` skips them by default (`--no-skip-entrypoints` to attempt). They drive the whole program from argv and aren't unit-testable.
- **UTF-8 Test Writes**: Generated/repaired tests are written with `encoding="utf-8"`. They routinely contain non-ASCII (sample strings for text-processing code); the platform default (cp1252 on Windows) raised `UnicodeEncodeError`, swallowed by the loop as a silent FAILED.
- **Explicit Failure Messages**: `RateLimitError(message, provider=...)` carries a human provider label; `provider.call_with_retry` raises `BudgetExhausted` with a message that names the provider (Gemini/Groq/proxy), states HTTP 429, echoes the raw API text, and classifies per-minute vs daily via `provider.explain_rate_limit`. The loop's two `BudgetExhausted` handlers render this through `ui.print_budget_exhausted(detail, stage=...)` and both STOP the run (generation- and repair-stage); `import_error` skips name the exact missing module (`_missing_module_name`); the summary's Stop-reason line is expanded to a plain-English explanation. When adding a provider/raise site, always pass `provider=` and keep the raw API message in the exception chain.
- **Static testability triage (no-LLM, spends zero quota)**: `testability.classify_target(source, qualified_name)` (AST only — no execution, no import, no LLM) returns BLOCKED / RISKY / TESTABLE. BLOCKED = the module does network/DB/IO or reads required env / constructs a client *at import* (so any generated test fails at collection) → every target in it is skipped. RISKY = the function body *directly* calls a network/DB/browser/subprocess/file-write API resolved through the module's imports → skipped by default (`--attempt-risky` to include). A client/session passed in as a **parameter** is NOT flagged (dependency injection = testable). `extract_targets` stamps the verdict onto `CoverageTarget.testability`; `run_loop` skips blocked/risky before any provider call and stops with `no_testable_targets` if nothing is attemptable. `loop.triage_repo()` powers `reflecta triage --path` and `run --dry-run` — a no-quota preview of attempt-vs-skip with reasons. The hostile-library registry lives in `testability.py`; harmless top-level calls (`load_dotenv`, `truststore.inject_into_ssl`, `logging.basicConfig`) are whitelisted so modules aren't over-blocked.
- **Failed targets must not leave test files behind**: when repair is exhausted (FAILED or ESCALATED), `run_loop` unlinks the generated file. One broken leftover in `tests/_reflecta/` raises a pytest *collection* error that interrupts the WHOLE suite, so every later coverage measurement collapses (observed on gist-backend: baseline 70.8% measured as 27.5% mid-run) and every subsequent passing test is wrongly discarded until the run stalls. Only KEPT tests stay on disk.
- **Delta gate compares like with like**: `measure_coverage_isolated` seeds + passes the same `--source` flags as `measure_coverage_real`. Without that, source files no test imports are missing from the isolated report but present in the baseline's — different denominators, so keep/discard decisions were noise.
- **Test files are never coverage targets**: `coverage_report._is_test_file` drops `test_*.py`, `*_test.py`, `conftest.py`, and anything under `tests/`/`_reflecta/` from `extract_targets`. Coverage runs with `--source=.` on flat repos, so without this filter reflecta's own generated tests re-enter coverage.json as targets and the run generates tests-for-tests until the Gemini RPD drains.
- **Window-aware 429 backoff**: `provider.call_with_retry` parses the provider's "try again in Xs"/`retryDelay` hint (`parse_retry_hint`) and sleeps at least that long; hint-less per-minute 429s wait 25s/attempt so retries actually span the next 60s window (plain exponential 1+2+4+8+16s never could). Daily-cap 429s (`per day`/`rpd`/`tpd` in the body) raise `BudgetExhausted` immediately — no backoff revives a daily quota, and failing fast is what lets the router fall over to Claude Haiku without a 31s stall per call.
- **Target tooling preflight (coverage/pytest live in the TARGET venv)**: every measurement runs `<target-python> -m coverage run -m pytest`, so `coverage` and `pytest` must be importable in the *target's* environment — reflecta's own venv is irrelevant. A target venv with project deps but no `coverage` made every measurement silently read 0.0% (no report file → `return 0.0`), so the run saw zero targets and exited "cleanly" (observed on Weave/python-service). `environment.preflight_tooling` checks both under the target interpreter and pip-installs missing ones into the detected venv; `loop._ensure_target_tooling` (called by `run_loop` and `triage_repo`) raises an actionable `EnvironmentError` if they're still missing. `measure_coverage_real` now deletes any stale `coverage.json` before measuring and raises `CoverageMeasurementError` (with the captured stderr tail) when no report is produced — never return a silent 0.0 baseline.
- **Salvage pass (repair exhaustion is not all-or-nothing)**: when repairs exhaust on the non-escalate path, `loop._try_salvage` → `salvage.py` parses pytest's `FAILED`/`ERROR` node ids from the last traceback, AST-strips exactly those test functions by line span (decorators included; fixtures/helpers/imports preserved verbatim), re-runs the trimmed file isolated, and lets it face the assertion + delta gates. Declines (target FAILED as before) when no failing name is parseable, no test would survive, or the trimmed file fails its re-run. Counted in `RunReport.tests_salvaged`. Salvage never invents code — deletion only.
- **pytest exit 0 with zero passing tests is NOT a pass**: pytest exits 0 when every collected test is *skipped* — e.g. bare `async def test_*` with no asyncio plugin configured in the target repo. Treating that as a pass let import-time coverage of the target module earn an unearned KEPT (observed on Weave: 4 skipped async tests, +3.2pp from imports alone). `runner.run_test` demotes exit-0-with-no-`N passed` to `failure_kind="all_skipped"` with the skip reasons as traceback (repairable — the "no async plugin" message tells the model to rewrite synchronously). Generation prompts forbid bare async tests outright: async targets are driven via `asyncio.run()` from sync tests.
- **HTTP 413 ≠ 429 — size requests to the model's TPM**: Free-tier limits live in `llm/limits.py` (verified: Groq `llama-3.1-8b-instant` TPM=6000, `llama-3.3-70b-versatile` TPM=12000; Gemini Flash TPM=250k but RPD=250). A single repair request bigger than a model's **tokens-per-minute** budget returns **HTTP 413 "request too large"**, NOT 429 — and waiting can't fix it. Groq's 413 body mentions "tokens per minute" and a `rate_limit_exceeded` code, so `groq.py` must check 413 **before** the 429 heuristic and raise `RequestTooLarge` (which `call_with_retry` never retries). `repair._budget_repair_prompt` trims source/test/traceback to `limits.request_char_budget(model)` before sending; on a 413 the repair re-tries on the higher-TPM 70B once, else records a clean FAIL. Never send an untrimmed prompt to Groq.
- **Mutation gate is gate 3 — opt-in, zero quota, runs LAST**: `mutation.py` is the *honesty gate*. The assertion gate (real asserts) and delta gate (coverage strictly rose) can both pass for a test that runs the lines but verifies nothing. The mutation gate proves the test would *fail if the code were wrong*: it plants single-operator AST mutants in the target function's line span (arithmetic/comparison/boolean op swaps, `not` removal, numeric/bool constant tweaks), re-renders each via `ast.unparse` (always valid Python), and re-runs the already-generated test against each mutant in **one reused temp copy**. Killed = test now fails; survived = test still passes. Score = killed/total; the test is kept only if `score ≥ --min-mutation-score` (default 0.5). **Order matters**: it runs only after a test has already cleared gates 1+2 (i.e. only on would-be-KEPT tests), so cost is bounded to (kept candidates × `--max-mutants`, default 30) subprocess runs — never on discards. **No LLM/quota** — pure AST + execution; `--mutation` is OFF by default precisely because of the extra subprocess cost. A function with **no mutable surface scores 1.0** (can't fault a test for code it can't break). `total==0` ⇒ score 1.0 ⇒ pass. Equivalent/no-op mutants (unparsed text == original) are dropped. Loop integration lives in the `outcome == "kept"` branch of `run_loop`; a sub-threshold score flips KEPT→DISCARDED and increments `RunReport.tests_failed_mutation` (vs `tests_mutation_tested` for passes; `mutants_killed`/`mutants_total` aggregate). Real tree is never mutated — only the disposable copy. Per-mutant timeout guards a mutation that spins an infinite loop.


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
│       ├── testability.py       # classify_target: static no-LLM testable/risky/blocked triage
│       ├── environment.py       # detect_interpreter (target venv) + preflight_imports
│       ├── runner.py            # run_test: subprocess + timeout + exit-code classification
│       ├── repair.py            # repair_test: Groq repair loop
│       ├── gates.py             # passes_assertion_gate, passes_delta_gate, passes_mutation_gate
│       ├── mutation.py          # mutation (honesty) gate: AST mutants + kill-score scorer
│       ├── budget.py            # BudgetTracker: stop before hitting daily cap
│       ├── report.py            # write/read reflecta-report.json
│       ├── prompts.py           # prompt templates (no logic, just strings)
│       └── llm/
│           ├── __init__.py
│           ├── provider.py      # retry wrapper + BudgetExhausted/RequestTooLarge (all calls go here)
│           ├── limits.py        # free-tier RPM/RPD/TPM/TPD per model + token budgeting (single source of truth)
│           ├── gemini.py        # Gemini Flash client
│           ├── groq.py          # Groq client
│           ├── claude_generate.py  # Claude Haiku text generation (generation overflow path)
│           ├── cache.py         # sha256-keyed disk cache for generation results (7-day TTL)
│           └── router.py        # generate() orchestrator: cache → Gemini → Claude Haiku
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
