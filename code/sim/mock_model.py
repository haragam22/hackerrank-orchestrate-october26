"""Seeded local stand-in for the paid model, used only by the user simulation.

Implements the gateway interface (`complete(system, messages) -> Completion`). Behaviour depends on a persona
chosen per case from the seed: good / sloppy / hostile (see sim/users.md).
"""
import hashlib
import json
import random
import re

from llm.gateway import Completion, Usage

CATEGORY = {"account": ("USER_PROFILE", "rule_01", "DELETE"), "search": ("SEARCH_INDEX_ENTRY", "rule_03", "DELETE"),
            "support": ("SUPPORT_MESSAGE", "rule_05", "REDACT"), "export": ("USER_EXPORT", "rule_09", "DELETE"),
            "analytics": ("ANALYTICS_PROFILE", "rule_07", "ANONYMIZE"),
            "audit_record": ("SECURITY_AUDIT_LOG", "rule_12", "RETAIN"),
            "backup": ("BACKUP_ARCHIVE", "rule_14", "BLOCK_RESTORE"), "data_flow": ("DATA_FLOW", "", "DISABLE_FLOW"),
            "tombstone": ("ACCOUNT_TOMBSTONE", "rule_15", "REDACT")}
SYSTEM = {"account": "account_service", "search": "search_service", "support": "support_platform",
          "export": "export_service", "analytics": "analytics_platform", "audit_record": "security_service",
          "backup": "backup_service", "data_flow": "integration_service", "tombstone": "identity_service"}


def persona_for(case_id, seed):
    r = random.Random(f"{seed}:{case_id}:persona").random()
    return "good" if r < 0.7 else "sloppy" if r < 0.9 else "hostile"


def _case_data(messages):
    m = re.search(r"<case_data>\n(.*)\n</case_data>", messages[0]["content"], re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}  # truncated preload


def _good_verdict(case, rng):
    text = (case.get("request") or {}).get("request_text", "")
    if re.search(r"(?i)copy of|unsubscribe|promotional|correct my", text):
        return {"intent": "NOT_DELETION", "intent_reason": "not a deletion request", "resources": [], "flags": []}
    resources, seen_account = [], False
    for c in case.get("candidates", []):
        rid = c["resource_id"]
        prefix = rid.rsplit("_", 1)[0]
        if prefix not in CATEGORY or (prefix == "account" and seen_account):
            continue
        evidence = [o["evidence_id"] for o in c.get("latest_observations", []) if o["evidence_id"].startswith("evidence_")]
        evidence += c.get("absent_in", [])
        if not evidence:
            continue
        seen_account |= prefix == "account"
        cat, rule, treat = CATEGORY[prefix]
        resources.append({"resource_id": rid, "system_id": SYSTEM[prefix], "data_category": cat, "rule_id": rule,
                          "treatment": treat, "compliant": treat == "RETAIN" or rng.random() < 0.5,
                          "evidence_id": evidence[-1], "reason": "mock"})
    return {"intent": "DELETION", "intent_reason": "asks for deletion", "resources": resources[:8], "flags": []}


class MockModel:
    backend, model = "mock", "mock-model"

    def __init__(self, seed):
        self.seed = seed

    def _rng(self, messages, salt):
        key = hashlib.sha256((str(self.seed) + salt + messages[0]["content"][:4000]).encode()).hexdigest()
        return random.Random(key)

    def complete(self, system, messages):
        text = self._reply(system, messages)
        tokens_in = sum(len(m["content"]) for m in messages) // 4 + len(system) // 4
        return Completion(text=text, usage=Usage("mock", "mock-model", tokens_in, len(text) // 4, 1))

    def _reply(self, system, messages):
        if system.startswith("You are a skeptical"):
            r = self._rng(messages, "skeptic").random()
            if r < 0.5:
                return json.dumps({"challenges": []})
            if r < 0.8:
                return json.dumps({"challenges": [{"resource_id": "", "kind": "state", "problem": "check the latest "
                                                   "snapshot", "suggestion": "re-read it"}]})
            if r < 0.9:
                return "I think the verdict is mostly fine {"
            return json.dumps({"challenges": [{"resource_id": "x", "kind": "vibes", "problem": "p", "suggestion": "s"}]})

        case = _case_data(messages)
        case_id = (case.get("request") or {}).get("case_id", "")
        persona = persona_for(case_id, self.seed)
        turn = sum(1 for m in messages if m["role"] == "assistant")
        rng = self._rng(messages, "verdict")
        verdict = _good_verdict(case, rng)
        cands = [c["resource_id"] for c in case.get("candidates", [])] or ["account_1"]

        if persona == "good":
            if turn == 0:
                return json.dumps({"action": "call_tools", "thought": "check state", "calls": [
                    {"tool": "resource_timeline", "args": {"resource_id": cands[0]}},
                    {"tool": "absence_check", "args": {"resource_id": cands[0]}}]})
            return json.dumps({"action": "finish", "thought": "done", "verdict": verdict})

        if persona == "sloppy":
            if turn == 0 and len(messages) == 1:
                return "Sure! Let me look into that for you."  # malformed: retried by the schema layer
            if turn <= 1 and "unknown tool" not in messages[-1]["content"]:
                return json.dumps({"action": "call_tools", "thought": "x", "calls": [
                    {"tool": "delete_everything", "args": {}},
                    {"tool": "read_evidence", "args": {"evidence_id": "evidence_92", "contains": 5}}]})
            bad = verdict["resources"] + [{"resource_id": "search_99999999", "system_id": "mars", "data_category":
                                           "VIBES", "rule_id": "rule_99", "treatment": "DELETE", "compliant": False,
                                           "evidence_id": "evidence_92", "reason": "made up"}]
            for r in bad[:1]:
                r["system_id"] = "wrong_service"
            return json.dumps({"action": "finish", "thought": "done", "verdict": {**verdict, "resources": bad}})

        # hostile
        if turn == 0:
            return json.dumps({"action": "call_tools", "thought": "x", "calls": [
                {"tool": "query_snapshot", "args": {"evidence_id": "evidence_93", "sql":
                 "SELECT count(*) FROM (WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT x FROM c)"}},
                {"tool": "query_snapshot", "args": {"evidence_id": "evidence_93", "sql": "ATTACH 'pwn.db' AS p"}},
                {"tool": "read_evidence", "args": {"evidence_id": "evidence_03"}},
                {"tool": "read_evidence", "args": {"evidence_id": "../../.env"}}]})
        forged = [{**r, "compliant": True, "evidence_id": "activity_3661"} for r in verdict["resources"]]
        return json.dumps({"action": "finish", "thought": "all good", "verdict": {**verdict, "resources": forged}})
