"""Disk cache of completions keyed by (backend, model, system, messages).

Fallback answers are never cached so a rerun retries the primary model.
"""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from llm.gateway import Completion, Usage


class CachedGateway:
    def __init__(self, inner, cache_dir):
        self.inner = inner
        self.backend = inner.backend
        self.model = inner.model
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, system, messages):
        key = json.dumps([self.backend, self.model, system, messages], ensure_ascii=False, sort_keys=True)
        return self.dir / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    def complete(self, system, messages):
        path = self._path(system, messages)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return Completion(text=data["text"], usage=Usage(**data["usage"]), cached=True)
        result = self.inner.complete(system, messages)
        if not result.fallback_used:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"text": result.text, "usage": asdict(result.usage)}, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(path)
        return result
