# Remote key-broker mode

This is how reflecta runs **as a product on your keys** instead of requiring
every user to bring their own Gemini/Groq keys. End users run `reflecta login`
once and never touch a provider key.

## The core idea

reflecta runs the user's `pytest` — i.e. arbitrary code. The deliberate design
choice here is to **keep that execution on the user's machine** and only broker
the LLM calls. So you (the operator) never run untrusted user code; you run a
thin proxy that holds your keys and meters usage.

```
end user (their machine)                 your proxy (your keys)        providers
────────────────────────                 ──────────────────────        ─────────
reflecta CLI
  • finds coverage gaps
  • runs pytest locally
  • generates / repairs
  • every LLM call ──────HTTPS + token──▶ • verify reflecta token
                                          • check daily quota (429 if over)
                                          • validate model allowlist
                                          • forward prompt w/ YOUR key ─▶ Gemini
                          ◀──── text ───── • meter usage                ─▶ Groq
```

Only **prompt text** crosses the wire. The repo, test execution, coverage
measurement, and generated files all stay local to the user.

## Client side (this package)

All free-stack LLM calls funnel through one seam, so remote mode is a contained
addition:

- `src/reflecta/llm/remote.py` — credential resolution, the proxy `complete()`
  call, and the `DEFAULT_PROXY_URL`.
- `src/reflecta/llm/gemini.py` / `groq.py` — when a token is configured (and no
  SDK client was injected for testing), they call `remote.complete(...)` instead
  of the provider SDK. Provider SDKs are lazy-imported, so remote-only users
  don't need them.
- `src/reflecta/config.py:require_credentials` — preflight skips provider-key
  checks in remote mode.
- `src/reflecta/cli.py` — `reflecta login` / `logout`.

**Mode selection (precedence):**
1. `REFLECTA_TOKEN` env var or `~/.reflecta/credentials` → **remote mode**
2. else `GEMINI_API_KEY` + `GROQ_API_KEY` → **direct mode** (BYO key / dev)
3. else → a clear preflight error

**End-user flow:**
```bash
reflecta login                 # paste the token you issued
reflecta run --path . -v       # no provider keys needed; runs on your account
```

Note: `--escalate` (Claude) still runs locally and needs `ANTHROPIC_API_KEY`; it
is not brokered by the proxy in v1.

## Server side

Lives in [`proxy/`](../proxy/) — a standalone FastAPI service with its own
README, tests, and Dockerfile. One endpoint (`POST /v1/complete`), bearer-token
auth, per-token daily quota, model allowlist, prompt-size cap. See
[`proxy/README.md`](../proxy/README.md) for configuration, running, deploying,
and the production-hardening checklist.

## What "your keys" forces (non-negotiables)

- **Paid, no-train provider tier.** The free Gemini tier may train on inputs —
  you must not run customers' private prompts through it.
- **Metering + quotas before launch.** The proxy enforces a per-token daily cap;
  swap the in-memory meter for Redis/DB before running multiple instances.
- **Never embed a key in the CLI.** It's trivially extractable; the whole point
  of the proxy is that the key stays server-side.

## Roadmap beyond this MVP

1. **Token issuance** — DB-backed, hashed tokens, revocation, self-serve signup.
2. **Billing** — meter → Stripe usage records.
3. **GitHub App / CI Action** — run inside the user's CI, still calling this proxy.
4. **Brokered escalation** — proxy the Claude tool-use loop too, so escalation
   also runs on operator keys.
