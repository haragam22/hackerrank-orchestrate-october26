"""Backends that turn (system, messages) into one model completion.

messages: list of {"role": "user" | "assistant", "content": str}.
"""
import json
import os
import re
import subprocess
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Usage:
    backend: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    cost_usd_estimate: float | None = None


@dataclass
class Completion:
    text: str
    usage: Usage
    cached: bool = False
    fallback_used: bool = False
    fallback_reason: str = ""
    extra: dict = field(default_factory=dict)


class GatewayError(Exception):
    """kind: server (5xx/429/timeout), blocked (provider content filter), client (bad request)."""

    def __init__(self, message, transient, kind):
        super().__init__(message)
        self.transient = transient
        self.kind = kind


_LINE_BREAKS = re.compile(r"\r\n|[\r\x0b\x0c\x85\u2028\u2029]")
_SK_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")
_ROLE_LINE = re.compile(r"(?im)^(\W*)(USER|ASSISTANT|SYSTEM)\b(.*)$")


def _neutralise_markers(content):
    """Content cannot fake a role marker, including look-alikes (security review F11, final pass L3)."""
    content = "".join(ch for ch in unicodedata.normalize("NFKC", content) if unicodedata.category(ch) != "Cf")
    content = _LINE_BREAKS.sub("\n", content)  # CR, VT, FF, NEL, U+2028/9 also start a line (re-check L2)
    content = content.replace("===", "= = =")
    return _ROLE_LINE.sub(lambda m: f"> {m.group(1)}{m.group(2)}{m.group(3)}" if not re.search(r"\w", m.group(1))
                          else m.group(0), content)


def render_transcript(messages):
    """Flatten a multi-turn history into one prompt for single-shot backends."""
    if len(messages) == 1:
        return messages[0]["content"]
    parts = ["Conversation so far (oldest first). Continue as ASSISTANT.\n"]
    for m in messages:
        parts.append(f"=== {m['role'].upper()} ===\n{_neutralise_markers(m['content'])}\n")
    parts.append("=== ASSISTANT (your next reply) ===")
    return "\n".join(parts)


def _classify(message):
    if "content-blocked" in message:
        return False, "blocked"
    if re.search(r"\b(5\d\d|429)\b", message) or "overloaded" in message.lower():
        return True, "server"
    return False, "client"


class ClaudeCodeGateway:
    """AgentRouter through the official Claude Code CLI in headless mode.

    Built-in tools are disabled (`--tools ""`): AgentRouter backends reject native function tools
    intermittently, so tool use is done with our own JSON-action protocol instead.
    """

    backend = "agentrouter"

    def __init__(self, model, api_key, base_url, claude_bin="claude", timeout_s=300, runner=subprocess.run):
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        self._claude_bin = claude_bin
        self._timeout_s = timeout_s
        self._runner = runner

    def _redact(self, text):
        """Removes the key, any 12+ character fragment of it, and any sk-style token (security pass L3-new)."""
        if self._api_key:
            text = text.replace(self._api_key, "[REDACTED]")
            key, n = self._api_key, 12
            if len(key) >= n:
                frags = {key[i:i + n] for i in range(len(key) - n + 1)}
                text = re.sub(r"[A-Za-z0-9_\-]{%d,}" % n,
                              lambda m: "[REDACTED]" if any(f in m.group(0) for f in frags) else m.group(0), text)
        return _SK_TOKEN.sub("[REDACTED]", text)

    def complete(self, system, messages):
        cmd = [self._claude_bin, "-p", "--bare", "--disable-slash-commands", "--strict-mcp-config",
               "--tools", "", "--model", self.model, "--output-format", "json", "--max-turns", "1",
               "--append-system-prompt", system]
        env = dict(os.environ)
        env.update({
            "ANTHROPIC_BASE_URL": self._base_url,
            "ANTHROPIC_AUTH_TOKEN": self._api_key,
            "ANTHROPIC_API_KEY": "",
            "MAX_THINKING_TOKENS": "0",
        })
        try:
            proc = self._runner(cmd, input=render_transcript(messages), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=env, timeout=self._timeout_s)
        except subprocess.TimeoutExpired:
            raise GatewayError(f"claude CLI timed out after {self._timeout_s}s", transient=True, kind="server")
        except OSError as exc:
            raise GatewayError(f"claude CLI could not start: {exc}", transient=False, kind="client")

        data = None
        for line in reversed((proc.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if data is None:
            # redact BEFORE truncating, so a cut can never leave part of the key (security pass M2)
            snippet = self._redact(proc.stdout or "")[:200] + self._redact(proc.stderr or "")[:200]
            raise GatewayError(f"unparseable claude output (rc={proc.returncode}): {snippet}",
                               transient=True, kind="server")

        result = str(data.get("result") or "")
        if data.get("is_error"):
            transient, kind = _classify(result)
            raise GatewayError(self._redact(result)[:300], transient=transient, kind=kind)

        model_usage = (data.get("modelUsage") or {}).get(self.model) or {}
        usage = Usage(backend=self.backend, model=self.model,
                      input_tokens=int(model_usage.get("inputTokens") or 0),
                      output_tokens=int(model_usage.get("outputTokens") or 0),
                      duration_ms=int(data.get("duration_ms") or 0),
                      cost_usd_estimate=model_usage.get("costUSD"))
        return Completion(text=result, usage=usage)


def _http_post_json(url, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class OllamaGateway:
    """Local fallback model through Ollama's /api/chat."""

    backend = "ollama"

    def __init__(self, model, url="http://localhost:11434", timeout_s=900, post=_http_post_json):
        self.model = model
        self._url = url.rstrip("/")
        self._timeout_s = timeout_s
        self._post = post

    def complete(self, system, messages):
        body = {"model": self.model, "stream": False, "think": False, "format": "json",
                "options": {"temperature": 0, "num_ctx": 16384},
                "messages": [{"role": "system", "content": system}] + list(messages)}
        started = time.monotonic()
        try:
            data = self._post(f"{self._url}/api/chat", body, self._timeout_s)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GatewayError(f"ollama unreachable: {exc}", transient=True, kind="server")
        duration_ms = int((data.get("total_duration") or 0) / 1e6) or int((time.monotonic() - started) * 1000)
        usage = Usage(backend=self.backend, model=self.model,
                      input_tokens=int(data.get("prompt_eval_count") or 0),
                      output_tokens=int(data.get("eval_count") or 0),
                      duration_ms=duration_ms, cost_usd_estimate=0.0)
        return Completion(text=(data.get("message") or {}).get("content", ""), usage=usage)
