# PLAN.md — coverloop

Ordered build tasks. Vertical slices, riskiest assumption first, something runs end to end from task 0. Each task lists what it does, which files it touches, and how to verify. Check items off as you go so progress survives `/clear`. One task per session. Plan Mode for anything non-trivial.

Legend: `[ ]` todo, `[~]` in progress, `[x]` done.

---

## Phase 4 — Walking skeleton

- [ ] **0. End-to-end with everything faked but the risky part.**
  - Does: builds the fixture `examples/sample_project/calc.py` (3 small functions) with a partial test leaving one known gap. Runs `coverage run -m pytest && coverage json`, reads the JSON, hardcodes selection of the uncovered function, makes ONE real Gemini Flash call to write a test into `tests/_coverloop/test_coverloop_calc_0.py`, reruns coverage, prints `coverage: BEFORE% -> AFTER%`.
  - Files: `examples/sample_project/`, `src/coverloop/skeleton.py`, `src/coverloop/llm/gemini.py` (minimal).
  - Verify: the printed AFTER number is higher than BEFORE on a real run. If yes, the riskiest assumption holds. If the test fails to import or asserts nothing, stop and rethink the prompt before building anything else.
  - Commit: "walking skeleton: gemini-written test moves real coverage".

---

## Phase 5 — Vertical slices

- [ ] **1. Real coverage-gap extraction.**
  - Does: `extract_targets(coverage_json, repo_path)` parses `missing_lines` per file into `CoverageTarget` objects, mapping line ranges back to enclosing function/method names via the source AST.
  - Files: `src/coverloop/coverage_report.py`, `tests/test_coverage_report.py`.
  - Verify (test-first): feed a saved sample `coverage.json` + source, assert the targets and their `qualified_name`s come out right.

- [ ] **2. Target selection and ranking.**
  - Does: `select_next(targets)` ranks by a simple priority (more missing lines and simpler signatures first) and returns the next pending target.
  - Files: `src/coverloop/selection.py`, `tests/test_selection.py`.
  - Verify (test-first): given a target list, assert ordering; assert `None` when all are non-pending.

- [ ] **3. Gemini generation with a real prompt.**
  - Does: `generate_test(target, source, existing_tests)` builds a prompt with the full source module, existing tests, and the exact missed lines, calls Gemini Flash, extracts a clean pytest file, writes it to the `_coverloop` path with a non-colliding name.
  - Files: `src/coverloop/generate.py`, `src/coverloop/llm/gemini.py`, `src/coverloop/prompts.py`, `tests/test_generate.py`.
  - Verify: with a mocked Gemini client, assert the prompt contains the missed lines and the file is written to the right path. One live smoke test behind a marker.

- [ ] **4. Test execution and traceback capture.**
  - Does: `run_test(test_file, repo_path, timeout_s)` runs only the new test in a subprocess, returns `RunResult{passed, traceback, duration}`. Per-test timeout.
  - Files: `src/coverloop/runner.py`, `tests/test_runner.py`.
  - Verify (test-first): a known-passing fixture test returns passed; a known-failing one returns the traceback; a hanging one is killed at the timeout.

- [ ] **5. Groq repair loop with attempt ceiling.**
  - Does: on failure, `repair_test(test, result, source)` sends the failing test + traceback + source to Groq, gets a patched test, reruns. Repeats up to `--max-repairs` (the 2-failure rule). On exhaustion, mark target `failed` (v1) or `escalated` (v2 hook, no-op for now).
  - Files: `src/coverloop/repair.py`, `src/coverloop/llm/groq.py`, `tests/test_repair.py`.
  - Verify (test-first): mocked Groq that fixes on attempt 2 -> kept; mocked Groq that never fixes -> stops at the ceiling, target marked failed, no infinite loop.

- [ ] **6. Assertion gate.**
  - Does: `passes_assertion_gate(test)` parses the test AST, rejects zero-assertion tests and trivially-true assertions before running.
  - Files: `src/coverloop/gates.py`, `tests/test_gates_assertion.py`.
  - Verify (test-first): assertion-free test -> rejected; `assert True` -> rejected; a real `assert add(2,3)==5` -> accepted.

- [ ] **7. Coverage-delta gate.**
  - Does: `passes_delta_gate(before, after)`; integrate it so a passing test is kept only if total coverage strictly rose, else the generated file is deleted and the target marked discarded.
  - Files: `src/coverloop/gates.py`, `src/coverloop/loop.py`, `tests/test_gates_delta.py`.
  - Verify (test-first): passing test that moves coverage -> kept and file remains; passing test that does not -> discarded and file removed.

- [ ] **8. Multi-target iteration with a budget.**
  - Does: `loop.py` ties it together: extract -> select -> generate -> gate -> run -> repair -> delta-gate -> keep/discard -> next, until target coverage, max-iters, stall, or budget. Tracks the `RunReport`.
  - Files: `src/coverloop/loop.py`, `src/coverloop/budget.py`, `tests/test_loop.py`.
  - Verify: end-to-end on the fixture, coverage climbs across several targets, stops on a real condition, report is populated.

- [ ] **9. CLI and run report.**
  - Does: `coverloop run/clean/report` via typer; writes `coverloop-report.json` and prints a readable summary (before/after, kept, discarded, repairs used, stop reason).
  - Files: `src/coverloop/cli.py`, `src/coverloop/report.py`, `tests/test_cli.py`.
  - Verify: `coverloop run --path examples/sample_project` end to end from a clean state; `coverloop clean` removes only `_coverloop` tests; `coverloop report --last` reprints.

---

## Phase 6 — Harden (each its own session)

- [ ] **10. Edge cases:** empty repo, no existing tests, broken/un-importable target file, untestable target, hanging test. Enumerate with Claude, handle, add tests.
- [ ] **11. Free-tier resilience:** exponential backoff on 429s for both providers; a budget tracker that stops before the daily cap; graceful fallback when one provider is exhausted.
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
- [ ] Config file (`coverloop.toml`).
- [ ] Other languages (JS/Jest, Go).
