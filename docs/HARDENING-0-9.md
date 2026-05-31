# HARDENING-0-9 — Bringing Tasks 0–9 to Production Quality

**Author:** senior-engineer review pass
**Date:** 2026-05-31
**Scope:** Tasks 0–9 are implemented and the suite is green (67 passed, 1 deselected). This document is a defect-and-improvement audit of what was actually built, not what the PLAN claims. Each finding has a severity, the evidence (`file:line`), the failure mode, and a concrete fix. The intent is that a fresh engineer can pick up any item and close it without re-deriving the analysis.

Legend: **S1** = correctness bug or security hole that ships wrong/unsafe behavior. **S2** = will break on realistic inputs beyond the bundled fixture. **S3** = maintainability / dead code / polish.

---

## 0. Executive summary

The walking skeleton and the happy path work **on the bundled `calc.py` fixture**. The risk is that several behaviors are fixture-shaped: they pass because `calc.py` is a flat top-level module with only free functions and a partial test. The moment reflecta is pointed at (a) a class method, (b) a packaged module, (c) a repo whose `python` on PATH differs from the venv, or (d) a 429 storm, it produces wrong results or crashes. Three items are genuine integrity/security risks (untrusted-code execution in-tree, env/secret exposure to generated tests, repair running in the wrong directory). Two PLAN-claimed stop conditions are **not implemented at all**.

Priority order to fix: **S1 group first** (§1), then **the two missing stop conditions and the .env gap** (§2), then **generalization beyond the fixture** (§3), then **cleanup** (§4).

---

## 1. S1 — Correctness & security defects (fix before any real-repo run)

### 1.1 Repair runs the test in the wrong working directory
**Evidence:** `src/reflecta/repair.py:35` — `run_test(test.test_file_path, test.test_file_path.parent)`.
**Problem:** Every other call site runs tests with `cwd=repo_path` (`loop.py:117`). Repair instead sets cwd to `tests/_reflecta/`. Import resolution (`from calc import add`) depends on cwd-derived `sys.path[0]`, so a repaired test is validated under a *different* import environment than the original. A repair can spuriously pass (wrong module shadowed) or spuriously fail (target not importable from `_reflecta/`), and the subsequent `measure_coverage` (run at `repo_path`) disagrees with what repair just "verified."
**Fix:** Thread `repo_path` into `repair_test` and use it:
```python
def repair_test(test, result, source, *, repo_path: Path, max_repairs=2, groq_client=None):
    ...
    run_result = run_test(test.test_file_path, repo_path)
```
Update the one call in `loop.py:127` to pass `repo_path=repo_path`. Add a regression test asserting repair invokes `run_test` with `repo_path`, not the file's parent.

### 1.2 LLM-generated tests execute in the working tree with the full parent environment
**Evidence:** `runner.py:10-16` (`subprocess.Popen(..., cwd=repo_path)` with inherited `env`), `generate.py:42` writes straight into `repo_path/tests/_reflecta/`.
**Problem:** reflecta executes code written by a free-tier LLM, in the user's repo, with the user's environment — which contains `GEMINI_API_KEY` and `GROQ_API_KEY`. A generated (or prompt-injected) test can read `os.environ`, exfiltrate keys, or touch the filesystem. PLAN defers full isolation to Task 12, but Tasks 0–9 already *ship the execution path*, so the minimum mitigations belong here:
- **Scrub secrets from the child env.** Pass an explicit `env` to `Popen`/`coverage run` that strips `*_API_KEY` (and ideally allowlists only `PATH`, `SYSTEMROOT`, `PYTHONPATH`, venv vars).
- **Run from a temp copy** (Task 12, but pull the secret-scrub forward now — it is two lines and removes the worst outcome).
**Fix sketch:**
```python
import os
def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
# Popen(..., env=_child_env())
```

### 1.3 Class-method and packaged-module targets generate broken imports
**Evidence:** `prompts.py:29` — `from {qualified_name.split('.')[0]} import {qualified_name.split('.')[-1]}`.
**Problem:** For a method target `Calc.add`, this emits `from Calc import add` — but `Calc` is a class, not a module, so the import is unsatisfiable and *every class-method target fails generation*. For a packaged module `pkg/sub/mod.py`, `extract_targets` stores `file_path` but the import line only ever uses the bare stem (`from mod import ...`), which fails unless `mod` happens to be on `sys.path` root. The fixture hides this because `calc.py` is a flat module of free functions.
**Fix:** Derive a real import path from `target.file_path` relative to `repo_path`, and import the class for methods:
- Compute module dotted path from the file's location relative to the repo root (and any `src/` layout).
- For `Class.method`, instruct the prompt to `from <module> import <Class>` and exercise `<Class>(...).method(...)`.
- Add three generation tests: free function, classmethod/instance method, and a `src/pkg/mod.py`-nested module — assert the emitted import is importable.

### 1.4 `python` invoked instead of `sys.executable`
**Evidence:** `runner.py:11` and `loop.py:20,25` use the literal `"python"`.
**Problem:** On Windows and in any venv where the active interpreter is not the first `python` on PATH, the child process is a *different* Python that may lack `pytest`, `coverage`, or the project deps — so every generated test "fails" (or coverage can't run) for environmental reasons unrelated to the test. Silent because `capture_output=True`.
**Fix:** Use `sys.executable` everywhere a subprocess Python is launched (`runner.py`, `loop.measure_coverage`). One-line change in three places; add a test asserting the runner command starts with `sys.executable`.

### 1.5 Rate-limit exhaustion crashes the run instead of stopping cleanly
**Evidence:** `provider.py:17-19` raises `BudgetExhausted` on retry-ceiling; the loop (`loop.py`) never catches it. The loop's only "budget" handling is the call-count `BudgetTracker`, a different mechanism.
**Problem:** PLAN/SPEC promise `stop_reason="budget"` when the free tier signals it is near the cap. A 429 storm today propagates `BudgetExhausted` out of `generate_test`/`repair_test` and aborts `run_loop` with a traceback and **no report written** — the opposite of the spec's "always write the report on stop."
**Fix:** Wrap the generate/repair calls; on `BudgetExhausted`, set `report.stop_reason = "budget"`, break, and fall through to the single report-write path. Unify the two notions: a provider `BudgetExhausted` and the `BudgetTracker.exhausted()` ceiling should both land on the same stop branch.

### 1.6 Any exception in generation/repair aborts the whole run
**Evidence:** `loop.py:101-108` calls `generate_test` with no `try/except`; same for `run_test`/`repair_test`.
**Problem:** One un-importable target file (`SyntaxError`/`ImportError`), one transient SDK error, or one malformed Gemini response throws and kills the loop — losing all prior kept tests' bookkeeping and writing no report. PLAN Task 10 enumerates these, but the loop has no per-target guard, so even today's happy path is one bad target away from a crash.
**Fix:** Wrap the per-target body in `try/except Exception`, mark the target `FAILED`, log, `continue`. Keep `BudgetExhausted` as the one exception that breaks (not continues). This also satisfies several Task 10 cases for free.

---

## 2. S1/S2 — Spec conformance gaps (claimed done, not implemented)

### 2.1 Two of four stop conditions are missing
**Evidence:** `SPEC.md:111` lists four stop conditions; PLAN 8b says "Implements all stop conditions." The loop (`loop.py:87-155`) implements only **`max_iters`**, **`budget`** (call-count), and **`exhausted`**. Missing:
- **Target coverage reached** — there is no `target_coverage` parameter anywhere; the loop never checks `coverage_after >= target`.
- **Coverage stalled across K consecutive targets** — no stall counter exists.
**Fix:**
- Add `target_coverage: float | None` and `stall_k: int = 3` params to `run_loop` and CLI. After each kept/discarded outcome: if `coverage_after >= target_coverage` → `stop_reason="target_reached"`, break. Track consecutive non-improving targets; at `>= stall_k` → `stop_reason="stalled"`, break.
- Add `test_loop_budget`-style tests for both (target reached after N keeps; K discards in a row stops).

### 2.2 `.env` is never loaded; missing keys raise a raw `KeyError`
**Evidence:** CLAUDE.md hard rule 5 + Task 10 promise a clear `EnvironmentError`. But no module calls `load_dotenv`; `gemini.py:23` / `groq.py:24` do `os.environ["..._API_KEY"]`. The only `.env` reader is the dead `skeleton.py:23`.
**Problem:** A user who set keys in `.env` (as the README instructs) gets a bare `KeyError: 'GEMINI_API_KEY'` from deep in the SDK construction — exactly the failure Task 10 says to prevent.
**Fix:** Load `.env` once at CLI entry (`cli.py`) — either `python-dotenv`'s `load_dotenv()` or promote `skeleton._load_dotenv` into a small `config.py`. Add an explicit preflight in `run` that checks both keys and raises `EnvironmentError("GEMINI_API_KEY is not set; add it to .env")`. Test by clearing env and asserting the message names the variable.

### 2.3 `existing_tests` is always empty — the "don't duplicate" context is dead
**Evidence:** `loop.py:99` hardcodes `existing_tests = ""`; the prompt branch in `prompts.py:19-23` is therefore never exercised in real runs.
**Problem:** Gemini regenerates tests for surface a human test already covers; those tests don't raise coverage → discarded → wasted LLM budget (and the budget is the scarce resource). Also raises the odds of name collisions / redundant asserts.
**Fix:** Collect existing test sources for the target module (human tests under `tests/`, excluding `_reflecta/`) and pass them in. Cap the size to protect the context window.

---

## 3. S2 — Generalization & robustness (beyond the fixture)

### 3.1 `measure_coverage` overwrites the user's `coverage.json` and re-runs the entire suite every iteration
**Evidence:** `loop.py:16-33` writes `repo_path/coverage.json` and runs the **full** `pytest` suite once per target.
**Problems:** (a) Side-effect: clobbers a file the user may rely on (it *is* gitignored in this repo, but reflecta runs against *other* repos). (b) Cost: O(targets × full-suite-runtime); on a repo with a 30 s suite and 20 targets that is 10 minutes of pure re-runs. (c) Uses the target repo's coverage config implicitly — if `[run] source` is unset, the totals measure only imported files and the delta is noisy.
**Fix:** Write coverage data to a temp file (`coverage json -o <tmp>`), pin `--data-file`/`--rcfile` so reflecta controls the config, and measure with an explicit `source` = the repo's package(s). Consider measuring the delta only for the target file when attributing a keep.

### 3.2 Module-level coverage gaps produce no targets
**Evidence:** `coverage_report.py:58-78` only emits targets for lines inside a `FunctionDef`/`AsyncFunctionDef`. Missing lines at module scope (guards, constants, `if __name__` blocks) are silently dropped.
**Impact:** Acceptable for v1, but it should be a *documented* limitation and ideally a `module:<file>` pseudo-target rather than an invisible drop, so coverage that can never move doesn't masquerade as "exhausted."
**Fix:** Document in `extract_targets` docstring; optionally emit a low-priority module-level target.

### 3.3 `read_report` is brittle on older/partial reports
**Evidence:** `report.py:46-50` uses `data["tests_kept"]` (hard index) for several fields while using `.get(...)` for others.
**Problem:** `reflecta report --last` on a report from an earlier schema raises `KeyError` instead of degrading.
**Fix:** Use `.get` with sensible defaults uniformly, or version the report and validate.

### 3.4 Assertion gate is shallow (will matter at Task 14, worth noting now)
**Evidence:** `gates.py:10-30`. Catches `assert <const>` and `assert <const> == <const>`. It does **not** catch `assert x == x` (same name both sides), `assert bool(...)`-style always-truthy, or `assert f()` with no semantic check.
**Impact:** A generated test can pass the gate without really testing anything, then ride a coincidental coverage bump into "kept." This is the product's honesty surface.
**Fix (defer to Task 14 but record):** add same-operand compare detection (`ast.dump(left) == ast.dump(comparator)`), and require at least one assert whose operands reference an imported target name.

### 3.5 No observability
**Evidence:** Every subprocess uses `capture_output=True`/`stdout=PIPE` and nothing is logged. On failure the user sees only the final summary.
**Fix:** Add a `logging` channel (and/or write per-target traces under `.omc/logs/` or `reflecta-run.log`): target chosen, model used, pass/fail, coverage delta, discard reason. This is what makes a failed real-repo run diagnosable.

---

## 4. S3 — Dead code, duplication, polish

| # | Item | Evidence | Action |
|---|------|----------|--------|
| 4.1 | `skeleton.py` (159 lines) is task-0 scaffolding, now superseded by `loop.py`+`generate.py`. Still imports the dead `generate_test_source`. | `skeleton.py:1-159` | Delete, or move to `examples/` as a demo script. Remove from package. |
| 4.2 | `gemini.generate_test_source` hardcodes `calc.py` and is unused by the loop. | `gemini.py:42-57` | Delete; the live path is `prompts.build_generation_prompt` → `gemini.generate`. |
| 4.3 | `_strip_fences` duplicated verbatim in two clients. | `gemini.py:10-18`, `groq.py:11-19` | Hoist to `llm/provider.py` (or `llm/_text.py`); import in both. |
| 4.4 | `RunReport.budget` field is written nowhere and printed nowhere. | `models.py:65`, never set | Either populate it (`f"{used}/{cap}"` from the tracker) or remove it. |
| 4.5 | `GeneratedTest.assertion_count` is always `0`; the gate re-parses instead. | `generate.py:48`, `gates.py:27` | Populate it during generation and have the gate reuse it, or drop the field. |
| 4.6 | CLI exposes no `--max-llm-calls`, so the core budget is uncontrollable from the command line (stuck at 50). | `cli.py:26-37`, `loop.py:56` | Add the option and thread it through. |
| 4.7 | `BudgetTracker.check()` (raises) is unused; the loop only calls `exhausted()`. | `budget.py:17-20` | Keep only if §1.5 unifies on it; otherwise remove to avoid two code paths. |
| 4.8 | `datetime.now()` is naive (no tz). | `loop.py:81` | Use `datetime.now(timezone.utc)`; `report._serialize` already handles isoformat. |
| 4.9 | `TargetStatus.GENERATING/REPAIRING/ESCALATED` are set inconsistently (`REPAIRING` never assigned; `ESCALATED` never used in v1). | `models.py:9-14`, `loop.py:96` | Either drive the state machine fully or trim to the states v1 actually uses. |

---

## 5. Suggested execution order (small, reviewable commits)

Each line is one branch/commit, matching the repo's commit discipline. Write the regression test first per the project's TDD rule.

1. `fix: run repaired tests with repo_path cwd` (§1.1)
2. `fix: use sys.executable for all subprocess python` (§1.4)
3. `fix: scrub *_API_KEY from generated-test child env` (§1.2 minimum)
4. `fix: derive real import path for class methods and packaged modules` (§1.3)
5. `feat: per-target try/except marks target failed and continues` (§1.6)
6. `feat: catch BudgetExhausted and stop with stop_reason=budget` (§1.5)
7. `feat: target_coverage and stall-K stop conditions` (§2.1)
8. `feat: load .env and preflight API keys with EnvironmentError` (§2.2)
9. `feat: pass existing module tests into the generation prompt` (§2.3)
10. `perf: isolate coverage data file; pin coverage source/config` (§3.1)
11. `feat: structured logging of per-target decisions` (§3.5)
12. `chore: remove skeleton.py and dead generate_test_source; hoist _strip_fences` (§4.1–4.3)
13. `chore: expose --max-llm-calls; populate/remove budget & assertion_count fields` (§4.4–4.6)

Items 1–6 are the gate to running reflecta on any repo other than the bundled fixture. Items 7–9 close the PLAN-vs-reality gaps for Tasks 8b/9. Items 10–13 are quality and cost.

---

## 6. What is genuinely good (keep)

- AST line→function mapping (`coverage_report.py`) is the right approach (no regex), and the class map is clean.
- The two-gate design (assertion *then* coverage-delta) is the correct honesty mechanism and is wired upstream of the runner as intended.
- Provider wrapper with exponential backoff is a sound seam; the dependency-injection of `gemini_client`/`groq_client` makes the loop testable without network (and the 67 tests prove it).
- Monotonic per-module counter computed at write time (`generate._next_counter`) avoids a manifest and collisions within a run.
- Report (de)serialization round-trips through a typed `read_report`, so `report --last` is real, not a cat of JSON.

The architecture is right. The defects above are about making the behavior match the architecture on inputs larger than `calc.py`.
