import json
import subprocess

import pytest
from pydantic import BaseModel

from config import load_env_file
from llm.cache import CachedGateway
from llm.gateway import ClaudeCodeGateway, Completion, GatewayError, OllamaGateway, Usage
from llm.jsoncall import JsonCallError, call_json, extract_json
from llm.router import FallbackGateway
from llm.usage import UsageLog

SECRET = "sk-test-secret-value-1234567890"


# ---------- helpers ----------

def _usage(backend="fake", model="m"):
    return Usage(backend=backend, model=model, input_tokens=10, output_tokens=5, duration_ms=1)


class ScriptedGateway:
    """External-boundary fake: returns scripted texts or raises scripted errors."""

    def __init__(self, script, backend="fake", model="m"):
        self.script = list(script)
        self.backend = backend
        self.model = model
        self.calls = []

    def complete(self, system, messages):
        self.calls.append((system, [dict(m) for m in messages]))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return Completion(text=item, usage=_usage(self.backend, self.model))


def _claude_stdout(result, is_error=False, model="gpt-6.1-sol"):
    payload = {
        "type": "result",
        "is_error": is_error,
        "result": result,
        "duration_ms": 1234,
        "modelUsage": {} if is_error else {
            model: {"inputTokens": 700, "outputTokens": 40, "costUSD": 0.003}
        },
    }
    return json.dumps(payload)


class FakeRunner:
    """Fake for subprocess.run (external process boundary)."""

    def __init__(self, stdout="", returncode=0, exc=None):
        self.stdout = stdout
        self.returncode = returncode
        self.exc = exc
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.stdout, stderr="")


class Verdict(BaseModel):
    status: str
    count: int


# ---------- extract_json ----------

@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Sure, here it is: {"a": {"b": [1, 2]}} done', {"a": {"b": [1, 2]}}),
    ('{"s": "brace } inside"}', {"s": "brace } inside"}),
])
def test_extract_json_handles_fences_and_prose(text, expected):
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2]", "{broken"])
def test_extract_json_rejects_non_objects(text):
    with pytest.raises(ValueError):
        extract_json(text)


# ---------- call_json: schema enforcement + retries ----------

def test_call_json_returns_validated_model_and_puts_schema_in_system():
    gw = ScriptedGateway(['{"status": "ok", "count": 3}'])
    result = call_json(gw, "base system", [{"role": "user", "content": "go"}], Verdict)
    assert result.value == Verdict(status="ok", count=3)
    assert result.attempts == 1
    system_sent = gw.calls[0][0]
    assert system_sent.startswith("base system")
    assert '"count"' in system_sent  # JSON schema included


def test_call_json_retries_malformed_then_succeeds_with_error_feedback():
    gw = ScriptedGateway(["not json", '{"status": "ok"}', '{"status": "ok", "count": 1}'])
    result = call_json(gw, "s", [{"role": "user", "content": "go"}], Verdict, max_retries=2)
    assert result.value.count == 1
    assert result.attempts == 3
    # second call carries the bad reply and a correction message
    second_msgs = gw.calls[1][1]
    assert second_msgs[-2] == {"role": "assistant", "content": "not json"}
    assert "invalid" in second_msgs[-1]["content"].lower()
    # third call explains the validation failure (missing count)
    assert "count" in gw.calls[2][1][-1]["content"]


def test_call_json_raises_after_retries_exhausted():
    gw = ScriptedGateway(["x", "y", "z"])
    with pytest.raises(JsonCallError) as err:
        call_json(gw, "s", [{"role": "user", "content": "go"}], Verdict, max_retries=2)
    assert err.value.attempts == 3
    assert len(gw.calls) == 3


def test_call_json_does_not_mutate_callers_messages():
    msgs = [{"role": "user", "content": "go"}]
    gw = ScriptedGateway(["bad", '{"status": "ok", "count": 1}'])
    call_json(gw, "s", msgs, Verdict)
    assert msgs == [{"role": "user", "content": "go"}]


# ---------- ClaudeCodeGateway ----------

def _claude_gateway(runner):
    return ClaudeCodeGateway(model="gpt-6.1-sol", api_key=SECRET, base_url="https://agentrouter.org",
                             claude_bin="claude", timeout_s=30, runner=runner)


def test_claude_gateway_parses_result_and_usage_and_keeps_key_out_of_args():
    runner = FakeRunner(stdout="warning line\n" + _claude_stdout('{"ok": true}'))
    gw = _claude_gateway(runner)
    out = gw.complete("SYS", [{"role": "user", "content": "hello case"}])
    assert out.text == '{"ok": true}'
    assert out.usage.input_tokens == 700 and out.usage.output_tokens == 40
    assert out.usage.backend == "agentrouter" and out.usage.model == "gpt-6.1-sol"
    cmd, kwargs = runner.calls[0]
    assert SECRET not in " ".join(cmd)
    for flag in ("--bare", "--strict-mcp-config", "--disable-slash-commands", "-p"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--model") + 1] == "gpt-6.1-sol"
    assert cmd[cmd.index("--append-system-prompt") + 1] == "SYS"
    env = kwargs["env"]
    assert env["ANTHROPIC_AUTH_TOKEN"] == SECRET
    assert env["ANTHROPIC_BASE_URL"] == "https://agentrouter.org"
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["MAX_THINKING_TOKENS"] == "0"
    assert "hello case" in kwargs["input"]  # prompt goes over stdin, not argv


def test_claude_gateway_renders_multi_turn_history_into_prompt():
    runner = FakeRunner(stdout=_claude_stdout("{}"))
    gw = _claude_gateway(runner)
    gw.complete("S", [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
                      {"role": "user", "content": "q2"}])
    prompt = runner.calls[0][1]["input"]
    assert prompt.index("q1") < prompt.index("a1") < prompt.index("q2")


@pytest.mark.parametrize("message,transient,kind", [
    ("API Error: 503 server_error", True, "server"),
    ("API Error: 429 rate limited", True, "server"),
    ("API Error: 400 content-blocked (request id: x)", False, "blocked"),
    ("API Error: 400 Function tools with reasoning_effort are not supported", False, "client"),
])
def test_claude_gateway_classifies_api_errors(message, transient, kind):
    gw = _claude_gateway(FakeRunner(stdout=_claude_stdout(message, is_error=True)))
    with pytest.raises(GatewayError) as err:
        gw.complete("S", [{"role": "user", "content": "x"}])
    assert err.value.transient is transient
    assert err.value.kind == kind


def test_claude_gateway_timeout_is_transient():
    gw = _claude_gateway(FakeRunner(exc=subprocess.TimeoutExpired("claude", 30)))
    with pytest.raises(GatewayError) as err:
        gw.complete("S", [{"role": "user", "content": "x"}])
    assert err.value.transient is True


def test_claude_gateway_missing_binary_is_non_transient_client_error():
    gw = _claude_gateway(FakeRunner(exc=FileNotFoundError("claude not found")))
    with pytest.raises(GatewayError) as err:
        gw.complete("S", [{"role": "user", "content": "x"}])
    assert err.value.transient is False and err.value.kind == "client"


def test_claude_gateway_unparseable_output_is_transient_and_redacted():
    gw = _claude_gateway(FakeRunner(stdout=f"garbage {SECRET}", returncode=1))
    with pytest.raises(GatewayError) as err:
        gw.complete("S", [{"role": "user", "content": "x"}])
    assert err.value.transient is True
    assert SECRET not in str(err.value)


# ---------- OllamaGateway ----------

def test_ollama_gateway_posts_chat_and_reads_usage():
    seen = {}

    def fake_post(url, body, timeout):
        seen["url"], seen["body"] = url, body
        return {"message": {"content": '{"a": 1}'}, "prompt_eval_count": 50, "eval_count": 7,
                "total_duration": 2_000_000}

    gw = OllamaGateway(model="qwen3.5:9b-q4_K_M", url="http://localhost:11434", timeout_s=10, post=fake_post)
    out = gw.complete("SYS", [{"role": "user", "content": "hi"}])
    assert out.text == '{"a": 1}'
    assert out.usage.input_tokens == 50 and out.usage.output_tokens == 7
    assert out.usage.backend == "ollama"
    assert seen["url"].endswith("/api/chat")
    assert seen["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert seen["body"]["options"]["temperature"] == 0
    assert seen["body"]["stream"] is False


# ---------- FallbackGateway: retries + flagged fallback ----------

def test_router_primary_success_is_not_flagged():
    primary = ScriptedGateway(["P"], backend="agentrouter")
    router = FallbackGateway(primary, ScriptedGateway([], backend="ollama"), retries=2, sleep=lambda s: None)
    out = router.complete("s", [{"role": "user", "content": "x"}])
    assert out.text == "P" and out.fallback_used is False


def test_router_retries_transient_errors_with_backoff_then_succeeds():
    sleeps = []
    primary = ScriptedGateway([GatewayError("503", transient=True, kind="server"), "P"], backend="agentrouter")
    router = FallbackGateway(primary, None, retries=2, sleep=sleeps.append, base_delay_s=1.0)
    out = router.complete("s", [{"role": "user", "content": "x"}])
    assert out.text == "P" and out.fallback_used is False
    assert sleeps == [1.0]


def test_router_uses_fallback_after_retries_and_flags_it():
    errs = [GatewayError("503", transient=True, kind="server") for _ in range(3)]
    primary = ScriptedGateway(errs, backend="agentrouter")
    fallback = ScriptedGateway(["F"], backend="ollama")
    router = FallbackGateway(primary, fallback, retries=2, sleep=lambda s: None)
    out = router.complete("s", [{"role": "user", "content": "x"}])
    assert out.text == "F"
    assert out.fallback_used is True
    assert "503" in out.fallback_reason
    assert len(primary.calls) == 3


def test_router_non_transient_error_skips_retries_and_goes_to_fallback():
    primary = ScriptedGateway([GatewayError("blocked", transient=False, kind="blocked")], backend="agentrouter")
    fallback = ScriptedGateway(["F"], backend="ollama")
    router = FallbackGateway(primary, fallback, retries=3, sleep=lambda s: None)
    out = router.complete("s", [{"role": "user", "content": "x"}])
    assert out.fallback_used is True and len(primary.calls) == 1


def test_router_without_fallback_raises_last_error():
    primary = ScriptedGateway([GatewayError("503", transient=True, kind="server")] * 2, backend="agentrouter")
    router = FallbackGateway(primary, None, retries=1, sleep=lambda s: None)
    with pytest.raises(GatewayError):
        router.complete("s", [{"role": "user", "content": "x"}])


# ---------- cache ----------

def test_cache_hit_skips_second_call(tmp_path):
    inner = ScriptedGateway(["A"], backend="agentrouter", model="gpt-6.1-sol")
    gw = CachedGateway(inner, tmp_path)
    first = gw.complete("s", [{"role": "user", "content": "x"}])
    second = gw.complete("s", [{"role": "user", "content": "x"}])
    assert first.text == second.text == "A"
    assert first.cached is False and second.cached is True
    assert len(inner.calls) == 1


def test_cache_key_changes_with_prompt(tmp_path):
    inner = ScriptedGateway(["A", "B"], backend="agentrouter", model="gpt-6.1-sol")
    gw = CachedGateway(inner, tmp_path)
    assert gw.complete("s", [{"role": "user", "content": "x"}]).text == "A"
    assert gw.complete("s", [{"role": "user", "content": "y"}]).text == "B"


def test_cache_does_not_store_fallback_answers(tmp_path):
    class FallbackOnce:
        backend, model = "agentrouter", "gpt-6.1-sol"

        def __init__(self):
            self.n = 0

        def complete(self, system, messages):
            self.n += 1
            return Completion(text=f"F{self.n}", usage=_usage("ollama"), fallback_used=True,
                              fallback_reason="503")

    inner = FallbackOnce()
    gw = CachedGateway(inner, tmp_path)
    gw.complete("s", [{"role": "user", "content": "x"}])
    again = gw.complete("s", [{"role": "user", "content": "x"}])
    assert inner.n == 2 and again.cached is False


# ---------- usage log ----------

def test_usage_log_appends_jsonl_with_flags(tmp_path):
    log = UsageLog(tmp_path / "usage.jsonl")
    log.record(Completion(text="t", usage=_usage("ollama", "qwen"), fallback_used=True, fallback_reason="503"),
               case_id="case_09", purpose="investigator")
    rows = [json.loads(l) for l in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows == [{
        "case_id": "case_09", "purpose": "investigator", "backend": "ollama", "model": "qwen",
        "input_tokens": 10, "output_tokens": 5, "duration_ms": 1, "cost_usd": 0.0,
        "cached": False, "fallback_used": True, "fallback_reason": "503",
    }]


def test_usage_cost_uses_configured_prices_and_zero_for_cache(tmp_path):
    log = UsageLog(tmp_path / "u.jsonl", prices={"gpt-6.1-sol": (1.0, 5.0)})
    u = Usage(backend="agentrouter", model="gpt-6.1-sol", input_tokens=1_000_000, output_tokens=200_000)
    assert log.cost(u) == 2.0
    assert log.cost(u, cached=True) == 0.0
    assert log.cost(Usage(backend="agentrouter", model="unknown", input_tokens=5)) is None


# ---------- factory ----------

def _settings(tmp_path, **over):
    from config import Settings
    base = dict(backend="agentrouter", model="gpt-6.1-sol", fallback_backend="ollama",
                fallback_model="qwen", agentrouter_key=SECRET, agentrouter_base_url="https://agentrouter.org",
                ollama_url="http://localhost:11434", claude_bin="claude", timeout_s=30, retries=2,
                parallel=1, max_turns=8, cache_dir=tmp_path)
    base.update(over)
    return Settings(**base)


def test_factory_builds_cache_over_fallback_stack(tmp_path):
    from llm.factory import build_gateway
    gw = build_gateway(_settings(tmp_path))
    assert isinstance(gw, CachedGateway)
    router = gw.inner
    assert isinstance(router, FallbackGateway)
    assert isinstance(router.primary, ClaudeCodeGateway) and isinstance(router.fallback, OllamaGateway)


def test_factory_fallback_none_and_missing_key(tmp_path):
    from llm.factory import ConfigError, build_gateway
    gw = build_gateway(_settings(tmp_path, fallback_backend="none"), use_cache=False)
    assert gw.fallback is None
    with pytest.raises(ConfigError):
        build_gateway(_settings(tmp_path, agentrouter_key=""))


def test_settings_repr_hides_key(tmp_path):
    assert SECRET not in repr(_settings(tmp_path))


# ---------- config ----------

def test_load_env_file_parses_and_does_not_override_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nA_KEY="quoted"\nB_KEY=plain\n\nEXISTING=from_file\n', encoding="utf-8")
    monkeypatch.setenv("EXISTING", "from_env")
    values = load_env_file(env_file)
    assert values == {"A_KEY": "quoted", "B_KEY": "plain", "EXISTING": "from_file"}
    import os
    assert os.environ["EXISTING"] == "from_env"
    assert os.environ["A_KEY"] == "quoted"
