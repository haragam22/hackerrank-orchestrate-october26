"""Manual smoke check: one schema-validated call through the configured gateway.

Run: python code/llm/smoke.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel  # noqa: E402

from config import get_settings  # noqa: E402
from llm.factory import build_gateway  # noqa: E402
from llm.jsoncall import call_json  # noqa: E402
from llm.usage import UsageLog  # noqa: E402


class IntentProbe(BaseModel):
    is_deletion_request: bool
    reason: str


def main():
    settings = get_settings()
    gateway = build_gateway(settings, use_cache=False)
    result = call_json(
        gateway,
        "You classify customer privacy requests for a deletion audit.",
        [{"role": "user", "content": "Customer request: 'Please send me a copy of all the personal data "
                                     "you hold about me.' Is this a request to delete personal data?"}],
        IntentProbe,
    )
    log = UsageLog(settings.cache_dir / "usage_smoke.jsonl", prices=settings.prices())
    for c in result.completions:
        log.record(c, case_id="smoke", purpose="smoke")
    last = result.completions[-1]
    print(f"value={result.value.model_dump()} attempts={result.attempts} backend={last.usage.backend} "
          f"model={last.usage.model} in={last.usage.input_tokens} out={last.usage.output_tokens} "
          f"ms={last.usage.duration_ms} fallback_used={result.fallback_used}")
    if result.fallback_used:
        print(f"FLAG: fallback used - {last.fallback_reason}")


if __name__ == "__main__":
    main()
