# PLAN.md — reflecta

Ordered build tasks. Vertical slices, riskiest assumption first, something runs end to end from task 0. Each task lists what it does, which files it touches, and how to verify. Check items off as you go so progress survives `/clear`. One task per session. Plan Mode for anything non-trivial.

Legend: `[ ]` todo, `[~]` in progress, `[x]` done.

---

## Phase 4 — Walking skeleton

- [x] **0. End-to-end with everything faked but the risky part.**
  - Does: builds the fixture `examples/sample_project/calc.py` (3 small functions) with a partial test leaving one known gap. Runs `coverage run -m pytest && coverage json`. Calls `extract_targets` against the real `coverage.json` (not hardcoded line numbers) to confirm the fixture's missing lines come out correctly — this removes one variable when debugging a Gemini failure. Then makes ONE real Gemini Flash call to write a test into `tests/_reflecta/test_reflecta_calc_0.py`, reruns coverage, prints `coverage: BEFORE% -> AFTER%`.
  - Files: `examples/sample_project/`, `src/reflecta/skeleton.py` (removed in the 0–9 hardening pass — see `docs/HARDENING-0-9.md` §4.1), `src/reflecta/llm/gemini.py` (minimal).
  - Verify: (1) `extract_targets` returns the expected `CoverageTarget` for the fixture's known gap. (2) `ast.parse(gemini_output)` succeeds — i.e., Gemini's raw response is syntactically valid Python before the test is even run. (3) The printed AFTER number is strictly higher than BEFORE on a real run. If `ast.parse` fails or the coverage number does not move, stop and revise the prompt before building anything else.
  - Commit: `"walking skeleton: gemini-written test moves real coverage"`.

- [x] **0.5. Shared data model.**
  - Does: defines all dataclasses in one canonical file — `CoverageTarget`, `GeneratedTest`, `RepairAttempt`, `RunReport` — so every subsequent task imports from the same place and there are no ad-hoc or duplicate definitions scattered across modules.
  - Files: `src/reflecta/models.py`, `tests/test_models.py` (light: instantiate each dataclass, assert field names and defaults).
  - Verify: `from reflecta.models import CoverageTarget, GeneratedTest, RepairAttempt, RunReport` works from any module. No task after this point defines its own version of these types.
  - Commit: `"feat: canonical data model in models.py"`.

---

## Phase 5 — Vertical slices

- [x] **1. Real coverage-gap extraction.**
  - Does: `extract_targets(coverage_json, repo_path)` parses `missing_lines` per file into `CoverageTarget` objects, mapping line ranges back to enclosing function/method names via the source AST.
  - Files: `src/reflecta/coverage_report.py`, `tests/test_coverage_report.py`.
  - Verify (test-first): feed a saved sample `coverage.json` + source, assert the targets and their `qualified_name`s come out right.
  - Commit: `"feat: coverage-gap extraction with AST line mapping"`.

- [x] **2. Target selection and ranking.**
  - Does: `select_next(targets)` ranks by a simple priority (more missing lines and simpler signatures first) and returns the next pending target.
  - Files: `src/reflecta/selection.py`, `tests/test_selection.py`.
  - Verify (test-first): given a target list, assert ordering; assert `None` when all are non-pending.
  - Commit: `"feat: target selection and priority ranking"`.

- [x] **2.5. LLM provider wrapper.**
  - Does: a thin wrapper around both provider clients (`gemini.py`, `groq.py`) that enforces exponential backoff on 429 responses and raises a `BudgetExhausted` exception when the retry ceiling is hit. All LLM calls in Tasks 3 and 6 go through this wrapper. Without it, 429s during development are indistinguishable from bad prompts.
  - Files: `src/reflecta/llm/provider.py`, `tests/test_provider.py`.
  - Verify (test-first): mock HTTP 429 three times then success → retries with backoff, returns response. Mock 429 beyond max-retries → raises `BudgetExhausted`. Mock 200 → returns immediately, no delay.
  - Commit: `"feat: LLM provider wrapper with exponential backoff"`.

- [x] **3. Gemini generation with a real prompt.**
  - Does: `generate_test(target, source, existing_tests)` builds a prompt with the full source module, existing tests, and the exact missed lines, calls Gemini Flash via the provider wrapper, extracts a clean pytest file, writes it to the `_reflecta` path. The per-module counter is determined at write time by scanning existing `_reflecta/test_reflecta_<module>_*.py` files — no manifest needed, no collisions across runs.
  - Files: `src/reflecta/generate.py`, `src/reflecta/llm/gemini.py`, `src/reflecta/prompts.py`, `tests/test_generate.py`.
  - Prompt iteration note: the first prompt will likely produce syntactically invalid output or wrong imports on some inputs. The verify step requires 3 distinct fixture inputs to all produce `ast.parse`-valid Python before this task is considered done. If fewer than 3 of 3 pass, iterate the prompt template.
  - Verify: (1) with a mocked Gemini client, assert the prompt contains the missed lines and the file is written to the right path. (2) One live smoke test behind a `@pytest.mark.live` marker — run against 3 different `calc.py` gap shapes, all must return `ast.parse`-valid Python.
  - Commit: `"feat: Gemini test generation with prompt and file writer"`.

- [x] **4. Assertion gate.**
  - Does: `passes_assertion_gate(test)` parses the test AST and rejects zero-assertion tests and trivially-true assertions (`assert True`, `assert 1 == 1`, asserting a literal against itself) before the test is run. This gate fires upstream of `run_test` in the loop; placing it here ensures the runner in Task 5 never receives a gateless test.
  - Files: `src/reflecta/gates.py`, `tests/test_gates_assertion.py`.
  - Verify (test-first): assertion-free test → rejected; `assert True` → rejected; `assert 1 == 1` → rejected; `assert add(2, 3) == 5` → accepted.
  - Commit: `"feat: AST-based assertion gate"`.

- [x] **5. Test execution and traceback capture.**
  - Does: `run_test(test_file, repo_path, timeout_s)` runs only the new test in a subprocess, returns `RunResult{passed, traceback, duration}`. Per-test timeout.
  - Files: `src/reflecta/runner.py`, `tests/test_runner.py`.
  - Verify (test-first): a known-passing fixture test returns passed; a known-failing one returns the traceback; a hanging one is killed at the timeout.
  - Commit: `"feat: subprocess test runner with timeout and traceback capture"`.

- [x] **6. Groq repair loop with attempt ceiling.**
  - Does: on failure, `repair_test(test, result, source)` sends the failing test + traceback + source to Groq via the provider wrapper, gets a patched test, reruns. Repeats up to `--max-repairs` (the 2-failure rule). On exhaustion, marks target `failed` (v1) or `escalated` (v2 hook, no-op for now).
  - Files: `src/reflecta/repair.py`, `src/reflecta/llm/groq.py`, `tests/test_repair.py`.
  - Verify (test-first): mocked Groq that fixes on attempt 2 → kept; mocked Groq that never fixes → stops at the ceiling, target marked `failed`, no infinite loop.
  - Commit: `"feat: Groq repair loop with attempt ceiling"`.

- [x] **7. Coverage-delta gate.**
  - Does: `passes_delta_gate(before, after)`. Integrate it so a passing test is kept only if total coverage strictly rose; otherwise the generated file is deleted and the target marked `discarded`.
  - Files: `src/reflecta/gates.py`, `src/reflecta/loop.py` (stub), `tests/test_gates_delta.py`.
  - Verify (test-first): passing test that moves coverage → kept and file remains; passing test that does not → `discarded` and file removed.
  - Commit: `"feat: coverage-delta gate with file cleanup on discard"`.

- [x] **8a. Happy-path loop.**
  - Does: `loop.py` wires the happy path: extract → select → generate → assertion gate → run → delta gate → keep/discard → select next, until all targets are exhausted or `max-iters` is hit. No repair, no budget yet. Populates `RunReport` with kept/discarded counts and stop reason.
  - Files: `src/reflecta/loop.py`, `tests/test_loop_happy.py`.
  - Verify: run against the fixture with 2 targets, both succeed end to end. Report shows 2 kept, 0 discarded, coverage climbs from BEFORE to AFTER, `stop_reason` is set.
  - Commit: `"feat: happy-path loop with coverage climb on fixture"`.

- [x] **8b. Repair loop and budget.**
  - Does: extend `loop.py` to wire the repair path (on runner failure, call `repair_test` up to `--max-repairs`), and introduce `budget.py` — a tracker that stops the loop before exhausting the free-tier daily cap. Implements all stop conditions: target coverage reached, `max-iters` hit, coverage stalled across K consecutive targets, budget signalled.
  - Files: `src/reflecta/loop.py` (extended), `src/reflecta/budget.py`, `tests/test_loop_budget.py`.
  - Verify: (1) mocked repair that fixes on attempt 2 → target kept. (2) mocked repair that never fixes → loop continues to next target, failed target logged. (3) budget exhausted mid-loop → stops cleanly with `stop_reason="budget"`. (4) `max-iters=2` → stops after 2, not 3.
  - Commit: `"feat: repair loop and budget tracking in main loop"`.

- [x] **9. CLI and run report.**
  - Does: `reflecta run/clean/report` via typer; writes `reflecta-report.json` and prints a readable summary (before/after, kept, discarded, repairs used, stop reason).
  - Files: `src/reflecta/cli.py`, `src/reflecta/report.py`, `tests/test_cli.py`.
  - Verify:
    - `reflecta run --path examples/sample_project --max-iters 1` completes end to end from a clean state in under 2 minutes.
    - `reflecta clean` sub-verify: set up a fixture with both `tests/_reflecta/test_reflecta_calc_0.py` (generated) and `tests/test_calc.py` (human-written). Run `reflecta clean`. Confirm only the `_reflecta` file is removed; `tests/test_calc.py` is untouched. This is a hard rule — a bug here deletes human tests.
    - `reflecta report --last` reprints the JSON report from the previous run without re-running.
  - Commit: `"feat: CLI with run/clean/report commands"`.

---

## Hardening pass on tasks 0–9 (2026-05-31)

A senior-review remediation of the completed 0–9 slices landed before Phase 6
(13 commits, branch `fix/hardening-0-9` merged to `main`). Full detail and
rationale: [`docs/HARDENING-0-9.md`](docs/HARDENING-0-9.md). Highlights:
repair runs with `repo_path` cwd; `sys.executable` for subprocesses; generated
tests run with `*_API_KEY` scrubbed from their env; correct import paths for
class methods / packaged modules; per-target error isolation; the two missing
SPEC stop conditions (`target_coverage`, `stall_k`); `.env` preflight; existing
tests fed into the prompt; coverage isolated to `.reflecta/`; structured logging
+ `run --verbose`; new CLI flags `--max-llm-calls/--target-coverage/--stall-k`.
This partially anticipates Phase 6 items 11–13 but does not close them — the
full edge-case matrix (10), provider fallback (11), temp-tree isolation (12),
and the gate stress test (14) remain open below.

---

## Phase 6 — Harden (each its own session)

- [x] **10. Edge cases.**
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

- [x] **11. Free-tier resilience:** exponential backoff on 429s for both providers (already stubbed in Task 2.5 — full budget tracker here); a budget tracker that stops before the daily cap; graceful fallback when one provider is exhausted.
- [x] **12. Isolation:** subprocess + timeout for every generated test; run the target suite against a temp copy so a bad test cannot corrupt the working tree.
- [x] **13. Secrets pass:** no keys in repo, logs, or report. Confirm `.env` is gitignored.
- [x] **14. Gate stress test (the honesty pass):** adversarial generated tests (assertion-free, trivially-true, import-only-to-bump-coverage). Confirm the gates reject all of them. Add as regression tests.
- [x] **15. Tidy:** code-review subagent for dead code and inconsistent patterns; fix what matters, log the rest as v2.

---

## Phase 7 — Ship

- [x] **16. README** drafted from `SPEC.md`: what it is, two-key free setup, install, one-command demo on the bundled sample, a GIF of coverage climbing.
- [x] **17. Package:** clean `pyproject.toml`, publish to PyPI or pipx-from-GitHub. Publish command stays behind manual confirmation.
- [x] **18. Clean-clone smoke test** with only the two free keys set.
- [ ] **19. Real-repo demo:** run on 2-3 of your own repos (LeaseGuard is a candidate), capture real before/after numbers for the README. **[in progress]** — current activity is manual testing on the operator's own repositories in **direct (BYO-key) mode** (see Session status below).
  - [x] **19a. Cross-repo robustness (2026-06-03):** fixed the "always fails on any non-example repo" bug. Was reflecta, not the target. Multi-block `strip_fences`; new `validation.py` (reject empty/no-test/missing-import drafts, regenerate once, else `SKIPPED`); new `environment.py` (target-venv auto-detect + `find_spec` import preflight); `runner` exit-code classification (`RunResult.failure_kind`); entrypoint detection + skip (`--skip-entrypoints`); utf-8 test writes; CLI `--python`. Verified live on LeaseGuard: valid tests now import+run. 206 tests pass.
  - [x] **19b. Explicit error messages (2026-06-03):** `BudgetExhausted`/`RateLimitError` name the provider + HTTP 429 + raw API text + per-minute-vs-daily remedy; import_error names the missing module; expanded Stop-reason line.
  - [x] **19c. Token/TPM & HTTP 413 fix (2026-06-04):** Groq repair hit HTTP 413 "request too large" (8B prompt 8486 tok > 6000 TPM), misclassified as a retryable 429. New `llm/limits.py` (verified free-tier RPM/RPD/TPM/TPD + token budgeting); `RequestTooLarge` exception (checked before 429, never retried); `repair._budget_repair_prompt` sizes prompts to model TPM; 413 escalates 8B→70B once. Verified live on leaseguard — no more 413. 217 tests pass.
  - [x] **19d. Repair-stage rate limit stops the run cleanly** (was optional follow-up) — both generation- and repair-stage `BudgetExhausted` now stop with `stop_reason=budget`.
  - [x] **19e. Static testability triage (2026-06-04):** new `testability.py` classifies every target (AST only, no LLM) as testable/risky/blocked. `run_loop` skips blocked (always) + risky (default) before any provider call; stops with `no_testable_targets` if nothing is attemptable. New `reflecta triage --path` and `run --dry-run` give a zero-quota preview; `--attempt-risky` overrides. Verified on leaseguard: 74 testable attempted, 29 risky + 8 entrypoints skipped, no quota spent. 234 tests pass.
  - [ ] **19f.** Capture before/after coverage numbers for the README — now that triage targets the 74 unit-testable functions, run with adequate `--max-iters` and grab the delta. Watch the Gemini daily RPD=250 cap.
- [x] **20. Tag `v0.1.0`.**

---

## Phase 8 — v2 Features

- [x] **21. Claude Agent SDK escalation.**
  - Does: when Groq repair exhausts `--max-repairs` attempts, passing `--escalate` hands the target to `escalate.py` — a Claude Opus tool-use loop with `read_file`, `write_test`, and `run_test` tools. Bounded by `--max-claude-iters` (default 3). On failure the target is marked `ESCALATED` (distinct from `FAILED`). Report tracks `escalations_attempted` and `escalations_succeeded`. Opt-in dep: `pip install reflecta[escalation]`. Requires `ANTHROPIC_API_KEY`.
  - Files: `src/reflecta/escalate.py` (new), `src/reflecta/models.py`, `src/reflecta/loop.py`, `src/reflecta/cli.py`, `src/reflecta/config.py`, `pyproject.toml`, `tests/test_escalate.py` (new, 10 tests), `tests/test_loop_escalation.py` (new, 5 tests).
  - Verify: 14 new tests pass; full suite 145/145 green.
  - Commit: `"feat: Claude Agent SDK escalation for stuck targets"`.

- [x] **21a. Escalation timeout hardening.**
  - Problem: `escalate_target` hung indefinitely on Windows after printing `[live] → sending request to Claude API...`. Three compounding causes: (1) httpx `read_timeout` is a per-chunk deadline, not a total-response deadline — a slow server that trickles bytes never triggers it; (2) the SDK default `max_retries=2` silently retried on timeout, multiplying the wait; (3) Windows TLS socket timeouts are unreliable under httpx.
  - Fix: wrap every `messages.create()` call in `concurrent.futures.ThreadPoolExecutor` with `future.result(timeout=55)` — a Python-level deadline that is always honoured regardless of socket/httpx behaviour. Also set `max_retries=0` on the Anthropic client so retries can't amplify the wait.
  - Files: `src/reflecta/escalate.py` (`_timed_create` helper, updated client creation), `tests/test_escalate.py` (updated `_TracingClient`).
  - Verify: `pytest -x -q` → 145/145 green.
  - Commit: `"fix: hard thread-level timeout for Claude API calls on Windows"`.
  - **Superseded (commit `1b33a8c`):** the ThreadPoolExecutor wrapper was replaced
    by calling the Messages API directly over `httpx` with a single per-round-trip
    `httpx.Timeout`, which the anthropic SDK could not honour reliably. See the
    module docstring in `src/reflecta/escalate.py` for the current design.

## Production-readiness audit + hardening (2026-06-01)

A principal-engineer audit of tasks 0–21 found one critical isolation flaw plus
several correctness and hygiene issues. Findings, evidence, and the phased
remediation plan: [`docs/AUDIT-PRODUCTION-READINESS.md`](docs/AUDIT-PRODUCTION-READINESS.md).

- [x] **Phase A — safety blockers.** Coverage measurement isolated + time-boxed
  (`measure_coverage_isolated`) so generated tests can't corrupt/wedge the real
  tree (C1); suite-breaking tests discarded, not kept (H2); per-file parse
  guarded so one broken file can't abort the run (H1); escalation path check
  uses `is_relative_to` (C2).
- [x] **Phase B — robustness.** Escalation/`httpx` lazy-imported off the core
  path (H3); `EmptyResponse` on None/empty LLM output (M2); escalation counts
  round-trip in the report + summary (M1); budget scope documented — free-tier
  only, Claude is separate (M3); defensive `end_lineno` (M4).
- [x] **Phase C — hygiene.** ruff clean; stray `.omc/` untracked + gitignored;
  `report --last` honoured; PLAN backlog de-duped; dependency version bounds;
  `clean` output reports workspace removal.
- Deferred to v2 (perf, no action): per-iteration repo copy cost (S1), full-suite
  measurement cost (S2).
- Suite after hardening: 155 passing, `ruff` clean.

## Remote key-broker mode (2026-06-02)

Turn reflecta into a product that runs on the **operator's** keys instead of
each user bringing their own. Architecture + rationale:
[`docs/REMOTE-MODE.md`](docs/REMOTE-MODE.md); proxy service:
[`proxy/README.md`](proxy/README.md).

- [x] **Client remote mode.** `src/reflecta/llm/remote.py` routes Gemini/Groq
  calls through a hosted proxy when a reflecta token is configured
  (`REFLECTA_TOKEN` env or `~/.reflecta/credentials` via `reflecta login`).
  Provider SDKs are lazy-imported; user code still runs entirely locally.
  Precedence: token → remote mode; else provider keys → direct mode; else clear
  error. New CLI `login`/`logout`; `config.require_credentials` is mode-aware;
  `httpx` promoted to a core dependency. Tests: `tests/test_remote.py` (+ CLI).
- [x] **Proxy service.** `proxy/` — standalone FastAPI broker (one
  `/v1/complete` endpoint + `/healthz`): bearer-token auth, per-token daily
  quota (429 over cap), model allowlist, prompt-size cap, forwards to providers
  on the operator's keys. Never receives/runs user code. Dockerfile, README,
  `.env.example`, 12 tests (providers stubbed).
- Suite after this work: 171 (package) + 12 (proxy), `ruff` clean. Merged to
  `main`.

### Operator TODO before remote mode goes live (deferred — see Session status)
- [ ] Set `DEFAULT_PROXY_URL` in `src/reflecta/llm/remote.py` to the deployed URL.
- [ ] Stand up the proxy on a host (Render/Fly/Railway) with `GEMINI_API_KEY`,
      `GROQ_API_KEY`, `REFLECTA_TOKENS` set; use a **paid, no-train** provider tier.
- [ ] Issue tokens to users; verify end-to-end (`/healthz` + a real run).
- [ ] Production hardening (per `proxy/README.md`): persistent metering
      (Redis/DB), token DB with revocation, billing, rate limits, ToS/privacy.

## Session status (2026-06-02)

- Remote key-broker mode is **built and merged** but **not yet deployed** — the
  operator will set up the proxy later (see Operator TODO above).
- **Current working mode: direct / BYO-key**, used for **manual testing on the
  operator's own repositories** (task 19). No proxy needed for this; set
  `GEMINI_API_KEY` + `GROQ_API_KEY` (or a `.env`) and run
  `reflecta run --path . -v`. Run reflecta inside the target repo's own venv so
  its suite runs under coverage; a baseline of `0.0%` means the suite didn't run.
- Suggested next step once real-repo numbers exist: an **eval harness** (v2
  backlog) to measure prompt/routing changes objectively.

## v2 backlog (remaining)

- [ ] Mutation testing as a stronger quality signal than line coverage.
- [ ] Branch-coverage targeting, not just lines.
- [ ] An eval harness: fixed targets with known gaps, measure coverage gained / accepted / rejected / repairs used on every prompt or routing change.
- [ ] Parallel targets via git worktrees.
- [ ] CI integration: open a PR with accepted tests.
- [ ] Config file (`reflecta.toml`).
- [ ] Other languages (JS/Jest, Go).
