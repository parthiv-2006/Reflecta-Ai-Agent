# SPEC.md — reflecta

## Problem statement

Developers under-test their own code because writing tests is tedious and it is hard to see exactly what is untested. reflecta points at a Python repository, finds the precise lines and branches that no test exercises, writes targeted tests for them using free LLM tiers, runs those tests, repairs the ones that fail, keeps only the ones that actually raise coverage, and repeats until a coverage target is hit or a budget is spent. The output is real new tests in the repo plus a report of what changed.

## v1 scope (the core loop only)

1. Read a Python project, run its existing suite under coverage, and parse the exact uncovered lines per file.
2. Rank uncovered targets and select the next one to attempt.
3. Generate a targeted pytest test for that target using Gemini Flash.
4. Run the generated test, capture pass/fail and the traceback.
5. On failure, repair the test using Groq, up to a fixed attempt ceiling.
6. Gate every kept test on two checks: it must contain real assertions, and it must raise total coverage.
7. Iterate across targets within a budget, then write a JSON + human-readable run report.
8. Expose this as one CLI command.

## Non-goals (v1)

- No languages other than Python.
- No mutation testing (line/branch coverage is the v1 signal).
- No Claude orchestration of the main loop (Claude escalation is v2).
- No database, no web UI, no multi-user, no auth.
- No CI/PR integration.
- No running against repositories that are not your own (free Gemini tier may train on inputs).
- No editing or deleting human-written test files, ever.

## Stack

- **Language/runtime:** Python 3.11.
- **Test + coverage:** pytest, coverage.py. Machine-readable signal via `coverage json -o coverage.json`.
- **Generation:** Gemini Flash via the `google-genai` SDK. Chosen for its ~1M-token context window, which holds a full source module plus its existing tests and dependencies in one prompt.
- **Triage + repair:** Groq via the `groq` SDK. Llama 3.1 8B instant for parsing/ranking/first repair attempts, Llama 3.3 70B for harder repairs. Chosen for speed on small, frequent calls.
- **Escalation (v2):** `claude-agent-sdk`, run through Claude Code auth so it draws on a Pro/Max subscription rather than metered API billing.
- **CLI:** typer. **Deps:** uv or pip. **Format/lint:** ruff.
- **Secrets:** `GEMINI_API_KEY` and `GROQ_API_KEY` in env, loaded from `.env` (gitignored).

## Free-stack routing (the core technical decision)

| Step | Runs on | Rationale |
|---|---|---|
| Loop orchestration, run coverage/pytest, file writes, git | Deterministic Python | Free, deterministic, debuggable |
| Parse coverage JSON, rank targets | Groq 8B | Small structured task, high frequency |
| Draft a test from full source + existing tests + missed lines | Gemini Flash | Large context holds the whole module |
| Repair a failing test from its traceback (attempts 1..N) | Groq 8B then 70B | Fast iteration |
| Stuck target after N repairs | Claude Agent SDK subagent (v2) | Real tools, reserved for hard reasoning |

## Data model (in-memory dataclasses; serialized to the run report)

- **CoverageTarget**: `file_path`, `qualified_name` (module.func or module.Class.method), `missing_lines: list[int]`, `priority: float`, `status: enum{pending, generating, repairing, kept, discarded, escalated, failed}`.
- **GeneratedTest**: `target`, `test_file_path`, `source_code: str`, `model_used: str`, `assertion_count: int`.
- **RepairAttempt**: `attempt_number: int`, `traceback: str`, `model_used: str`, `result: enum{pass, fail}`.
- **RunReport**: `repo_path`, `started_at`, `coverage_before: float`, `coverage_after: float`, `targets: list[CoverageTarget]`, `tests_kept: int`, `tests_discarded: int`, `repair_attempts_used: int`, `budget`, `stop_reason: str`.

## Contracts

### CLI

```
reflecta run --path ./src \
              --target-coverage 80 \
              --max-iters 25 \
              --max-repairs 2 \
              --models gemini-flash,groq-8b \
              [--dry-run]

reflecta clean --path ./        # remove reflecta's own generated tests
reflecta report --last          # reprint the most recent run report
```

### Core function signatures (named, not final)

```python
def extract_targets(coverage_json: dict, repo_path: Path) -> list[CoverageTarget]: ...

def select_next(targets: list[CoverageTarget]) -> CoverageTarget | None: ...

def generate_test(target: CoverageTarget, source: str, existing_tests: str) -> GeneratedTest: ...   # Gemini

def run_test(test_file: Path, repo_path: Path, timeout_s: int = 30) -> RunResult: ...                # subprocess

def repair_test(test: GeneratedTest, result: RunResult, source: str) -> GeneratedTest: ...           # Groq

def passes_assertion_gate(test: GeneratedTest) -> bool: ...     # AST: real, non-trivial assertions
def passes_delta_gate(before: float, after: float) -> bool: ... # coverage strictly increased
```

### Coverage JSON shape consumed

```jsonc
// from `coverage json`
{
  "files": {
    "src/reflecta/foo.py": {
      "summary": { "percent_covered": 64.0 },
      "missing_lines": [12, 13, 14, 22],
      "missing_branches": [[20, 21]]
    }
  },
  "totals": { "percent_covered": 71.3 }
}
```

## The two gates (what keeps reflecta honest)

1. **Assertion gate.** Parse the generated test's AST. Reject before running if it has zero `assert` statements, or if every assertion is trivially true (e.g. `assert True`, `assert 1 == 1`, asserting a literal against itself).
2. **Coverage-delta gate.** After a test passes, re-measure total coverage. Keep the test only if coverage strictly increased. A passing test that does not move coverage is discarded as worthless. This is what prevents coverage theater.

## Budget and stop conditions

Stop when any of: target coverage reached; `max-iters` hit; coverage stalled across K consecutive targets; free-tier budget tracker signals it is near the daily cap. Always write the report on stop, including `stop_reason`.

## Open questions resolved here

- *What if no existing tests exist?* Run coverage on import of the package (everything is a gap), start from zero. Supported.
- *Where do generated tests live?* `tests/_reflecta/test_reflecta_<module>_<n>.py`. Never anywhere else, never overwriting.
- *What if two targets need the same test file name?* Monotonic counter per module; names never collide and never reuse.
- *Is line coverage enough?* For v1, yes. Branch and mutation testing are v2.
