# PLAN.md — reflecta

Ordered build tasks. Vertical slices, riskiest assumption first, something runs end to end from task 0. Each task lists what it does, which files it touches, and how to verify. Check items off as you go so progress survives `/clear`. One task per session. Plan Mode for anything non-trivial.

Legend: `[ ]` todo, `[~]` in progress, `[x]` done.

---

## Phase 4 — Walking skeleton

- [x] **0. End-to-end with everything faked but the risky part.**
  - Does: builds the fixture `examples/sample_project/calc.py` (3 small functions) with a partial test leaving one known gap. Runs `coverage run -m pytest && coverage json`. Calls `extract_targets` against the real `coverage.json` (not hardcoded line numbers) to confirm the fixture's missing lines come out correctly — this removes one variable when debugging a Gemini failure. Then makes ONE real Gemini Flash call to write a test into `tests/_reflecta/test_reflecta_calc_0.py`, reruns coverage, prints `coverage: BEFORE% -> AFTER%`.
  - Files: `examples/sample_project/`, `src/reflecta/skeleton.py`, `src/reflecta/llm/gemini.py` (minimal).
  - Verify: (1) `extract_targets` returns the expected `CoverageTarget` for the fixture's known gap. (2) `ast.parse(gemini_output)` succeeds — i.e., Gemini's raw response is syntactically valid Python before the test is even run. (3) The printed AFTER number is strictly higher than BEFORE on a real run. If `ast.parse` fails or the coverage number does not move, stop and revise the prompt before building anything else.
  - Commit: `"walking skeleton: gemini-written test moves real coverage"`.

- [ ] **0.5. Shared data model.**
  - Does: defines all dataclasses in one canonical file — `CoverageTarget`, `GeneratedTest`, `RepairAttempt`, `RunReport` — so every subsequent task imports from the same place and there are no ad-hoc or duplicate definitions scattered across modules.
  - Files: `src/reflecta/models.py`, `tests/test_models.py` (light: instantiate each dataclass, assert field names and defaults).
  - Verify: `from reflecta.models import CoverageTarget, GeneratedTest, RepairAttempt, RunReport` works from any module. No task after this point defines its own version of these types.
  - Commit: `"feat: canonical data model in models.py"`.

---

## Phase 5 — Vertical slices

- [ ] **1. Real coverage-gap extraction.**
  - Does: `extract_targets(coverage_json, repo_path)` parses `missing_lines` per file into `CoverageTarget` objects, mapping line ranges back to enclosing function/method names via the source AST.
  - Files: `src/reflecta/coverage_report.py`, `tests/test_coverage_report.py`.
  - Verify (test-first): feed a saved sample `coverage.json` + source, assert the targets and their `qualified_name`s come out right.
  - Commit: `"feat: coverage-gap extraction with AST line mapping"`.

- [ ] **2. Target selection and ranking.**
  - Does: `select_next(targets)` ranks by a simple priority (more missing lines and simpler signatures first) and returns the next pending target.
  - Files: `src/reflecta/selection.py`, `tests/test_selection.py`.
  - Verify (test-first): given a target list, assert ordering; assert `None` when all are non-pending.
  - Commit: `"feat: target selection and priority ranking"`.

- [ ] **2.5. LLM provider wrapper.**
  - Does: a thin wrapper around both provider clients (`gemini.py`, `groq.py`) that enforces exponential backoff on 429 responses and raises a `BudgetExhausted` exception when the retry ceiling is hit. All LLM calls in Tasks 3 and 6 go through this wrapper. Without it, 429s during development are indistinguishable from bad prompts.
  - Files: `src/reflecta/llm/provider.py`, `tests/test_provider.py`.
  - Verify (test-first): mock HTTP 429 three times then success → retries with backoff, returns response. Mock 429 beyond max-retries → raises `BudgetExhausted`. Mock 200 → returns immediately, no delay.
  - Commit: `"feat: LLM provider wrapper with exponential backoff"`.

- [ ] **3. Gemini generation with a real prompt.**
  - Does: `generate_test(target, source, existing_tests)` builds a prompt with the full source module, existing tests, and the exact missed lines, calls Gemini Flash via the provider wrapper, extracts a clean pytest file, writes it to the `_reflecta` path. The per-module counter is determined at write time by scanning existing `_reflecta/test_reflecta_<module>_*.py` files — no manifest needed, no collisions across runs.
  - Files: `src/reflecta/generate.py`, `src/reflecta/llm/gemini.py`, `src/reflecta/prompts.py`, `tests/test_generate.py`.
  - Prompt iteration note: the first prompt will likely produce syntactically invalid output or wrong imports on some inputs. The verify step requires 3 distinct fixture inputs to all produce `ast.parse`-valid Python before this task is considered done. If fewer than 3 of 3 pass, iterate the prompt template.
  - Verify: (1) with a mocked Gemini client, assert the prompt contains the missed lines and the file is written to the right path. (2) One live smoke test behind a `@pytest.mark.live` marker — run against 3 different `calc.py` gap shapes, all must return `ast.parse`-valid Python.
  - Commit: `"feat: Gemini test generation with prompt and file writer"`.

- [ ] **4. Assertion gate.**
  - Does: `passes_assertion_gate(test)` parses the test AST and rejects zero-assertion tests and trivially-true assertions (`assert True`, `assert 1 == 1`, asserting a literal against itself) before the test is run. This gate fires upstream of `run_test` in the loop; placing it here ensures the runner in Task 5 never receives a gateless test.
  - Files: `src/reflecta/gates.py`, `tests/test_gates_assertion.py`.
  - Verify (test-first): assertion-free test → rejected; `assert True` → rejected; `assert 1 == 1` → rejected; `assert add(2, 3) == 5` → accepted.
  - Commit: `"feat: AST-based assertion gate"`.

- [ ] **5. Test execution and traceback capture.**
  - Does: `run_test(test_file, repo_path, timeout_s)` runs only the new test in a subprocess, returns `RunResult{passed, traceback, duration}`. Per-test timeout.
  - Files: `src/reflecta/runner.py`, `tests/test_runner.py`.
  - Verify (test-first): a known-passing fixture test returns passed; a known-failing one returns the traceback; a hanging one is killed at the timeout.
  - Commit: `"feat: subprocess test runner with timeout and traceback capture"`.

- [ ] **6. Groq repair loop with attempt ceiling.**
  - Does: on failure, `repair_test(test, result, source)` sends the failing test + traceback + source to Groq via the provider wrapper, gets a patched test, reruns. Repeats up to `--max-repairs` (the 2-failure rule). On exhaustion, marks target `failed` (v1) or `escalated` (v2 hook, no-op for now).
  - Files: `src/reflecta/repair.py`, `src/reflecta/llm/groq.py`, `tests/test_repair.py`.
  - Verify (test-first): mocked Groq that fixes on attempt 2 → kept; mocked Groq that never fixes → stops at the ceiling, target marked `failed`, no infinite loop.
  - Commit: `"feat: Groq repair loop with attempt ceiling"`.

- [ ] **7. Coverage-delta gate.**
  - Does: `passes_delta_gate(before, after)`. Integrate it so a passing test is kept only if total coverage strictly rose; otherwise the generated file is deleted and the target marked `discarded`.
  - Files: `src/reflecta/gates.py`, `src/reflecta/loop.py` (stub), `tests/test_gates_delta.py`.
  - Verify (test-first): passing test that moves coverage → kept and file remains; passing test that does not → `discarded` and file removed.
  - Commit: `"feat: coverage-delta gate with file cleanup on discard"`.

- [ ] **8a. Happy-path loop.**
  - Does: `loop.py` wires the happy path: extract → select → generate → assertion gate → run → delta gate → keep/discard → select next, until all targets are exhausted or `max-iters` is hit. No repair, no budget yet. Populates `RunReport` with kept/discarded counts and stop reason.
  - Files: `src/reflecta/loop.py`, `tests/test_loop_happy.py`.
  - Verify: run against the fixture with 2 targets, both succeed end to end. Report shows 2 kept, 0 discarded, coverage climbs from BEFORE to AFTER, `stop_reason` is set.
  - Commit: `"feat: happy-path loop with coverage climb on fixture"`.

- [ ] **8b. Repair loop and budget.**
  - Does: extend `loop.py` to wire the repair path (on runner failure, call `repair_test` up to `--max-repairs`), and introduce `budget.py` — a tracker that stops the loop before exhausting the free-tier daily cap. Implements all stop conditions: target coverage reached, `max-iters` hit, coverage stalled across K consecutive targets, budget signalled.
  - Files: `src/reflecta/loop.py` (extended), `src/reflecta/budget.py`, `tests/test_loop_budget.py`.
  - Verify: (1) mocked repair that fixes on attempt 2 → target kept. (2) mocked repair that never fixes → loop continues to next target, failed target logged. (3) budget exhausted mid-loop → stops cleanly with `stop_reason="budget"`. (4) `max-iters=2` → stops after 2, not 3.
  - Commit: `"feat: repair loop and budget tracking in main loop"`.

- [ ] **9. CLI and run report.**
  - Does: `reflecta run/clean/report` via typer; writes `reflecta-report.json` and prints a readable summary (before/after, kept, discarded, repairs used, stop reason).
  - Files: `src/reflecta/cli.py`, `src/reflecta/report.py`, `tests/test_cli.py`.
  - Verify:
    - `reflecta run --path examples/sample_project --max-iters 1` completes end to end from a clean state in under 2 minutes.
    - `reflecta clean` sub-verify: set up a fixture with both `tests/_reflecta/test_reflecta_calc_0.py` (generated) and `tests/test_calc.py` (human-written). Run `reflecta clean`. Confirm only the `_reflecta` file is removed; `tests/test_calc.py` is untouched. This is a hard rule — a bug here deletes human tests.
    - `reflecta report --last` reprints the JSON report from the previous run without re-running.
  - Commit: `"feat: CLI with run/clean/report commands"`.

---

## Phase 6 — Harden (each its own session)

- [ ] **10. Edge cases.**
  Handle and add regression tests for each of the following enumerated cases:
  - Empty repo: no Python source files found → exits cleanly with `stop_reason="no_targets"`.
  - No existing tests: coverage from package import only, starts at ~0% → loop proceeds normally from zero.
  - Broken/un-importable target file: `SyntaxError` or `ImportError` in the target module → target marked `failed`, loop continues with next target, error logged.
  - Target with no testable surface: all side effects, no pure functions, no introspectable callables → target marked `discarded`, loop continues.
  - Hanging generated test: subprocess timeout fires, process killed → `RunResult.passed=False`, traceback contains "timeout", enters repair path.
  - Missing `.env` or unset API key: user sees a clear `EnvironmentError` naming the missing variable, not a raw traceback from the SDK.
  - Fixture with zero coverage gap: all lines covered → `stop_reason="no_targets"` immediately, no LLM calls made.
  - Gemini returns syntactically invalid Python: `ast.parse` fails → treated as a generation failure, enters repair path; if repair also fails → target marked `failed`.
  - Commit per case or per batch of related cases.

- [ ] **11. Free-tier resilience:** exponential backoff on 429s for both providers (already stubbed in Task 2.5 — full budget tracker here); a budget tracker that stops before the daily cap; graceful fallback when one provider is exhausted.
- [ ] **12. Isolation:** subprocess + timeout for every generated test; run the target suite against a temp copy so a bad test cannot corrupt the working tree.
- [ ] **13. Secrets pass:** no keys in repo, logs, or report. Confirm `.env` is gitignored.
- [ ] **14. Gate stress test (the honesty pass):** adversarial generated tests (assertion-free, trivially-true, import-only-to-bump-coverage). Confirm the gates reject all of them. Add as regression tests.
- [ ] **15. Tidy:** code-review subagent for dead code and inconsistent patterns; fix what matters, log the rest as v2.

---

## Phase 7 — Ship

- [ ] **16. README** drafted from `SPEC.md`: what it is, two-key free setup, install, one-command demo on the bundled sample, a GIF of coverage climbing.
- [ ] **17. Package:** clean `pyproject.toml`, publish to PyPI or pipx-from-GitHub. Publish command stays behind manual confirmation.
- [ ] **18. Clean-clone smoke test** with only the two free keys set.
- [ ] **19. Real-repo demo:** run on 2-3 of your own repos (LeaseGuard is a candidate), capture real before/after numbers for the README.
- [ ] **20. Tag `v0.1.0`.**

---

## v2 backlog (Phase 8, same mini-loop each)

- [ ] Claude Agent SDK escalation for `escalated` targets (real bash + edit tools, its own bounded loop).
- [ ] Mutation testing as a stronger quality signal than line coverage.
- [ ] Branch-coverage targeting, not just lines.
- [ ] An eval harness: fixed targets with known gaps, measure coverage gained / accepted / rejected / repairs used on every prompt or routing change.
- [ ] Parallel targets via git worktrees.
- [ ] CI integration: open a PR with accepted tests.
- [ ] Config file (`reflecta.toml`).
- [ ] Other languages (JS/Jest, Go).
