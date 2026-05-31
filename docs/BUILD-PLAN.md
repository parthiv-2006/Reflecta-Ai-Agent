# coverloop: Build Plan

A self-improving test coverage agent, built on a completely free AI stack, following the Idea-to-Shipped workflow phase by phase.

Working project name: **coverloop** (rename freely). The name captures the point: a real loop that measures coverage, writes tests, runs them, repairs failures, and iterates until coverage actually moves.

---

## The free stack, and what each piece does

The honest version of "completely free":

- **Claude Agent SDK** (`pip install claude-agent-sdk`, formerly the Claude Code SDK) is free software. It still consumes Claude usage when it runs. The design below keeps Claude calls rare (escalation only), so they fit inside a Claude Pro/Max subscription's included Claude Code usage rather than metered API billing. Confirm your SDK auth uses your Claude Code login, not a raw pay-as-you-go API key, or the "free" part breaks.
- **Gemini API free tier** gives you Flash / Flash-Lite with a very large context window (around 1M tokens) and a daily request cap in the hundreds-to-low-thousands. No credit card. Note: free-tier prompts may be used to train Google's models, so only point this at your own code, not anyone's private repo.
- **Groq API free tier** runs open models (Llama 3.x and similar) at very high speed, with tight per-day request caps and a small context window. No credit card.

These map onto three jobs:

| Job in the loop | Runs on | Why this one |
|---|---|---|
| Orchestrate the loop, run `coverage`/`pytest`, edit files, manage git | Plain Python you write (the spine) | Deterministic, debuggable, costs nothing |
| Parse the coverage report, rank untested targets, quick triage | **Groq** (Llama 3.1 8B instant) | Tiny structured tasks, needs speed and high request count |
| Read a whole source file plus its existing tests, draft a coherent test | **Gemini Flash** | The 1M context window holds the full module and its dependencies in one shot |
| Repair a failing test from a pytest traceback (first few attempts) | **Groq** (8B, then 70B if needed) | Fast iteration on a tight feedback loop |
| Hard cases: a target that fails repair after N tries | **Claude Agent SDK** subagent | Real filesystem and bash tools, reserved for genuine reasoning |

The key architectural decision: **the orchestration loop is deterministic Python, not an LLM agent.** The "self-improving" property comes from real execution (pytest and coverage.py giving ground truth), not from a model narrating its own plan. Cheap models do the bulk generation. Claude is the escalation path for stuck targets, which is the workflow's 2-failure rule encoded directly into the architecture. This keeps the whole thing free and keeps the loop from thrashing.

---

## Phase 0 — Shape the idea

**One-liner:** coverloop helps a developer raise test coverage on their own Python project so they can refactor and ship without fear, by finding untested code, writing tests for it, running them, and fixing its own tests until coverage measurably improves.

**The core loop (one sentence):** measure coverage, pick the most valuable untested target, write a test, run it, repair it if it fails, keep it only if coverage went up, repeat.

**Skeptical-staff-engineer questions worth answering before any code:**

1. What stops it writing tests that pass but assert nothing? (Coverage theater is the number one failure mode. Answer: an assertion-quality gate, and a coverage-delta gate that discards tests which do not move the number.)
2. How does it know what is untested, precisely? (Answer: `coverage.py` JSON report gives exact missed line and branch numbers per file. That is the structured signal, not a guess.)
3. What does "the test failed" mean to a model, and is the traceback enough to repair from? (This is the riskiest assumption. See below.)
4. What if the loop never converges and just burns the free-tier quota? (Answer: a hard budget: max iterations, max repair attempts per target, and a stop when coverage stalls across K targets.)
5. Does it run untrusted code? (It runs the target project's tests, which execute the target's code. For your own projects this is fine. Treat sandboxing as a harden-phase item, not v1.)
6. What language? (Python only for v1. pytest plus coverage.py are the most boring, best-trodden choice, and the workflow says pick boring tech.)
7. What about test pollution: clobbering existing tests, or leaving junk behind? (Answer: generated tests go in a dedicated path with a clear prefix, never overwrite, and a clean-up command exists.)
8. Is "coverage went up" the right success metric? (It is the v1 metric because it is cheap and objective. Mutation testing is a better but heavier signal: v2.)
9. Who is the user? (You, on your own repos, as a portfolio demo first. Not a multi-user product in v1.)
10. What is the smallest thing that proves the idea? (One function, one generated test, a coverage number that goes from X to X+something. That is Phase 4.)

**Riskiest assumption (what Phase 4 tests first):** that a free, cheap model can write a test that imports and runs the real code, exercises the specific uncovered lines, and when it fails, produces a failure the agent can repair within a small number of attempts, so the loop converges instead of thrashing. Everything else is engineering. This is the part that could simply not work, so it gets proven on day one.

**Scope cut:**

- **v1 (the core loop only):** Python target repos, pytest + coverage.py, Gemini for generation, Groq for triage and repair, deterministic loop, assertion gate, coverage-delta gate, a run report, a CLI. Single target repo at a time.
- **v2 (obvious next):** Claude Agent SDK escalation for stuck targets, mutation testing as a quality signal, branch-coverage targeting (not just line), parallel targets via worktrees, a config file.
- **Someday:** other languages (JS/Jest, Go), a web dashboard, CI integration (open a PR with the new tests), multi-repo, hosted version.

(Claude escalation sits in v2 deliberately. The free loop has to stand on its own first. Pull it into v1 only if Phase 4 shows the cheap models stall too often.)

**Exit criteria met:** a paragraph you could text a friend, a named core loop, a v1 list of about five items.

---

## Phase 1 — Write the spec

The full spec is in **`SPEC.md`** (drop it in the repo root). It fixes: problem statement, v1 scope, the free-stack routing, the data model (CoverageTarget, GeneratedTest, RepairAttempt, RunReport), the contracts (the CLI command, the four core function signatures, the coverage JSON shape it consumes), and the non-goals.

The decisions that matter most, made here on paper:

- **Stack:** Python 3.11, pytest, coverage.py (`coverage json` for the machine-readable report), `google-genai` SDK for Gemini, `groq` SDK for Groq, `claude-agent-sdk` for v2 escalation. `typer` for the CLI. `uv` or `pip` for deps. No database in v1: run state is held in memory and written to a JSON report at the end.
- **The coverage signal:** `coverage json -o coverage.json` produces, per file, the exact `missing_lines` and missed branches. coverloop parses that, not stdout text.
- **Test isolation:** generated tests live in `tests/_coverloop/` with a `test_coverloop_<module>_<n>.py` naming scheme. coverloop never edits a human-written test file. A `coverloop clean` command removes its own generated tests.
- **The two gates** (this is what keeps it honest):
  - *Assertion gate:* a generated test is rejected before it even runs if it contains zero `assert` statements (checked by parsing the test's AST), or if every assertion is trivially true.
  - *Coverage-delta gate:* a test that passes is kept only if it raised total coverage. A test that passes without moving coverage is discarded as worthless.

**Exit criteria:** a `SPEC.md` you would be comfortable handing to a contractor.

---

## Phase 2 — Scaffold the project and its guardrails

Now you are in Claude Code. The drop-in artifacts for this phase are **`CLAUDE.md`** and **`settings.json`** (put the latter in `.claude/settings.json`).

Steps, in order:

1. `mkdir coverloop && cd coverloop && git init`. Scaffold the standard Python layout: `src/coverloop/`, `tests/`, `pyproject.toml`, a `coverloop/__main__.py` that prints a version string and exits 0. Confirm `python -m coverloop` runs. That green hello-world is your first verification signal.
2. **Commit immediately.** Tiny, but it is the safety net. Commit after every working increment from here.
3. Drop in `CLAUDE.md` (provided). It already contains the exact build/test/lint/coverage commands, the stack, the data model summary, the free-stack routing rules, and the hard rules (never overwrite human tests, always gate on assertions and coverage delta). Trim anything that does not earn its place.
4. **Set up testing now.** Wire pytest, add `tests/test_smoke.py` with one trivial passing test, confirm `pytest` is green and `coverage run -m pytest && coverage json` produces a report. This is the rail the whole build leans on, and your tool reads exactly this output, so building it first is doubly useful.
5. Drop in `.claude/settings.json` (provided). It pre-approves the safe, frequent commands (pytest, coverage, ruff, `git status`, `git diff`, reads) and keeps destructive ones (`rm`, force-push, `git reset --hard`) on prompt or deny.
6. **One deterministic hook:** a `PostToolUse` hook that runs `ruff format` on edited Python files, so every edit is auto-formatted. (Config sketch is in `CLAUDE.md`.)
7. **Secrets:** create `.env.example` with `GEMINI_API_KEY=` and `GROQ_API_KEY=`, add `.env` to `.gitignore`. Keys load from env, never committed, never logged.
8. **MCP servers:** none in v1. The tool talks to Gemini and Groq over plain HTTPS inside its own code. Do not add MCP servers you do not need; each one taxes context.

**Exit criteria:** a committed repo that builds, runs a hello-world, passes one test, produces a coverage JSON, auto-formats on edit, and has a lean `CLAUDE.md` and a permission profile.

---

## Phase 3 — Turn the spec into an ordered build plan

The full sequenced task list is in **`PLAN.md`**. It is built as vertical slices, riskiest-first, so something runs end to end on the first task. Use Plan Mode (`Shift+Tab` twice) when you feed Claude each task so it proposes before editing, and update `PLAN.md` as you go so progress survives `/clear`.

The shape of it: task 0 is the walking skeleton (hardcoded target, one Gemini-written test, a real before/after coverage number). Then each slice replaces one stub with real logic: gap extraction, target selection, generation, execution, the Groq repair loop, the two gates, multi-target iteration, and the CLI. Hardening tasks (rate-limit backoff, isolation, idempotency) come after the loop works.

**Exit criteria:** a reviewed, file-backed task list whose first item is "make the simplest end-to-end thing run."

---

## Phase 4 — Build the walking skeleton

**Goal:** the thinnest end-to-end version of the loop, with the hard parts faked, that proves the riskiest assumption.

Concretely:

1. `/clear`, start task 0 fresh.
2. Create a tiny fixture target inside the repo: `examples/sample_project/calc.py` with two or three small functions and a `tests/` dir with a test that covers only one of them, leaving a known gap.
3. Run `coverage run -m pytest && coverage json` on the fixture. Read `coverage.json`. Hardcode the selection of one missed function for now.
4. Make one real Gemini Flash call: prompt it with the source of `calc.py`, the existing test, and the missed line numbers, asking for a single pytest test targeting the uncovered function. Write the result to `tests/_coverloop/test_coverloop_calc_0.py`.
5. Run pytest again, re-run `coverage json`, print "coverage: BEFORE% -> AFTER%".

If that number goes up, the riskiest assumption holds and the project is real. If the model writes a test that fails to import or asserts nothing, you have learned the most important thing on day one, while changing direction is still cheap, exactly as the workflow intends. Stub everything else (Groq, the gates, target ranking). The point is the wiring and the assumption, not features.

**Exit criteria:** you can trigger the loop and watch coverage move on a real fixture, even with one hardcoded target.

---

## Phase 5 — Build vertical slices (the main loop)

Run the per-task loop from `PLAN.md`, one task at a time. For this project specifically, lean hard on the workflow's strongest move:

- **Test-first is almost free here, because the tool is about tests.** For each slice, write the test for coverloop's own behavior first, watch it fail, then implement. Example: for the assertion gate, write a test that feeds in an assertion-free generated test and expects `rejected`, confirm it fails, then build the gate. The failing test is the precise target Claude iterates against on its own.
- **The 2-failure rule maps directly onto your product.** If Groq fails to repair a generated test twice, your tool escalates (to Claude in v2, or just logs and skips in v1). Build that ceiling in from the start so neither your tool nor your build session grinds a polluted context.
- **Model routing as you build:** mechanical slices and exploration on the fast tier, reserve the top tier for the genuinely tricky parts (the repair-loop convergence logic, the AST assertion analysis). This mirrors how coverloop itself routes work.
- **Watch the context meter,** stay under about 70 percent, commit each working slice, `/clear` between tasks.

The slices, in order (detail in `PLAN.md`): real coverage-gap extraction, target selection and ranking, Gemini generation with a proper prompt, test execution and traceback capture, the Groq repair loop with the attempt ceiling, the assertion gate, the coverage-delta gate, multi-target iteration with a budget, and the `coverloop run` CLI plus a run report.

**Exit criteria:** every v1 task in `PLAN.md` is implemented, tested, reviewed, committed. Pointed at a real repo, coverloop raises coverage and produces a report of what it added.

---

## Phase 6 — Harden

Each as its own focused session:

1. **Edge and failure cases:** empty repo, no existing tests, a target with no obviously testable surface, a syntactically broken target file, a module that errors on import, a test that hangs (add a per-test timeout). A free-tier 429 or network drop mid-run must back off and resume, not crash. Enumerate these with Claude, then add handling and tests.
2. **Free-tier resilience:** exponential backoff on 429s from both providers, a token-and-request budget tracker that stops cleanly before you exhaust the daily cap, and graceful degradation (if Gemini is exhausted, fall back to Groq for generation with smaller context, or pause).
3. **Isolation:** generated tests run in a subprocess with a timeout. Never let a generated test import or mutate coverloop's own state. Consider running the target's suite in a fresh virtualenv or temp copy so a bad generated test cannot corrupt the working tree.
4. **Secrets:** confirm no keys in the repo, in logs, or in the run report. Keys live in env only.
5. **The honesty pass (most important for this tool):** stress-test the two gates. Write adversarial generated tests (assertion-free, trivially-true, tests that import the module just to bump coverage without exercising logic) and confirm the gates reject them. This is where coverloop earns trust or loses it.
6. **Tidy:** have a code-review subagent flag dead code and inconsistent patterns. Fix what matters, log the rest as v2.

**Exit criteria:** coverloop survives a hostile repo, never leaks keys, never exhausts a free tier ungracefully, and its gates reject coverage theater.

---

## Phase 7 — Ship

This ships as an open-source CLI and a portfolio demo, which is the smallest responsible release for a tool like this.

1. **README** (draft from `SPEC.md` and the code): what it is, the free-stack setup (two API keys, both no-card), `pip install`, a one-command demo on the bundled `examples/sample_project`, and an animated terminal recording or GIF of coverage climbing. That demo loop is the whole pitch, so make it the first thing in the README.
2. **Package and publish:** a clean `pyproject.toml`, publish to PyPI or at minimum a `pipx`-installable GitHub repo. Keep the publish command behind a manual confirmation, never auto-run.
3. **Smoke-test from a clean clone** with only the two free keys set, on a machine that is not your dev box, to catch the boring env-var and path failures.
4. **Ship to a tiny audience:** run it on two or three of your own repos (LeaseGuard is a natural candidate), capture the real before/after numbers, and put those in the README as evidence. Then share with a few friends.
5. **Tag the release** (`v0.1.0`) so you have a known-good rollback point.

**Exit criteria:** someone who is not you can clone it, set two free API keys, run it on a sample project, and watch coverage rise.

---

## Phase 8 — Iterate

1. **Watch real runs.** On which kinds of code does it stall? Which gate fires most? That re-prioritizes v2 far better than guessing. (Likely finding: it stalls on code with heavy I/O or external dependencies, which points at a mocking strategy as the next slice.)
2. **Run the v2 list through the same mini-loop:** spec, plan, slice, test, review, ship. The big three: Claude Agent SDK escalation for stuck targets, mutation testing as a stronger quality signal than line coverage, and CI integration that opens a PR with the accepted tests.
3. **Keep `CLAUDE.md` and `SPEC.md` alive.** When Claude repeatedly gets a coverloop convention wrong, a rule is missing: add it.
4. **Add evaluation, because this project itself calls models.** Build a small eval set: a handful of fixed target files with known gaps, and a script that runs coverloop against them and records coverage gained, tests accepted, gate rejections, and repair attempts used. Run it on every prompt or model-routing change. Without it you are tuning generation prompts by vibes, and this tool lives or dies on prompt quality. This is the single highest-leverage thing you can build in Phase 8.
5. **Pay down debt deliberately** with a periodic review subagent.

**Exit criteria:** none. This is the rest of the project's life.

---

## The five rules, applied to this project

1. **Decide before you build.** The model-routing table and the two gates are decided here, on paper, not live in a session.
2. **Always have something that runs.** Task 0 moves a real coverage number. You never go dark.
3. **Every task gets a verification signal.** This tool is made of verification signals: a passing test, a coverage delta, a gate verdict.
4. **Clean context, small commits.** `/clear` per task, commit per slice, the 2-failure rule (which is also a feature of the product), under 70 percent context.
5. **You own the diff.** Review the repair loop and the gates by hand. Those are the parts that, if wrong, make the tool lie about its own value.

The free stack will change (limits move, models get renamed, tiers get adjusted). The order of operations does not.
