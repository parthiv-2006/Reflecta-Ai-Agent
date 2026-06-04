from reflecta.llm.limits import (
    GROQ_FREE_LIMITS,
    estimate_tokens,
    model_tpm,
    request_char_budget,
    request_token_budget,
)


def test_known_groq_tpm_values():
    assert model_tpm("llama-3.1-8b-instant") == 6_000
    assert model_tpm("llama-3.3-70b-versatile") == 12_000


def test_unknown_model_falls_back_to_smallest_tpm():
    # Conservative default so we never over-send to an unknown model.
    assert model_tpm("some-future-model") == 6_000


def test_estimate_tokens_monotonic_and_positive():
    assert estimate_tokens("") >= 1
    assert estimate_tokens("a" * 350) > estimate_tokens("a" * 35)


def test_request_budget_is_fraction_of_tpm():
    # Default fraction leaves room for the completion within the same minute.
    assert request_token_budget("llama-3.1-8b-instant") < 6_000
    assert request_token_budget("llama-3.3-70b-versatile") > request_token_budget(
        "llama-3.1-8b-instant"
    )


def test_char_budget_tracks_token_budget():
    assert request_char_budget("llama-3.1-8b-instant") > 0
    # Bigger-TPM model gets a bigger char budget.
    assert request_char_budget("llama-3.3-70b-versatile") > request_char_budget(
        "llama-3.1-8b-instant"
    )


def test_limits_table_has_all_four_axes():
    lim = GROQ_FREE_LIMITS["llama-3.1-8b-instant"]
    assert lim.tpm and lim.rpm and lim.rpd and lim.tpd
