# eval/recordings — LLM response cache for CI-safe harness runs

This directory stores sha256-keyed LLM response cache files written by
`reflecta eval cache`. Commit them after warming so CI runs are quota-free.

One sub-directory per fixture:
```
recordings/
├── calc/          # cache files for the calc fixture
├── text_utils/    # cache files for the text_utils fixture
└── risky_io/      # (should be empty — triage blocks all LLM calls)
```

See `eval/README.md` for the full CI workflow.
