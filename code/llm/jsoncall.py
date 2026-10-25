"""Schema-enforced model calls: every reply is parsed and validated, malformed replies are retried."""
import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text):
    """Return the first JSON object in text (handles code fences and surrounding prose)."""
    candidates = [m.group(1) for m in _FENCE.finditer(text or "")] + [text or ""]
    decoder = json.JSONDecoder()
    for cand in candidates:
        cand = cand.strip()
        for i, ch in enumerate(cand):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(cand, i)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    raise ValueError("no JSON object found in model reply")


class JsonCallError(Exception):
    def __init__(self, message, attempts, completions):
        super().__init__(message)
        self.attempts = attempts
        self.completions = completions


@dataclass
class JsonCallResult:
    value: object
    attempts: int
    completions: list = field(default_factory=list)

    @property
    def fallback_used(self):
        return any(c.fallback_used for c in self.completions)


def call_json(gateway, system, messages, model_cls, max_retries=2):
    """Call the gateway until the reply validates against model_cls (pydantic), max_retries extra tries."""
    schema = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)
    full_system = (f"{system}\n\nReply with ONLY one JSON object (no prose, no code fences) that matches "
                   f"this JSON schema:\n{schema}")
    history = [dict(m) for m in messages]
    completions = []
    error = ""
    for attempt in range(1, max_retries + 2):
        completion = gateway.complete(full_system, history)
        completions.append(completion)
        try:
            value = model_cls.model_validate(extract_json(completion.text))
            return JsonCallResult(value=value, attempts=attempt, completions=completions)
        except (ValueError, ValidationError) as exc:
            error = str(exc)[:800]
            history = history + [
                {"role": "assistant", "content": completion.text},
                {"role": "user", "content": f"Your previous reply was invalid: {error}\n"
                                            "Reply again with ONLY a JSON object matching the schema."},
            ]
    raise JsonCallError(f"model reply invalid after {max_retries + 1} attempts: {error}",
                        attempts=max_retries + 1, completions=completions)
