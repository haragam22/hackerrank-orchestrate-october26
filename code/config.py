"""Runtime settings. Secrets come only from the environment (optionally loaded from repo-root .env)."""
import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = REPO_ROOT / "code"
DATASET_DIR = REPO_ROOT / "dataset"
CACHE_DIR = CODE_DIR / ".cache"


def load_env_file(path):
    """Parse KEY=VALUE lines into os.environ without overriding variables already set.

    Returns the values found in the file (not the merged environment).
    """
    values = {}
    path = Path(path)
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        values[key] = value
        os.environ.setdefault(key, value)
    return values


@dataclass(frozen=True)
class Settings:
    backend: str            # "agentrouter" | "ollama"
    model: str
    fallback_backend: str   # "ollama" | "none"
    fallback_model: str
    agentrouter_key: str
    agentrouter_base_url: str
    ollama_url: str
    claude_bin: str
    timeout_s: int
    retries: int
    parallel: int
    max_turns: int
    cache_dir: Path
    use_skeptic: bool = True
    price_in_per_m: float = 1.0     # AgentRouter gpt-6.1-sol prompt price, USD per 1M tokens
    price_out_per_m: float = 5.0    # completion price

    def prices(self):
        return {self.model: (self.price_in_per_m, self.price_out_per_m)}

    def __repr__(self):  # never print the key
        return f"Settings(backend={self.backend!r}, model={self.model!r}, fallback={self.fallback_backend!r})"


def get_settings():
    load_env_file(REPO_ROOT / ".env")
    env = os.environ.get
    return Settings(
        backend=env("AUDIT_BACKEND", "agentrouter"),
        model=env("AUDIT_MODEL", "gpt-6.1-sol"),
        fallback_backend=env("AUDIT_FALLBACK_BACKEND", "ollama"),
        fallback_model=env("AUDIT_FALLBACK_MODEL", "qwen3.5:9b-q4_K_M"),
        agentrouter_key=env("AGENTROUTER_API_KEY", ""),
        # Claude Code's Anthropic-style base URL (not the OpenAI /v1 path).
        agentrouter_base_url=env("AUDIT_AGENTROUTER_BASE_URL", "https://agentrouter.org"),
        ollama_url=env("AUDIT_OLLAMA_URL", "http://localhost:11434"),
        claude_bin=env("AUDIT_CLAUDE_BIN", "claude"),
        timeout_s=int(env("AUDIT_TIMEOUT_S", "300")),
        retries=int(env("AUDIT_RETRIES", "3")),
        parallel=int(env("AUDIT_PARALLEL", "6")),
        max_turns=int(env("AUDIT_MAX_TURNS", "3")),
        cache_dir=Path(env("AUDIT_CACHE_DIR", str(CACHE_DIR))),
        use_skeptic=env("AUDIT_SKEPTIC", "1") == "1",
        price_in_per_m=float(env("AUDIT_PRICE_IN_PER_M", "1.0")),
        price_out_per_m=float(env("AUDIT_PRICE_OUT_PER_M", "5.0")),
    )
