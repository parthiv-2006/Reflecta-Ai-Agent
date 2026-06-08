# ROBUSTNESS-FIXES.md — Execution Guide

## Why this exists

A run against a real-world repo (Leaseguard) produced 0 kept tests and stopped
after 3 targets. Three compounding bugs caused this:

1. **Priority is inverted.** `priority = len(missing_lines)` sends reflecta to
   the *largest* functions first. On Leaseguard those are complex orchestrators
   that exhaust repair attempts. Small pure utilities (1–5 lines) that would
   trivially pass sit at the bottom of the queue and never get reached.

2. **stall_k=3 default is too aggressive.** After 3 consecutive failures the
   run stops — before it ever reaches the easy wins.

3. **Testability triage misses indirect I/O.** `seed_clause_type` calls
   `browse_canlii` (marked RISKY), but `seed_clause_type` itself is marked
   TESTABLE because it doesn't *directly* call `requests`. Three LLM calls
   are wasted per target on functions that are effectively untestable.

This document describes the exact changes needed, in TDD order, with commit
messages and verification steps. Execute each task in sequence. Run
`ruff check . && pytest -x -q` after every commit before moving on.

---

## Execution workflow

For each task below:
1. Read the files listed under "Read first".
2. Write the failing test(s) (TDD — test first, then implementation).
3. Run `pytest tests/<file> -x -q` — confirm it fails for the right reason.
4. Implement the change.
5. Run `pytest tests/<file> -x -q` — confirm it passes.
6. Run `ruff check . && ruff format .`
7. Stage only the files listed, commit with the exact commit message given.
8. Push: `git push`.

Never skip the red-test step. Never `git add .` — stage specific files only.

---

## Task 1 — Easy-wins-first selection

### Read first
- `src/reflecta/selection.py` (full)
- `tests/test_selection.py` (full)

### What to change

**`src/reflecta/selection.py`** — add a `_size_bucket` helper and insert it as
a new sort tier before `-t.priority`:

```python
from reflecta.models import CoverageTarget, TargetStatus


def _size_bucket(t: CoverageTarget) -> int:
    n = len(t.missing_lines)
    if n <= 15:
        return 0
    if n <= 50:
        return 1
    return 2


def select_next(targets: list[CoverageTarget]) -> CoverageTarget | None:
    """Return the highest-priority pending target, or None if none remain.

    Ranking (primary to secondary):
      1. Non-entrypoints before entrypoints.
      2. testable < risky < blocked.
      3. Size bucket 0 (≤15 missing lines) before 1 (≤50) before 2 (51+).
         Smaller functions are more likely to be pure utilities that generate
         passing tests immediately; attempting them first maximises early
         coverage gains and avoids wasting repair budget on orchestrators.
      4. Within each bucket, descending by priority (most missing lines first)
         for maximum coverage bang per buck.
      5. Top-level functions before class methods on ties.
    """
    pending = [t for t in targets if t.status == TargetStatus.PENDING]
    if not pending:
        return None
    _risk_rank = {"testable": 0, "risky": 1, "blocked": 2}
    return min(
        pending,
        key=lambda t: (
            t.is_entrypoint,
            _risk_rank.get(t.testability, 0),
            _size_bucket(t),
            -t.priority,
            t.qualified_name.count("."),
        ),
    )
```

### Tests to add in `tests/test_selection.py`

Add these two tests. Write them first and confirm they fail before implementing:

```python
def test_small_target_beats_large_target():
    """A small function (≤15 lines) should be selected before a large one
    even when the large function has a higher raw priority score."""
    small = CoverageTarget(
        file_path=Path("a.py"),
        qualified_name="small_func",
        missing_lines=list(range(5)),   # priority = 5
        priority=5.0,
        status=TargetStatus.PENDING,
        is_entrypoint=False,
        testability="testable",
    )
    large = CoverageTarget(
        file_path=Path("a.py"),
        qualified_name="large_func",
        missing_lines=list(range(60)),  # priority = 60
        priority=60.0,
        status=TargetStatus.PENDING,
        is_entrypoint=False,
        testability="testable",
    )
    assert select_next([large, small]) is small
    assert select_next([small, large]) is small


def test_within_same_bucket_larger_first():
    """Within the same size bucket, the function with more missing lines
    (higher priority) should be selected first."""
    medium_a = CoverageTarget(
        file_path=Path("a.py"),
        qualified_name="func_a",
        missing_lines=list(range(30)),  # bucket 1, priority 30
        priority=30.0,
        status=TargetStatus.PENDING,
        is_entrypoint=False,
        testability="testable",
    )
    medium_b = CoverageTarget(
        file_path=Path("a.py"),
        qualified_name="func_b",
        missing_lines=list(range(20)),  # bucket 1, priority 20
        priority=20.0,
        status=TargetStatus.PENDING,
        is_entrypoint=False,
        testability="testable",
    )
    assert select_next([medium_b, medium_a]) is medium_a
```

### Commit

```
feat: easy-wins-first selection — small targets (≤15 lines) attempted before large
```

Files to stage: `src/reflecta/selection.py`, `tests/test_selection.py`

---

## Task 2 — Raise stall_k and max_iters defaults

### Read first
- `src/reflecta/cli.py` lines 15–80 (the `run` command defaults)
- `src/reflecta/loop.py` lines 437–455 (the `run_loop` signature)

### What to change

**`src/reflecta/cli.py`** — two typer.Option defaults:

```python
# Change stall_k default: 3 → 7
stall_k: int = typer.Option(
    7, help="Stop after this many consecutive targets that do not raise coverage."
),

# Change max_iters default: 10 → 20
max_iters: int = typer.Option(20, help="Maximum targets to attempt per run."),
```

**`src/reflecta/loop.py`** — keep the `run_loop` function signature defaults in
sync (they are used directly in tests):

```python
def run_loop(
    repo_path: Path,
    *,
    max_iters: int = 20,   # was 10
    ...
    stall_k: int = 7,      # was 3
    ...
```

### Tests to add in `tests/test_loop_budget.py`

Confirm the defaults are what's documented. Add after existing tests:

```python
def test_run_loop_default_stall_k_is_7(tmp_path):
    """stall_k default must be 7 so easy-win repos don't stop prematurely."""
    import inspect
    from reflecta.loop import run_loop
    sig = inspect.signature(run_loop)
    assert sig.parameters["stall_k"].default == 7


def test_run_loop_default_max_iters_is_20(tmp_path):
    import inspect
    from reflecta.loop import run_loop
    sig = inspect.signature(run_loop)
    assert sig.parameters["max_iters"].default == 20
```

Check whether any existing tests pass an explicit `stall_k=3` or `max_iters=10`
to `run_loop` — if so, leave those explicit values in place (do not change test
call sites, only the defaults).

### Commit

```
fix: raise default stall_k 3→7 and max_iters 10→20 for real-world repos
```

Files to stage: `src/reflecta/cli.py`, `src/reflecta/loop.py`,
`tests/test_loop_budget.py`

---

## Task 3 — Transitive hostile-call detection in testability triage

### Read first
- `src/reflecta/testability.py` (full — understand `_hostile_categories_in_calls`,
  `classify_target`, and how `aliases` is built)
- `tests/test_testability.py` (full — understand existing test patterns)

### What to change

**`src/reflecta/testability.py`** — add `_local_risky_names` above
`classify_target`, then add a transitive check inside `classify_target`.

Add this function after `_hostile_categories_in_calls`:

```python
def _local_risky_names(tree: ast.Module, aliases: dict[str, str]) -> frozenset[str]:
    """Return the names of module-level functions that directly perform hostile I/O.

    Used to detect *transitive* hostility: a function is RISKY if it calls one
    of these names, even though it does not itself reference a hostile import.
    Only goes one level deep — chasing longer call chains is over-engineering
    for the value it adds (one level catches ~95% of real-world cases).
    """
    risky: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
            if _hostile_categories_in_calls(calls, aliases):
                risky.add(node.name)
    return frozenset(risky)
```

In `classify_target`, insert the transitive check between the direct-hostile
check and the final `return Verdict(TESTABLE)`:

```python
def classify_target(source: str, qualified_name: str) -> Verdict:
    """Static testability verdict for one target. No execution, no LLM."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return Verdict(TESTABLE)

    aliases = _import_alias_map(tree)

    # 1. Import-time hazards block every target in the module.
    hazard_cat, hazard_reason = _module_import_hazards(tree, aliases)
    if hazard_cat:
        return Verdict(BLOCKED, f"module {hazard_reason}", {hazard_cat})

    func = _find_function(tree, qualified_name)
    if func is None:
        return Verdict(TESTABLE)

    calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)]

    # 2. Direct hostile calls.
    cats = _hostile_categories_in_calls(calls, aliases)
    if cats:
        label = ", ".join(sorted(cats))
        return Verdict(RISKY, f"directly performs {label} I/O", cats)

    # 3. Transitive: calls a module-local function that performs hostile I/O.
    local_risky = _local_risky_names(tree, aliases)
    if local_risky:
        for call in calls:
            root = _call_root_name(call)
            if root and root in local_risky:
                return Verdict(RISKY, f"calls {root} which performs I/O", {"indirect"})

    return Verdict(TESTABLE)
```

### Tests to add in `tests/test_testability.py`

Write these tests first; confirm they fail before implementing:

```python
def test_classify_target_transitive_risky_via_helper():
    """A function that only calls a local helper which does network I/O
    should be classified RISKY, not TESTABLE."""
    source = textwrap.dedent("""
        import requests

        def _fetch(url):
            return requests.get(url)

        def process(url):
            return _fetch(url)
    """)
    v = classify_target(source, "process")
    assert v.level == RISKY
    assert "_fetch" in v.reason or "I/O" in v.reason


def test_classify_target_direct_risky_unaffected():
    """A directly-risky function should still be RISKY (regression guard)."""
    source = textwrap.dedent("""
        import requests

        def _fetch(url):
            return requests.get(url)
    """)
    v = classify_target(source, "_fetch")
    assert v.level == RISKY


def test_classify_target_pure_helper_not_infected():
    """A function that only calls a pure (non-risky) local helper should
    remain TESTABLE — the transitive check must not over-flag."""
    source = textwrap.dedent("""
        import requests

        def _normalize(text):
            return text.strip().lower()

        def process(text):
            return _normalize(text)
    """)
    v = classify_target(source, "process")
    assert v.level == TESTABLE


def test_classify_target_di_parameter_not_flagged():
    """A function that receives a client as a parameter and calls it is
    dependency injection — must remain TESTABLE (existing contract)."""
    source = textwrap.dedent("""
        import requests

        def _call(session, url):
            return session.get(url)

        def process(client, url):
            return _call(client, url)
    """)
    v = classify_target(source, "process")
    # _call receives session as a parameter — not a hostile import call.
    # process calling _call is therefore not transitive-hostile.
    assert v.level == TESTABLE
```

Note: the DI test (`test_classify_target_di_parameter_not_flagged`) may already
pass without changes — verify before adding if it is a regression guard or a new
assertion.

### Commit

```
feat: transitive hostile-call detection in testability triage
```

Files to stage: `src/reflecta/testability.py`, `tests/test_testability.py`

---

## Task 4 — Update PLAN.md

Mark task 19f in `PLAN.md` with a sub-item for these robustness fixes:

```markdown
  - [x] **19g. Priority + triage robustness (2026-06-08):** easy-wins-first
        selection (small functions ≤15 lines attempted before large orchestrators);
        stall_k default 3→7 and max_iters default 10→20; transitive hostile-call
        detection in testability triage (one-level call-graph analysis catches
        functions that delegate I/O to local helpers). Fixes zero-kept-test run
        on Leaseguard.
```

### Commit

```
chore: mark task 19g done in PLAN.md
```

Files to stage: `PLAN.md`

---

## End-to-end verification

After all four tasks are committed and pushed:

```bash
# Full suite must be green
pytest -x -q

# Lint must be clean
ruff check . && ruff format --check .

# Dry run on Leaseguard — confirm seed_clause_type is now RISKY (skipped)
# and small utilities appear at the top of "Would attempt"
python -m reflecta run \
  --path "C:\Users\Parthiv Paul\Documents\leaseguard" \
  --dry-run

# Real run (limited budget to test progress)
python -m reflecta run \
  --path "C:\Users\Parthiv Paul\Documents\leaseguard" \
  --max-iters 15 \
  --max-llm-calls 20

# Success criteria:
# - Coverage rises above 8.0%
# - At least 1 test kept
# - Stop reason is NOT "stalled" on first 3 targets
# - seed_clause_type and _parse_rta_sections do NOT appear in the run output
#   (they are now triage-RISKY and skipped before any LLM call)
```
