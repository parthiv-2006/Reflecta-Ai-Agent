from dataclasses import dataclass, field

from reflecta.llm.provider import BudgetExhausted


@dataclass
class BudgetTracker:
    max_llm_calls: int
    _used: int = field(default=0, init=False)

    def charge(self, n: int = 1) -> None:
        self._used += n

    @property
    def used(self) -> int:
        return self._used

    def exhausted(self) -> bool:
        return self._used >= self.max_llm_calls

    def check(self) -> None:
        """Raise BudgetExhausted if the cap has been reached."""
        if self.exhausted():
            raise BudgetExhausted(
                f"LLM call budget exhausted ({self._used}/{self.max_llm_calls})"
            )
