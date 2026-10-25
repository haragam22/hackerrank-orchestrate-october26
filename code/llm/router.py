"""Primary gateway with retry/backoff, then a flagged fallback."""
import time

from llm.gateway import GatewayError


class FallbackGateway:
    def __init__(self, primary, fallback=None, retries=3, base_delay_s=2.0, sleep=time.sleep):
        self.primary = primary
        self.fallback = fallback
        self.retries = retries
        self.base_delay_s = base_delay_s
        self._sleep = sleep
        self.backend = primary.backend
        self.model = primary.model

    def complete(self, system, messages):
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                return self.primary.complete(system, messages)
            except GatewayError as exc:
                last_error = exc
                if not exc.transient or attempt == self.retries:
                    break
                self._sleep(self.base_delay_s * (2 ** attempt))
        if self.fallback is None:
            raise last_error
        result = self.fallback.complete(system, messages)
        result.fallback_used = True
        result.fallback_reason = f"{self.primary.backend}/{self.primary.model}: {last_error.kind}: {last_error}"[:300]
        return result
