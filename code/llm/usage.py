"""Append-only per-call usage log (feeds evaluation/usage_report.md)."""
import contextvars
import json
import threading
from pathlib import Path

# Case being audited by the current worker thread; set by runner.run_cases.
current_case = contextvars.ContextVar("current_case", default="")


class UsageLog:
    """prices: {model: (usd_per_1m_input, usd_per_1m_output)}; models not listed get cost None (local = 0)."""

    def __init__(self, path, prices=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.prices = prices or {}
        self._lock = threading.Lock()

    def cost(self, usage, cached=False):
        if cached:
            return 0.0  # served from our cache: no provider call
        if usage.backend == "ollama":
            return 0.0
        price = self.prices.get(usage.model)
        if price is None:
            return None
        return round(usage.input_tokens / 1e6 * price[0] + usage.output_tokens / 1e6 * price[1], 6)

    def record(self, completion, case_id, purpose):
        u = completion.usage
        row = {
            "case_id": case_id, "purpose": purpose, "backend": u.backend, "model": u.model,
            "input_tokens": u.input_tokens, "output_tokens": u.output_tokens, "duration_ms": u.duration_ms,
            "cost_usd": self.cost(u, completion.cached), "cached": completion.cached,
            "fallback_used": completion.fallback_used, "fallback_reason": completion.fallback_reason,
        }
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


class RecordingGateway:
    """Logs every completion the moment it returns, so paid calls are counted even if the run is killed."""

    def __init__(self, inner, usage_log, purpose="audit"):
        self.inner = inner
        self.backend = inner.backend
        self.model = inner.model
        self.usage_log = usage_log
        self.purpose = purpose

    def complete(self, system, messages):
        completion = self.inner.complete(system, messages)
        self.usage_log.record(completion, case_id=current_case.get(), purpose=self.purpose)
        return completion
