import csv
import json

import pytest

from config import DATASET_DIR
from llm.gateway import Completion, Usage
from pipeline import audit_case, build_world


class ScriptedGateway:
    backend, model = "fake", "m"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, messages):
        self.calls += 1
        return Completion(text=self.replies.pop(0), usage=Usage("fake", "m", 10, 5, 1))


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return build_world(DATASET_DIR, tmp_path_factory.mktemp("cache"))


def _row(name, case_id):
    with open(DATASET_DIR / name, encoding="utf-8", newline="") as fh:
        return next(r for r in csv.DictReader(fh) if r["case_id"] == case_id)


def _finish(resources, intent="DELETION"):
    return json.dumps({"action": "finish", "thought": "t", "verdict": {
        "intent": intent, "intent_reason": "r", "resources": resources, "flags": []}})


CASE_09 = [
    {"resource_id": "account_3960", "system_id": "account_service", "data_category": "USER_PROFILE",
     "rule_id": "rule_01", "treatment": "DELETE", "compliant": True, "evidence_id": "evidence_93", "reason": "r"},
    {"resource_id": "backup_4978", "system_id": "backup_service", "data_category": "BACKUP_ARCHIVE",
     "rule_id": "rule_14", "treatment": "BLOCK_RESTORE", "compliant": False, "evidence_id": "evidence_93",
     "reason": "restore_allowed 1"},
]


def test_audit_case_end_to_end_matches_gold_except_unreachable_audit_record(world):
    result = audit_case(world, _row("sample_deletion_requests.csv", "case_09"), ScriptedGateway([_finish(CASE_09)]),
                        max_turns=3)
    assert result.ok and result.deletion_status == "INCOMPLETE"
    gold = json.loads(_row("sample_output.csv", "case_09")["steps_json"])
    reachable = [s for s in gold if s["resource_id"] != "audit_record_4902"]
    assert [{k: v for k, v in s.items() if k != "step_no"} for s in result.steps] == \
           [{k: v for k, v in s.items() if k != "step_no"} for s in reachable]


def test_validator_feedback_then_finalize_drops_unsupported_resource(world):
    bad = CASE_09 + [{"resource_id": "search_123456", "system_id": "search_service",
                      "data_category": "SEARCH_INDEX_ENTRY", "rule_id": "rule_03", "treatment": "DELETE",
                      "compliant": False, "evidence_id": "evidence_93", "reason": "made up"}]
    gw = ScriptedGateway([_finish(bad)] * 3)
    result = audit_case(world, _row("sample_deletion_requests.csv", "case_09"), gw, max_turns=3)
    assert gw.calls == 3  # first answer + 2 revision rounds
    assert all(s["resource_id"] != "search_123456" for s in result.steps)
    assert any("dropped search_123456" in f for f in result.flags)


def test_skeptic_challenge_triggers_revision_and_is_flagged(world):
    challenge = json.dumps({"challenges": [{"resource_id": "backup_4978", "kind": "state",
                                            "problem": "check restore flag", "suggestion": "re-read"}]})
    gw = ScriptedGateway([_finish(CASE_09), challenge, _finish(CASE_09)])
    result = audit_case(world, _row("sample_deletion_requests.csv", "case_09"), gw, max_turns=3, use_skeptic=True)
    assert gw.calls == 3 and result.deletion_status == "INCOMPLETE"
    assert any("skeptic challenged" in f for f in result.flags)
    assert len(result.completions) == 3   # investigator x2 + skeptic, all counted for usage


@pytest.mark.parametrize("override,needle", [
    ({"audit_at": "yesterday"}, "audit_at"),
    ({"email": ""}, "email"),
    ({"email": "not-an-email"}, "email"),
    ({"reported_at": "2026-13-45T99:00:00Z"}, "reported_at"),
    ({"case_id": ""}, "case_id"),
    ({"reported_status": "MAYBE"}, "reported_status"),
])
def test_invalid_requests_are_rejected_before_any_model_call(world, override, needle):
    gw = ScriptedGateway([])
    req = dict(_row("sample_deletion_requests.csv", "case_09"), **override)
    result = audit_case(world, req, gw, max_turns=3)
    assert gw.calls == 0 and result.ok is False
    assert result.deletion_status == "INCOMPLETE" and result.steps == []
    assert any(f.startswith("INVALID REQUEST") and needle in f for f in result.flags)


def test_every_real_request_passes_input_validation():
    from pipeline import request_problems
    from world import load_requests
    reqs = load_requests(DATASET_DIR)
    assert len(reqs) == 550
    assert {r["case_id"]: request_problems(r) for r in reqs if request_problems(r)} == {}


def test_unknown_email_is_valid_input_but_flagged(world):
    req = dict(_row("sample_deletion_requests.csv", "case_09"), email="nobody@nowhere.example")
    result = audit_case(world, req, ScriptedGateway([_finish([], intent="DELETION")] * 3), max_turns=3)
    assert result.ok is False and any("EMPTY RESULT" in f for f in result.flags)


def test_all_resources_dropped_is_not_ok(world):
    # security review F4: an unsupported-only verdict must not become a silent "COMPLETE, []"
    bogus = [{"resource_id": "account_99999999", "system_id": "account_service", "data_category": "USER_PROFILE",
              "rule_id": "rule_01", "treatment": "DELETE", "compliant": True, "evidence_id": "evidence_54",
              "reason": "x"}]
    result = audit_case(world, _row("sample_deletion_requests.csv", "case_05"),
                        ScriptedGateway([_finish(bogus)] * 3), max_turns=3)
    # Final-run decision: an empty deletion verdict gets fallback findings (still ok=False), else EMPTY RESULT.
    assert result.ok is False and any(("EMPTY RESULT" in f or "FALLBACK FINDINGS" in f) for f in result.flags)
    assert (result.deletion_status, result.steps) != ("COMPLETE", [])


def test_not_deletion_yields_not_applicable(world):
    export = dict(_row("sample_deletion_requests.csv", "case_01"), request_text="Please send me a copy of my data.")
    result = audit_case(world, export, ScriptedGateway([_finish([], intent="NOT_DELETION")]), max_turns=3)
    assert (result.deletion_status, result.steps) == ("NOT_APPLICABLE", [])


def test_not_deletion_despite_deletion_wording_is_questioned_but_agent_decides(world):
    # security review F9: questioned (2 validator rounds), the agent keeps the final say, the doubt is flagged
    gw = ScriptedGateway([_finish([], intent="NOT_DELETION")] * 3)
    result = audit_case(world, _row("sample_deletion_requests.csv", "case_01"), gw, max_turns=3)
    assert gw.calls == 3 and result.deletion_status == "NOT_APPLICABLE"
    assert any("deletion wording" in f for f in result.flags)


def test_agent_failure_gives_flagged_placeholder(world):
    result = audit_case(world, _row("sample_deletion_requests.csv", "case_01"),
                        ScriptedGateway(["x", "y", "z"]), max_turns=3)
    assert result.ok is False and any("NO VERDICT" in f for f in result.flags)
