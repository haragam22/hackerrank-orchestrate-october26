import json

import pytest

from agent.loop import investigate
from agent.prompts import SYSTEM_PROMPT
from llm.gateway import Completion, Usage
from tools import ToolError


class FakeToolbox:
    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "boom":
            raise ToolError("unknown tool 'boom'")
        return {"tool": name, "args": args, "data": "ok"}

    def preload(self):
        return {"request": {"case_id": "case_x"}, "identity": {}, "candidates": [], "rules": []}


class ScriptedGateway:
    backend, model = "fake", "m"

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def complete(self, system, messages):
        self.seen.append((system, [dict(m) for m in messages]))
        return Completion(text=self.replies.pop(0), usage=Usage("fake", "m", 10, 5, 1))


VERDICT = {"intent": "DELETION", "intent_reason": "asks to delete", "resources": [
    {"resource_id": "account_1", "system_id": "account_service", "data_category": "USER_PROFILE", "rule_id": "rule_01",
     "treatment": "DELETE", "compliant": True, "evidence_id": "evidence_1", "reason": "404 in export"}], "flags": []}


def _finish(verdict=VERDICT):
    return json.dumps({"action": "finish", "thought": "done", "verdict": verdict})


def _calls(*calls):
    return json.dumps({"action": "call_tools", "thought": "look",
                       "calls": [{"tool": t, "args": a} for t, a in calls]})


def test_loop_runs_tools_then_returns_verdict():
    box = FakeToolbox()
    gw = ScriptedGateway([_calls(("resource_timeline", {"resource_id": "account_1"}),
                                 ("absence_check", {"resource_id": "account_1"})), _finish()])
    result = investigate(gw, box, max_turns=5)
    assert result.verdict.resources[0].resource_id == "account_1"
    assert box.calls == [("resource_timeline", {"resource_id": "account_1"}),
                         ("absence_check", {"resource_id": "account_1"})]
    assert result.turns == 2 and result.forced_finish is False
    second_prompt = gw.seen[1][1][-1]["content"]
    assert "resource_timeline" in second_prompt and '"data": "ok"' in second_prompt


def test_first_message_preloads_case_and_system_prompt_is_used():
    gw = ScriptedGateway([_finish()])
    investigate(gw, FakeToolbox(), max_turns=3)
    system, messages = gw.seen[0]
    assert system.startswith(SYSTEM_PROMPT)
    assert '"case_id": "case_x"' in messages[0]["content"]
    assert "<case_data>" in messages[0]["content"]


def test_tool_errors_are_fed_back_not_raised():
    gw = ScriptedGateway([_calls(("boom", {})), _finish()])
    result = investigate(gw, FakeToolbox(), max_turns=3)
    assert result.verdict is not None
    assert "unknown tool 'boom'" in gw.seen[1][1][-1]["content"]


def test_turn_cap_forces_finish():
    gw = ScriptedGateway([_calls(("get_rules", {}))] * 2 + [_finish()])
    result = investigate(gw, FakeToolbox(), max_turns=2)
    assert result.forced_finish is True and result.verdict is not None
    assert "must finish now" in gw.seen[2][1][-1]["content"].lower()


def test_malformed_reply_is_retried_by_schema_layer():
    gw = ScriptedGateway(["not json at all", _finish()])
    result = investigate(gw, FakeToolbox(), max_turns=3)
    assert result.verdict is not None and len(result.completions) == 2


def test_inconsistent_step_is_rejected_and_retried():
    bad = json.dumps({"action": "finish", "thought": "x"})  # finish without verdict
    gw = ScriptedGateway([bad, _finish()])
    result = investigate(gw, FakeToolbox(), max_turns=3)
    assert result.verdict is not None


def test_unrecoverable_model_failure_returns_none_verdict_with_flag():
    gw = ScriptedGateway(["x", "y", "z"])
    result = investigate(gw, FakeToolbox(), max_turns=3)
    assert result.verdict is None and any("invalid" in f for f in result.flags)


def test_validator_problems_are_sent_back_for_revision():
    fixed = dict(VERDICT, intent_reason="revised")
    gw = ScriptedGateway([_finish(), _finish(fixed)])
    checks = []

    def validate(v):
        checks.append(v.intent_reason)
        return ["account_1: evidence e9 was captured after audit_at"] if len(checks) == 1 else []

    result = investigate(gw, FakeToolbox(), max_turns=3, validate=validate)
    assert result.verdict.intent_reason == "revised" and result.validation_rounds == 1
    assert "evidence e9 was captured after audit_at" in gw.seen[1][1][-1]["content"]


def test_validation_rounds_are_bounded():
    gw = ScriptedGateway([_finish()] * 3)
    result = investigate(gw, FakeToolbox(), max_turns=3, validate=lambda v: ["still wrong"],
                         max_validation_rounds=2)
    assert result.validation_rounds == 2 and len(gw.seen) == 3
    assert result.verdict is not None and any("still wrong" in f for f in result.flags)


def test_review_runs_once_after_valid_finish_and_investigator_keeps_final_say():
    revised = dict(VERDICT, intent_reason="after review")
    gw = ScriptedGateway([_finish(), _calls(("resource_timeline", {"resource_id": "account_1"})), _finish(revised)])
    reviews = []

    def review(v):
        reviews.append(v.intent_reason)
        return ["account_1: ownership is burst-only; check the status match"]

    result = investigate(gw, FakeToolbox(), max_turns=3, review=review)
    assert reviews == ["asks to delete"]          # reviewed exactly once
    assert result.verdict.intent_reason == "after review"
    assert result.reviewed is True
    assert "ownership is burst-only" in gw.seen[1][1][-1]["content"]


def test_review_with_no_challenges_finishes_immediately():
    gw = ScriptedGateway([_finish()])
    result = investigate(gw, FakeToolbox(), max_turns=3, review=lambda v: [])
    assert result.verdict is not None and len(gw.seen) == 1


def test_large_tool_results_are_truncated():
    class Big(FakeToolbox):
        def call(self, name, args):
            return {"blob": "x" * 50000}

    gw = ScriptedGateway([_calls(("read_evidence", {"evidence_id": "e"})), _finish()])
    investigate(gw, Big(), max_turns=3)
    assert len(gw.seen[1][1][-1]["content"]) < 12000
