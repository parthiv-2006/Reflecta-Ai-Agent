# Production-Readiness Audit — reflecta

**Date:** 2026-06-01
**Scope:** Full codebase audit of tasks 0–20 + Claude escalation (task 21/21a). Task 19 (real-repo demo) intentionally excluded.
**Reviewer role:** Principal engineer sign-off before further feature work.
**Baseline:** `pytest` → 145 passed, 2 deselected (live). `ruff check` → 4 errors. `ruff format --check` → 6 files drifted.

## Remediation status (updated 2026-06-01)

Phases A–C of the plan below are **implemented, tested, and pushed** on branch
`claude/codebase-audit-production-ready-eSekj`. Suite: 155 passed, 2 deselected
(live); `ruff check` + `ruff format --check` clean.

| ID | Severity | Status |
|----|----------|--------|
| C1 | Critical | ✅ Fixed — coverage measurement isolated + time-boxed; regression test reproduces the old hole |
| C2 | Critical | ✅ Fixed — `is_relative_to` boundary check; sibling-prefix regression test |
| H1 | High | ✅ Fixed — per-file parse guarded; broken-file regression test |
| H2 | High | ✅ Fixed — suite-breaking tests discarded; loop + isolated-measure tests |
| H3 | High | ✅ Fixed — escalation/httpx lazy-imported; core-import contract test |
| M1 | Medium | ✅ Fixed — escalation counts round-trip + shown in summary; round-trip test |
| M2 | Medium | ✅ Fixed — `EmptyResponse` on None/empty LLM output; tests for both clients |
| M3 | Medium | ✅ Resolved — budget scope documented (free-tier only; Claude separate) |
| M4 | Medium | ✅ Fixed — defensive `end_lineno` |
| L1–L7 | Low | ✅ Fixed — ruff clean, `.omc` untracked, `report --last`, PLAN de-dupe, dep bounds, clean output |
| S1–S2 | Perf | ⏳ Logged for v2 (copy cost, full-suite cost) — no action |

The phased plan and per-finding detail below are retained as the record.

## Verdict

The architecture is sound and faithful to SPEC.md: deterministic orchestration, free-stack routing intact, the two gates implemented as specified, clean module-per-concern layout. The test suite is green and broad.

**However, the application is not yet production-ready.** There is one **critical** flaw where the product's central safety guarantee — "a bad generated test cannot corrupt your working tree" — is *defeated* at the coverage-measurement step, and is actively *masked* by the very isolation that is supposed to provide it. There are also several correctness gaps (a single broken source file aborts the whole run; isolated-pass ≠ suite-pass; report round-trip data loss) and hygiene failures that break the project's own "ruff + pytest must pass" gate.

Findings are severity-ranked below with evidence (`file:line`), impact, and a concrete fix. A phased remediation plan follows.

---

## CRITICAL

### C1 — Isolation is bypassed (and masked) by `measure_coverage`; no timeout there either
**Evidence:** `src/reflecta/loop.py:37-79` (`measure_coverage`) runs `coverage run -m pytest` with `cwd=repo_path` — the **real** repository — with **no `timeout=`**. The generated `_reflecta` test physically lives in the real tree (`generate.py:94`), so it is collected and executed there. The temp-copy isolation in `runner.run_test_isolated` (`runner.py:45-77`) only guards the pass/fail *validation* run.

**Why it is worse than a simple gap:** the isolation copy *strips* `.git`, `.venv`, `.reflecta`, etc. (`runner.py:65-73`). A destructive test (`shutil.rmtree('.git')`, deleting a sibling file, etc.) is therefore a **no-op during isolated validation** (the target is absent from the stripped copy) — so it *passes* the gate — and then **executes for real** during `measure_coverage`. Isolation doesn't just fail to protect here; it hides the danger.

**Reproduced** (this audit): a generated test whose body deletes `SECRET.txt` and asserts `f(2)==3` passes `run_test_isolated` with `SECRET.txt` intact, then `measure_coverage` deletes the real `SECRET.txt`.

**Impact:** Violates the stated hard rule (CLAUDE.md #4: "Generated tests run in a subprocess with a timeout"; PLAN.md task 12: "a bad test cannot corrupt the working tree"). Arbitrary code execution against the real tree on every kept-candidate measurement. No timeout → a test that hangs only under full-suite conditions hangs the entire run forever.

**Fix:**
1. Run the post-generation coverage measurement inside an **isolated temp copy** (same copy strategy as `run_test_isolated`) that already contains the generated test, and read `coverage.json` from that copy. Only the *total* `percent_covered` delta is needed, so temp paths are fine.
2. Add an explicit `timeout=` to **both** subprocess calls in `measure_coverage`; on timeout, treat as "coverage did not rise" (discard).
3. The *initial* baseline measurement (human tests only, trusted) may stay in-tree for speed.
4. Add a regression test mirroring the reproduction above: destructive generated test → real tree untouched after a full loop iteration.

### C2 — Unsound path-traversal check in escalation `read_file`
**Evidence:** `src/reflecta/escalate.py:194` — `if not str(target).startswith(str(repo_path.resolve())):`. Prefix-string containment, not path containment: a sibling directory such as `/home/user/repo-secrets` passes the guard for repo `/home/user/repo`.

**Impact:** During `--escalate`, the Claude tool loop can read files outside the repository root (read-only, but still an exfiltration path for adjacent secrets). Lower likelihood than C1 (escalation is opt-in) but a real boundary bug.

**Fix:** Use `target.is_relative_to(repo_path.resolve())` (Python 3.11+). Add a test for the `repo` vs `repo-secrets` sibling case and for `../` traversal.

---

## HIGH

### H1 — A single unparseable source file aborts the entire run
**Evidence:** `src/reflecta/coverage_report.py:51` calls `ast.parse(source)` with no guard, inside `extract_targets`, which runs **once at loop start** (`loop.py:130`) — *outside* the per-target `try/except` (`loop.py:185-319`).

**Impact:** Directly contradicts PLAN.md task 10 ("Broken/un-importable target file → target marked `failed`, loop continues with next target"). One file with a `SyntaxError` anywhere in the repo raises out of `run_loop` before any target is attempted — the per-target isolation never gets a chance. Real repos routinely contain a vendored/partial/py2 file.

**Fix:** Wrap the per-file `ast.parse` in `try/except SyntaxError` (and `OSError` on read), log and `continue` to the next file. Add a regression test: repo with one broken file + one good file → good file's targets still extracted.

### H2 — Kept tests are validated in isolation only, never confirmed green in the full suite
**Evidence:** The keep decision rests on `run_test_isolated` (the test run **alone**, `loop.py:215`/`repair.py:41`) plus the coverage delta. `measure_coverage` runs the full suite but **discards the pytest return code** (`loop.py:46-61`). A test that passes alone can fail inside the user's full suite (fixture/state/ordering/import collisions, autouse fixtures, plugins).

**Impact:** reflecta can leave a **red test** in the user's repo and report it as "kept". That erodes the core trust contract of the tool ("the tests we keep pass"). Especially likely once generated tests accumulate across runs and interact.

**Fix:** When measuring coverage in the isolated full-suite run (see C1), also capture pass/fail. Keep a candidate only if (a) the full suite still passes (or at minimum the new test passes within it) **and** (b) coverage strictly rose. Discard otherwise. Add a regression test with an autouse-fixture collision that passes alone but fails in-suite.

### H3 — `httpx` is a hard import on the core path despite being an opt-in extra
**Evidence:** `loop.py:11` imports `escalate` unconditionally; `escalate.py:28` does `import httpx` at module top level. `pyproject.toml:41-46` declares `httpx` only under the optional `[escalation]` extra; PLAN.md task 21 calls escalation an "opt-in dep".

**Impact:** `python -m reflecta run` (no `--escalate`) imports `httpx` transitively at startup. It happens to be present (pulled by `google-genai`/`groq`), so it works today — but the dependency contract is wrong, and a future trimming of transitive deps would break the core command. The opt-in promise is not actually enforced.

**Fix:** Lazy-import `escalate_target` (and thus `httpx`) only when `escalate=True` inside `run_loop`. Either move `httpx` to core `dependencies` *or* keep it optional and guard the import with a clear "install reflecta[escalation]" message.

---

## MEDIUM

### M1 — Report round-trip silently drops escalation counts
**Evidence:** `report.py:40-51` (`read_report`) reconstructs `RunReport` without `escalations_attempted` / `escalations_succeeded` (they default to 0). `cli._print_summary` (`cli.py:14-26`) never prints them either.

**Impact:** `reflecta report --last` under-reports escalation activity; the written JSON is correct but the reprint and human summary lose it. Data-integrity / observability bug.

**Fix:** Restore both fields in `read_report`; add them to `_print_summary` (e.g. only when `escalations_attempted > 0`). Add a round-trip test asserting equality of all numeric fields.

### M2 — LLM responses assumed non-`None`
**Evidence:** `gemini.py:17` returns `response.text` (can be `None` on a safety block / empty candidate) straight into `strip_fences` (`provider.py:9` → `None.strip()` → `AttributeError`). Same shape for Groq `resp.choices[0].message.content` (`groq.py:21`).

**Impact:** Today this is swallowed by the loop's broad `except Exception` (`loop.py:313`) → target marked FAILED. Ungraceful: a safety refusal or empty completion looks identical to a code bug, wastes the target, and is invisible in logs. A `None` from Groq during *repair* (`repair.py:37`) would be written to disk as the literal text via `write_text(None)` → also `TypeError`.

**Fix:** In both clients, treat `None`/empty content as a provider failure with a clear message (`RateLimitError` if it correlates with quota, else a dedicated `EmptyResponse`/`ValueError`). Test with a mock client returning `None`.

### M3 — Escalation API calls are uncounted by the budget tracker
**Evidence:** `loop.py:248` charges only Groq repair attempts; `escalate_target` makes up to `max_claude_iters` Claude calls (`escalate.py:249-258`) with no `budget.charge`.

**Impact:** `RunReport.budget` and the `--max-llm-calls` ceiling under-count total LLM calls when escalation is on. Arguably acceptable (Claude is a separate subscription/quota), but it makes the budget figure misleading. Decide explicitly.

**Fix:** Either charge escalation iterations to a separate counter surfaced in the report, or document that `--max-llm-calls` governs free-tier (Gemini/Groq) calls only.

### M4 — `node.end_lineno` assumed non-`None`
**Evidence:** `coverage_report.py:22,60` use `node.end_lineno + 1` unguarded.

**Impact:** On CPython 3.11 these are populated for `ClassDef`/`FunctionDef`, so low risk today — but a latent `TypeError` if ever fed nodes without position info (e.g. synthetic ASTs in tests). Defensive only.

**Fix:** `(node.end_lineno or node.lineno)`.

---

## LOW / HYGIENE

### L1 — `ruff check` fails (project gate red)
4 × F401 unused imports in `tests/test_loop_escalation.py` (`SimpleNamespace`, `MagicMock`, redundant `run_loop`). CLAUDE.md requires "ruff check . && pytest — both must pass" after every task. **Fix:** `ruff check --fix .`.

### L2 — `ruff format --check` fails on 6 files
`cli.py`, `escalate.py`, `loop.py`, `tests/test_escalate.py`, `tests/test_loop_escalation.py`, `tests/test_repair.py` have drifted from `ruff format`. The PostToolUse hook formats *edited* files only. **Fix:** `ruff format .`.

### L3 — Stray agent-state file committed into the sample project
`examples/sample_project/.omc/state/last-tool-error.json` is tracked (a Windows-path, stale `coverloop` traceback from an old editor/agent session). It pollutes the published sample and leaks a local filesystem path. **Fix:** `git rm -r --cached` the `.omc/` dir, delete it, and add `.omc/` to `.gitignore`.

### L4 — `reflecta report --last` flag is dead
`cli.py:106` declares `--last` but the command ignores it (always reads the last report). Misleading. **Fix:** remove the flag, or make `report` require `--last` to reprint (and otherwise show a hint), matching SPEC.md's `reflecta report --last`.

### L5 — PLAN.md drift
v2 backlog (`PLAN.md:165-177`) duplicates five items verbatim. Task 21a documents the superseded `ThreadPoolExecutor` timeout approach, but the shipped code uses direct `httpx` with its own timeout (commit `1b33a8c`). **Fix:** de-duplicate the backlog; add a one-line note that 21a was superseded by the httpx rewrite.

### L6 — Unpinned dependencies for a published package
`pyproject.toml:23-27` lists `typer`, `google-genai`, `groq` with no version bounds (and `httpx` only in an extra). A major-version bump of any SDK can silently break installs of `reflecta 0.1.0`. **Fix:** add conservative lower bounds (and upper caps for the fast-moving SDKs), since this is tagged `v0.1.0` and intended for PyPI/pipx.

### L7 — `clean` "Nothing to clean" path can mislead
`cli.py:97` prints "Nothing to clean" only when `removed == 0 and not coverage_dir.exists()`, but after `shutil.rmtree` the dir never exists, so the message is correct only by timing. Minor cosmetic — when only `.reflecta/` existed it prints "Removed 0 generated test file(s)." instead of mentioning the workspace. **Fix:** report the workspace removal explicitly.

---

## Scalability / performance notes (not blockers, log for v2)

- **S1.** `run_test_isolated` copies the **entire repo** for every run — once per generation, once per repair attempt. On a large repo this is O(repo size × attempts) of disk churn. Consider copying only source + tests, or a COW/overlay strategy.
- **S2.** `measure_coverage` runs the **full test suite** once per kept candidate. On a repo with a slow suite this dominates runtime (O(targets × suite-time)). Acceptable for the tool's purpose; document the expectation and consider `--cov` scoping later.

---

## Remediation plan (phased)

Each item is its own branch + commit per the repo's git discipline. Run `ruff check . && ruff format --check . && pytest` before every merge.

### Phase A — Safety & correctness (must-fix before "production ready")
1. **C1** Isolate + time-box `measure_coverage` (temp-copy full-suite run; read coverage.json from the copy; timeout → discard). Regression test for the destructive-test reproduction.
2. **H2** In the same isolated full-suite run, capture pass/fail; keep only if the suite (or at least the new test within it) stays green *and* coverage rose. Regression test for in-suite-only failure.
3. **H1** Guard per-file `ast.parse` in `extract_targets`; broken file skipped + logged, loop proceeds. Regression test.
4. **C2** Replace the `startswith` path check with `is_relative_to`. Sibling + traversal tests.

### Phase B — Robustness & contracts
5. **H3** Lazy-import escalation/`httpx`; fix the dependency contract.
6. **M2** Handle `None`/empty LLM responses explicitly in both clients.
7. **M1** Restore escalation counts in `read_report` + `_print_summary`; round-trip test.
8. **M3** Decide and document budget semantics for escalation; surface a separate counter if charged.
9. **M4** Defensive `end_lineno`.

### Phase C — Hygiene & docs (quick wins, can land first)
10. **L1/L2** `ruff check --fix .` + `ruff format .`; consider adding a CI step / pre-commit so the gate can't go red again.
11. **L3** Remove `.omc/` from tracking and gitignore it.
12. **L4** Fix the `report --last` flag.
13. **L5** De-dupe PLAN.md backlog; note 21a supersession.
14. **L6** Add dependency version bounds.
15. **L7** Tidy `clean` output.

### Phase D — Log for v2 (no action now)
- S1 (copy cost), S2 (full-suite cost), and the existing v2 backlog (mutation testing, branch coverage, eval harness, worktree parallelism, CI/PR integration, config file, other languages).

## Suggested sequencing
Land **Phase C** first (zero-risk, makes the gate green again), then **Phase A** (the real production blockers), then **Phase B**. Phase A item C1 and H2 share the same isolated-measurement refactor and should be implemented together.
