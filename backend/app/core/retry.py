import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0

    def allows(self, attempt: int) -> bool:
        """`attempt` is the number of attempts already made."""
        return attempt < self.max_attempts

    def delay(
        self, attempt: int, retry_after: float = 0.0, rng: random.Random | None = None
    ) -> float:
        """Exponential backoff with jitter, never below a server-provided retry-after."""
        rng = rng or random
        backoff = min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        jittered = backoff * (0.5 + rng.random() / 2)
        return max(jittered, retry_after) if self.base_delay > 0 else 0.0
