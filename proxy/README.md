# reflecta proxy

The server side of reflecta's **key-broker (remote) mode**. It lets you run
reflecta as a product on *your* provider keys: end users run `reflecta login`
with a token you issue, and never obtain a Gemini/Groq key of their own.

It is a thin broker. It holds your provider keys, authenticates per-user
reflecta tokens, meters usage against a daily quota, validates the requested
model against an allowlist, and forwards prompt text to Gemini/Groq. **It never
receives or runs user code** — only prompts cross the wire; the user's repo,
test execution, and coverage all stay on the user's machine.

## Endpoint

```
POST /v1/complete
Authorization: Bearer <reflecta_token>
{"task": "generate" | "repair", "prompt": "...", "model": "..."}

200 {"text": "..."}     401 bad/missing token      400 bad task/model
413 prompt too large    429 daily quota exceeded   502 upstream provider error
GET /healthz -> {"status": "ok", ...}
```

## Configure

Copy `.env.example` to `.env` and fill in (see that file for all options):

| Var | Purpose |
|-----|---------|
| `GEMINI_API_KEY`, `GROQ_API_KEY` | your provider keys |
| `REFLECTA_TOKENS` | issued tokens + quotas (JSON map or comma list) |
| `REFLECTA_DEFAULT_DAILY_QUOTA` | per-token daily call cap (default 200) |
| `REFLECTA_MAX_PROMPT_CHARS` | reject oversized prompts (default 200000) |

## Run locally

```bash
cd proxy
pip install -r requirements.txt
set -a; source .env; set +a            # load config
uvicorn app:app --reload --port 8000
```

Point a client at it:

```bash
reflecta login --token tok_alice --proxy-url http://localhost:8000
reflecta run --path /your/repo -v
```

## Test

```bash
cd proxy
pip install -r requirements.txt
pytest                                  # provider calls are stubbed; no keys needed
```

## Deploy

A `Dockerfile` is included. Any container host works (Fly.io, Render, Railway,
Cloud Run, ECS):

```bash
docker build -t reflecta-proxy .
docker run -p 8000:8000 --env-file .env reflecta-proxy
```

Set the deployed URL as the client default (`DEFAULT_PROXY_URL` in
`src/reflecta/llm/remote.py`) or have users pass `--proxy-url`.

## Production hardening (before you open it up)

This is a correct, minimal MVP. Before real users:

- **Metering is in-memory.** A multi-worker / multi-instance deployment needs a
  shared store (Redis or a DB) so quotas are enforced globally and survive
  restarts. Swap the `_Meter` class for a backed implementation.
- **Tokens are static env config.** For self-serve signup, move issuance to a
  database with hashed tokens, revocation, and per-user plans; add billing
  (e.g. Stripe) keyed on metered usage.
- **Use the paid, no-train provider tier.** The free Gemini tier may train on
  inputs — you must not run customers' private code/prompts through it. This is
  also why you can't just embed a free key in the CLI.
- **Add rate limiting + request logging** (per-token RPS caps, structured usage
  logs) to detect and stop abuse.
- **Terms/privacy.** Brokering customer prompts makes you a data processor —
  publish terms and a privacy policy, and confirm your provider contracts allow
  it.
