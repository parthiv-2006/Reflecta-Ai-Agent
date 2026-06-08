# EVAL-HARNESS-PLAN.md — reflecta eval harness

## Purpose

Before touching prompts, routing, or generation quality, we need an
objective benchmark. Right now any prompt change is a leap of faith. The
eval harness turns it into a measurement.

**What it measures per run:**
- Coverage gained (delta %)
- Tests accepted (passed assertion gate + coverage gate)
- Tests discarded
- Repairs used per target
- LLM calls by provider

**What it enables:**
- Detect regressions before merging a prompt change
- Compare routing strategies (e.g., Gemini-first vs Claude-first) objectively
- Validate that testability triage is neither over-blocking nor under-blocking

---

## Design principles

1. **Black-box**: the harness calls `reflecta run` as a subprocess and reads
   `reflecta-report.json`. It never imports `src/reflecta` internals. This
   means refactors in the product don't break the harness contract.

2. **CI-safe by default**: the harness uses reflecta's existing generation
   cache (`llm/cache.py`, sha256-keyed, 7-day TTL). First run with `--cache`
   populates the cache; subsequent CI runs hit it and spend zero quota.

3. **Tolerance-based comparison**: LLM output is stochastic. The baseline
   stores expected values with per-metric tolerances, not exact counts.

4. **Fixtures are committed source**: fixture modules + their partial tests +
   their pre-run `coverage_baseline.json` all live in `eval/fixtures/` and
   are tracked in git. The harness is fully reproducible from a clean clone.

5. **One new metric type, one location**: `EvalMetrics` lives in
   `eval/metrics.py`. `RunReport` (in `models.py`) gains three new fields for
   LLM call counts — it's the canonical per-run dataclass and the report
   already serialises to JSON, so the counts come out for free.

---

## New files (nothing in src/reflecta/ is modified except models.py)

```
eval/
├── __init__.py
├── fixtures/
│   ├── calc/                          # Task E-1a
│   │   ├── calc.py                    # 6 functions, pure arithmetic
│   │   ├── tests/
│   │   │   └── test_calc_partial.py   # covers 3/6 — leaves known gaps
│   │   └── coverage_baseline.json     # pre-run snapshot (committed)
│   ├── text_utils/                    # Task E-1b
│   │   ├── text_utils.py              # 5 functions, string ops + conditionals
│   │   ├── tests/
│   │   │   └── test_text_partial.py   # covers 2/5
│   │   └── coverage_baseline.json
│   └── risky_io/                      # Task E-1c
│       ├── risky_io.py                # 4 functions that do file/network I/O
│       ├── tests/
│       │   └── test_risky_partial.py  # minimal — these get triaged, not generated
│       └── coverage_baseline.json
├── recordings/                        # Task E-6: LLM response cache warm-up
│   └── .gitkeep
├── baselines/
│   └── baseline.json                  # Task E-4: expected metrics per fixture
├── metrics.py                         # Task E-2: EvalMetrics, EvalResult, EvalReport
├── runner.py                          # Task E-3: run_fixture() subprocess driver
├── compare.py                         # Task E-4: compare_to_baseline()
├── report.py                          # Task E-5: format_eval_report()
└── cli.py                             # Task E-5: typer command group
```

**Modified:**
- `src/reflecta/models.py` — add `llm_calls_gemini`, `llm_calls_groq`,
  `llm_calls_claude` int fields to `RunReport` (Task E-3).
- `src/reflecta/cli.py` — register `reflecta eval` command group (Task E-5).

---

## Fixtures specification

### calc/ (easy tier)

```python
# calc.py — 6 functions, no imports, pure arithmetic
def add(a, b): ...
def subtract(a, b): ...
def multiply(a, b): ...
def divide(a, b): ...          # raises ZeroDivisionError
def power(base, exp): ...
def clamp(value, lo, hi): ...  # conditional
```

`test_calc_partial.py` covers `add`, `subtract`, `multiply`. Leaves
`divide`, `power`, `clamp` uncovered — three clean generation targets.

**Expected harness outcome:**
- 3 targets attempted (all `testable`)
- ≥ 2 tests accepted
- coverage_delta ≥ 0.20 (20 pp)

### text_utils/ (medium tier)

```python
# text_utils.py — 5 functions, standard-library-only
def slugify(text): ...          # re + lower + strip
def truncate(text, n, ellipsis="..."): ...
def count_words(text): ...
def is_palindrome(s): ...       # conditional
def camel_to_snake(name): ...   # re
```

`test_text_partial.py` covers `count_words`, `truncate`. Leaves 3 targets.

**Expected harness outcome:**
- 3 targets attempted (all `testable`)
- ≥ 2 tests accepted
- Exercises multi-line conditional and regex handling in generated tests

### risky_io/ (triage-validation tier)

```python
# risky_io.py — 4 functions with direct I/O
def read_config(path): ...      # open() at top-level call in body
def write_report(data, path): ... # open() write
def fetch_data(url): ...        # urllib.request.urlopen
def list_files(directory): ...  # os.listdir
```

`test_risky_partial.py` covers nothing meaningful.

**Expected harness outcome:**
- 0 targets attempted (all triaged `risky` or `blocked`)
- `stop_reason = "no_testable_targets"`
- This fixture validates that the triage classifier is working — any
  regression that lets these through will show up as unexpected LLM calls.

---

## EvalMetrics dataclass (`eval/metrics.py`)

```python
@dataclass
class EvalMetrics:
    fixture_name: str
    # Coverage
    coverage_before: float
    coverage_after: float
    coverage_delta: float          # coverage_after - coverage_before
    # Generation outcomes
    targets_attempted: int
    tests_accepted: int
    tests_discarded: int
    repair_attempts_used: int
    # Triage
    targets_skipped_blocked: int
    targets_skipped_risky: int
    targets_skipped_entrypoint: int
    # LLM calls (from RunReport fields added in Task E-3)
    llm_calls_gemini: int
    llm_calls_groq: int
    llm_calls_claude: int
    # Run metadata
    run_time_seconds: float
    stop_reason: str

@dataclass
class MetricResult:
    name: str
    actual: float
    baseline: float
    tolerance: float
    passed: bool
    message: str                   # human-readable verdict

@dataclass
class EvalReport:
    fixture_name: str
    metrics: EvalMetrics
    results: list[MetricResult]
    overall_passed: bool
```

---

## RunReport additions (`src/reflecta/models.py`)

Add three fields to `RunReport`:

```python
@dataclass
class RunReport:
    ...existing fields...
    llm_calls_gemini: int = 0
    llm_calls_groq: int = 0
    llm_calls_claude: int = 0
```

Increment these in `loop.py` at each provider call site (generation and
repair stages), so the JSON report already carries the counts. The harness
reads them with no extra instrumentation.

Where to increment:
- `generate_test` call succeeds → `report.llm_calls_gemini += generated_test.generation_calls`
- `repair_test` call → increment groq or claude depending on `attempt.model_used`
- Claude overflow in router → increment `llm_calls_claude` (router returns
  `model_used` in `GeneratedTest` already; loop reads it)

---

## Baseline file format (`eval/baselines/baseline.json`)

```json
{
  "calc": {
    "coverage_delta":           { "min": 0.18, "note": "3 uncovered functions" },
    "tests_accepted":           { "min": 2,    "note": "at least 2 of 3 pass both gates" },
    "tests_discarded":          { "max": 2,    "note": "allow up to 1 failed target" },
    "repair_attempts_used":     { "max": 4,    "note": "2 repairs × 2 targets worst case" },
    "llm_calls_gemini":         { "min": 1, "max": 6, "note": "1 per target + regen" },
    "targets_skipped_blocked":  { "exact": 0 },
    "targets_skipped_risky":    { "exact": 0 }
  },
  "text_utils": {
    "coverage_delta":           { "min": 0.15 },
    "tests_accepted":           { "min": 2 },
    "tests_discarded":          { "max": 2 },
    "repair_attempts_used":     { "max": 4 },
    "targets_skipped_blocked":  { "exact": 0 },
    "targets_skipped_risky":    { "exact": 0 }
  },
  "risky_io": {
    "tests_accepted":           { "exact": 0, "note": "triage should block all targets" },
    "llm_calls_gemini":         { "exact": 0, "note": "no LLM calls before triage blocks" },
    "llm_calls_groq":           { "exact": 0 },
    "targets_skipped_risky":    { "min": 1,   "note": "at least the I/O functions" }
  }
}
```

Tolerances are intentionally loose on LLM call counts (stochastic retries)
and tight on triage outcomes (deterministic AST analysis).

---

## Comparison logic (`eval/compare.py`)

```python
def compare_to_baseline(metrics: EvalMetrics, baseline: dict) -> list[MetricResult]:
    results = []
    for metric_name, spec in baseline.items():
        actual = getattr(metrics, metric_name)
        if "exact" in spec:
            passed = actual == spec["exact"]
        elif "min" in spec and "max" in spec:
            passed = spec["min"] <= actual <= spec["max"]
        elif "min" in spec:
            passed = actual >= spec["min"]
        elif "max" in spec:
            passed = actual <= spec["max"]
        results.append(MetricResult(
            name=metric_name, actual=actual,
            baseline=spec.get("min", spec.get("max", spec.get("exact"))),
            tolerance=...,
            passed=passed,
            message=_verdict(metric_name, actual, spec, passed),
        ))
    return results
```

---

## Harness runner (`eval/runner.py`)

```python
def run_fixture(fixture_name: str, cache_dir: Path | None = None,
                python: str | None = None, verbose: bool = False,
                extra_flags: list[str] | None = None) -> EvalMetrics:
    """
    1. Locate eval/fixtures/<fixture_name>/
    2. Run: reflecta run --path <fixture_dir> --max-iters 10
               [--cache-dir <cache_dir>] [--python <python>] ...
       in a temp copy so the fixture tree is never mutated.
    3. Read the resulting reflecta-report.json.
    4. Compute and return EvalMetrics.
    """
```

Key implementation notes:
- Always run in a `tempfile.mkdtemp()` copy of the fixture — same isolation
  pattern as `loop.py`'s `copytree`.
- Pass `--cache-dir eval/recordings/<fixture_name>/` so LLM responses are
  stored/replayed from a fixture-specific directory.
- Read `llm_calls_*` directly from the deserialized `RunReport` JSON.
- Measure wall time with `time.monotonic()` wrapping the subprocess call.

---

## CLI command group (`eval/cli.py` + registered in `src/reflecta/cli.py`)

```
reflecta eval run [FIXTURE]
    Run harness against one or all fixtures. Exits 0 if all pass, 1 if any fail.
    Options:
      --fixture TEXT       Run a single fixture by name (default: all)
      --verbose            Show per-metric detail
      --python PATH        Override interpreter for fixture subprocess

reflecta eval update-baseline [FIXTURE]
    Run live (real LLM calls) and write new baseline.json. Prompts for
    confirmation before overwriting.
    Options:
      --fixture TEXT       Update baseline for a single fixture only

reflecta eval cache [FIXTURE]
    Run live and populate eval/recordings/ cache. Subsequent runs are quota-free.
    Options:
      --fixture TEXT       Cache a single fixture only
```

Registration in `src/reflecta/cli.py`:

```python
from eval.cli import eval_app   # lazy import — only if eval/ is present
app.add_typer(eval_app, name="eval")
```

---

## CI-safe execution via generation cache

reflecta's `llm/cache.py` uses sha256(prompt_text) as the key with a 7-day
TTL stored in `{repo}/.reflecta/gen_cache/`. The eval harness passes
`--cache-dir eval/recordings/<fixture>/` which redirects cache writes to a
committed directory:

1. Developer runs `reflecta eval cache` once → real LLM calls, responses
   written to `eval/recordings/<fixture>/`.
2. Commit `eval/recordings/` entries to git.
3. CI runs `reflecta eval run` → cache hits → zero quota spent.
4. When prompts change, developer re-runs `reflecta eval cache` to refresh
   the recordings, then re-runs `reflecta eval run` to capture new baseline.

This reuses existing infrastructure (no new mock layer to maintain).

---

## Task list

Legend: `[ ]` todo. Complete in order — each task is independently testable.

### E-1. Fixtures (no LLM, no reflecta code)

- [ ] **E-1a. calc/ fixture**
  - Does: write `eval/fixtures/calc/calc.py` (6 pure functions),
    `eval/fixtures/calc/tests/test_calc_partial.py` (covers 3/6),
    run `coverage json` in the fixture dir, commit the output as
    `eval/fixtures/calc/coverage_baseline.json`.
  - Verify: `cd eval/fixtures/calc && coverage run -m pytest && coverage json`
    produces a `coverage_baseline.json` with < 60% total coverage (3 gaps remain).
  - Commit: `"eval: calc fixture with partial test coverage baseline"`.

- [ ] **E-1b. text_utils/ fixture**
  - Does: same structure for `text_utils.py` (5 string functions, 2 covered).
  - Verify: `coverage_baseline.json` shows ≥ 3 uncovered function bodies.
  - Commit: `"eval: text_utils fixture with partial test coverage baseline"`.

- [ ] **E-1c. risky_io/ fixture**
  - Does: `risky_io.py` with 4 I/O functions that testability.py classifies
    as risky/blocked. Minimal test file (import only).
  - Verify: `python -c "from reflecta.testability import classify_target; ..."`
    returns `risky` or `blocked` for all 4 functions.
  - Commit: `"eval: risky_io fixture to validate triage classifier"`.

### E-2. EvalMetrics dataclass

- [ ] **E-2. metrics.py**
  - Does: `eval/metrics.py` with `EvalMetrics`, `MetricResult`, `EvalReport`
    dataclasses. Include `to_dict()` and `from_dict()` for JSON round-trip.
    Add `eval/__init__.py`.
  - Test-first: `eval/tests/test_metrics.py` — instantiate each dataclass,
    assert JSON round-trip, assert field names and types.
  - Verify: `pytest eval/tests/test_metrics.py -q` passes.
  - Commit: `"eval: EvalMetrics, MetricResult, EvalReport dataclasses"`.

### E-3. RunReport LLM call counts + harness runner

- [ ] **E-3a. Add llm_calls_* to RunReport**
  - Does: add `llm_calls_gemini: int = 0`, `llm_calls_groq: int = 0`,
    `llm_calls_claude: int = 0` to `RunReport` in `src/reflecta/models.py`.
    Increment them in `loop.py` at generation and repair call sites (use
    `generated_test.model_used` to route: `"gemini"` → gemini counter,
    `"claude-haiku-*"` → claude counter; repair `attempt.model_used` →
    groq or claude counter).
  - Verify: existing 237 tests still pass. `reflecta-report.json` gains the
    three new fields. Confirm with a smoke run on `examples/sample_project`.
  - Commit: `"feat: add llm_calls_* counters to RunReport"`.

- [ ] **E-3b. eval/runner.py**
  - Does: `run_fixture(fixture_name, cache_dir, python, verbose) -> EvalMetrics`.
    Copies fixture to temp dir, runs `reflecta run --path <tmp>` as subprocess,
    reads `reflecta-report.json`, maps fields to `EvalMetrics`. Cleans up tmp.
  - Test-first: `eval/tests/test_runner.py` — mock the subprocess call to
    return a synthetic `reflecta-report.json`; assert EvalMetrics fields map
    correctly. Test that tmp dir is cleaned up even on failure.
  - Verify: `pytest eval/tests/test_runner.py -q` passes.
  - Commit: `"eval: harness runner — subprocess driver + EvalMetrics mapping"`.

### E-4. Baseline management

- [ ] **E-4. compare.py + baselines/baseline.json (skeleton)**
  - Does: `eval/compare.py` implements `compare_to_baseline(metrics, baseline)
    -> list[MetricResult]`. Handles `exact`, `min`, `max`, and `min+max`
    constraint shapes. Writes `eval/baselines/baseline.json` with placeholder
    zeroes for all three fixtures (to be filled by Task E-7).
  - Test-first: `eval/tests/test_compare.py` — test each constraint shape
    with pass and fail cases; test that unknown metric names in the baseline
    raise a clear error.
  - Verify: `pytest eval/tests/test_compare.py -q` passes.
  - Commit: `"eval: comparison logic with tolerance-based metric constraints"`.

### E-5. CLI

- [ ] **E-5. eval CLI command group**
  - Does: `eval/cli.py` with `eval_app = typer.Typer()` and three commands:
    `run`, `update-baseline`, `cache`. Register in `src/reflecta/cli.py` via
    `app.add_typer(eval_app, name="eval")`. `eval run` exits 0/1.
  - `eval/report.py`: `format_eval_report(report: EvalReport) -> str` — plain
    text table of metric name / actual / baseline / pass|FAIL, final line
    "PASSED" or "FAILED (N regressions)".
  - Test-first: `eval/tests/test_cli.py` — use `typer.testing.CliRunner` to
    invoke `eval run --fixture calc` with a mocked `run_fixture` and
    `compare_to_baseline`; assert exit code and output contain "PASSED".
  - Verify: `pytest eval/tests/test_cli.py -q` passes. `reflecta eval --help`
    prints the command group.
  - Commit: `"eval: CLI command group — run / update-baseline / cache"`.

### E-6. Cache warm-up documentation

- [ ] **E-6. Document CI-safe cache workflow**
  - Does: add `eval/README.md` explaining:
    1. First-time setup: `reflecta eval cache` (live, spends quota once).
    2. Normal use: `reflecta eval run` (cache hit, zero quota).
    3. After prompt change: re-run cache, inspect metric delta, update
       baseline if regression is intentional.
    4. `eval/recordings/` is committed to git — treat it like a snapshot test.
  - Verify: document is accurate against the implementation. No code changes.
  - Commit: `"docs: eval harness cache workflow and CI integration guide"`.

### E-7. First live baseline capture

- [ ] **E-7. Capture and commit real baseline**
  - Does: run `reflecta eval cache` (populates `eval/recordings/`), then
    `reflecta eval update-baseline` for all three fixtures (live LLM calls,
    fills `eval/baselines/baseline.json` with real numbers). Review the
    captured metrics; tighten tolerances where the signal is deterministic
    (e.g., `risky_io` triage counts should be `exact`).
  - Verify: `reflecta eval run` immediately after returns exit 0 (PASSED).
    Inspect the report — sanity-check that coverage_delta for calc/ is > 0.
  - Commit: `"eval: commit first live baseline and cache recordings"`.

---

## Acceptance criteria (all must hold before this plan is closed)

1. `reflecta eval run` exits 0 against all three fixtures using cached LLM
   responses (zero quota spent).
2. `reflecta eval run --fixture risky_io` exits 0 with `llm_calls_gemini=0`,
   confirming triage blocked all targets before any provider call.
3. Mutate one prompt template in `src/reflecta/prompts.py` (e.g., remove a
   critical instruction), re-run `reflecta eval run --fixture calc --live`,
   observe that `tests_accepted` drops below baseline and exit code is 1.
   Revert the mutation; confirm exit 0 is restored.
4. All existing 237+ tests still pass (`pytest` from repo root).
5. `ruff check .` clean.

---

## What this unlocks (v2 backlog items this enables)

| Future work | How the harness enables it |
|-------------|---------------------------|
| Prompt iteration | Change prompt in `prompts.py`, run eval, see delta |
| Router comparison | A/B test Gemini-first vs Claude-first by diffing eval results |
| Repair strategy tuning | Change `--max-repairs` or groq model, compare `repair_attempts_used` |
| Regression CI gate | Add `reflecta eval run` as a CI step; fail the PR if exit code is 1 |
| Coverage across more fixtures | Add new `eval/fixtures/<repo>/` directories for more complex targets |
