"""Volume pricing — the target that only Claude can test.

``quote`` reads its per-unit rates and volume-discount schedule from
``rate_table.json`` (a sibling file). Reflecta's generator only ever sees *this*
module's source, and the Groq repair loop only sees this function plus a
traceback — neither is shown the JSON, so neither can know what a correct order
total actually is. They guess, the assertion fails, and repair exhausts.

Claude escalation is the only stage with a ``read_file`` tool: it opens
``rate_table.json``, reads the real rates, and writes a test that asserts the
correct totals. That is the cross-file reasoning this fixture is built to show.
"""

import json
from pathlib import Path

_TABLE_PATH = Path(__file__).with_name("rate_table.json")


class UnknownTier(Exception):
    """Raised when a pricing tier is not defined in the rate table."""


def _load_table() -> dict:
    with open(_TABLE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def quote(units: int, tier: str) -> float:
    """Price ``units`` of usage at the named pricing ``tier``, in dollars.

    Per-unit rates and the volume-discount schedule live in
    ``rate_table.json`` beside this module. The largest discount whose unit
    threshold is met applies to the whole order; the result is rounded to
    whole cents.

    Raises:
        ValueError: if ``units`` is negative.
        UnknownTier: if ``tier`` is not present in the rate table.
    """
    if units < 0:
        raise ValueError("units must be non-negative")
    table = _load_table()
    tiers = table["tiers"]
    if tier not in tiers:
        raise UnknownTier(tier)
    subtotal = units * tiers[tier]
    discount = 0.0
    for threshold, pct in sorted(table["discounts"]):
        if units >= threshold:
            discount = pct
    return round(subtotal * (1.0 - discount), 2)
