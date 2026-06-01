from dataclasses import dataclass, field


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
