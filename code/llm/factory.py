"""Build the gateway stack from Settings: Cache -> Fallback(primary, fallback)."""
from llm.cache import CachedGateway
from llm.gateway import ClaudeCodeGateway, OllamaGateway
from llm.router import FallbackGateway


class ConfigError(Exception):
    pass


def _make(backend, model, settings):
    if backend == "agentrouter":
        if not settings.agentrouter_key:
            raise ConfigError("AGENTROUTER_API_KEY is not set (put it in the repo-root .env)")
        return ClaudeCodeGateway(model=model, api_key=settings.agentrouter_key,
                                 base_url=settings.agentrouter_base_url, claude_bin=settings.claude_bin,
                                 timeout_s=settings.timeout_s)
    if backend == "ollama":
        return OllamaGateway(model=model, url=settings.ollama_url, timeout_s=max(settings.timeout_s, 900))
    raise ConfigError(f"unknown backend {backend!r}")


def build_gateway(settings, use_cache=True):
    primary = _make(settings.backend, settings.model, settings)
    fallback = None
    if settings.fallback_backend != "none":
        fallback = _make(settings.fallback_backend, settings.fallback_model, settings)
    gateway = FallbackGateway(primary, fallback, retries=settings.retries)
    if use_cache:
        gateway = CachedGateway(gateway, settings.cache_dir / "llm")
    return gateway
