from agent.schema import ResourceVerdict, Verdict
from case_context import CaseContext
from ingest.extract import make_record
from ingest.loader import EvidenceStore
from people import People
from validator import finalize, validate
from world import World

REQ = {"case_id": "case_1", "email": "ana@x.com", "requested_at": "2026-09-01T00:00:00Z",
       "reported_status": "COMPLETE", "reported_at": "2026-09-02T12:00:00Z", "audit_at": "2026-09-05T00:00:00Z",
       "request_text": "delete my account"}
RULES = [{"rule_id": "rule_01", "data_category": "USER_PROFILE", "required_action": "DELETE"},
         {"rule_id": "rule_03", "data_category": "SEARCH_INDEX_ENTRY", "required_action": "DELETE"},
         {"rule_id": "rule_12", "data_category": "SECURITY_AUDIT_LOG", "required_action": "RETAIN"},
         {"rule_id": "rule_14", "data_category": "BACKUP_ARCHIVE", "required_action": "RETAIN"}]


def _ctx():
    recs = [make_record("e1", "json", "2026-09-03T00:00:00Z", "f", 1, "account_1 email=ana@x.com status_code=404"),
            make_record("e1", "json", "2026-09-03T00:00:00Z", "f", 2, "search_2 email=ana@x.com"),
            make_record("e1", "json", "2026-09-03T00:00:00Z", "f", 3, "audit_record_3 subject_reference=user_1"),
            make_record("e9", "json", "2026-09-09T00:00:00Z", "f", 1, "search_2 email=ana@x.com"),
            make_record("e5", "json", "2026-09-03T00:00:00Z", "f", 1, "search_77 email=someone@else.com")]
    store = EvidenceStore(records=recs, files={"e1": "a.json", "e9": "b.json", "e5": "c.json"},
                          captured={"e1": "2026-09-03T00:00:00Z", "e9": "2026-09-09T00:00:00Z",
                                    "e5": "2026-09-03T00:00:00Z"}, coverage={})
    people = People([{"user_id": "user_1", "name": "Ana", "email": "ana@x.com", "phone": "", "email_hash": "",
                      "updated_at": ""}], [])
    return CaseContext(World(store, people, [REQ]), REQ)


def _r(rid, system, cat, rule, treat, ok, ev):
    return ResourceVerdict(resource_id=rid, system_id=system, data_category=cat, rule_id=rule, treatment=treat,
                           compliant=ok, evidence_id=ev, reason="r")


GOOD = [_r("account_1", "account_service", "USER_PROFILE", "rule_01", "DELETE", True, "e1"),
        _r("search_2", "search_service", "SEARCH_INDEX_ENTRY", "rule_03", "DELETE", False, "e1"),
        _r("audit_record_3", "security_service", "SECURITY_AUDIT_LOG", "rule_12", "RETAIN", True, "e1")]


def test_valid_verdict_has_no_problems():
    assert validate(Verdict(intent="DELETION", intent_reason="x", resources=GOOD), _ctx(), RULES) == []


def test_not_deletion_needs_no_resources():
    ctx = _ctx()
    ctx.request = dict(ctx.request, request_text="Please send me a copy of my data.")
    assert validate(Verdict(intent="NOT_DELETION", intent_reason="export"), ctx, RULES) == []


def test_unknown_unlinked_and_duplicate_resources_are_reported():
    bad = GOOD + [_r("search_999", "search_service", "SEARCH_INDEX_ENTRY", "rule_03", "DELETE", False, "e1"),
                  _r("search_77", "search_service", "SEARCH_INDEX_ENTRY", "rule_03", "DELETE", False, "e5"),
                  GOOD[1]]
    problems = " | ".join(validate(Verdict(intent="DELETION", intent_reason="x", resources=bad), _ctx(), RULES))
    assert "search_999" in problems and "not linked" in problems
    assert "search_77" in problems
    assert "duplicate" in problems


def test_evidence_after_audit_or_not_mentioning_resource_is_reported():
    bad = [GOOD[0], _r("search_2", "search_service", "SEARCH_INDEX_ENTRY", "rule_03", "DELETE", False, "e9"),
           _r("audit_record_3", "security_service", "SECURITY_AUDIT_LOG", "rule_12", "RETAIN", True, "e5")]
    problems = " | ".join(validate(Verdict(intent="DELETION", intent_reason="x", resources=bad), _ctx(), RULES))
    assert "e9" in problems and "after audit_at" in problems
    assert "e5" in problems and "does not mention" in problems


def test_activity_rows_cannot_be_cited_as_state_proof():
    ctx = _ctx()
    ctx.world.store.records.append(make_record("activity_7", "activity", "2026-09-03T00:00:00Z", "a", 0,
                                               "REDACT_RECORD search_2 SUCCESS"))
    ctx.world.by_resource["search_2"].append(ctx.world.store.records[-1])
    bad = [GOOD[0], _r("search_2", "search_service", "SEARCH_INDEX_ENTRY", "rule_03", "DELETE", False,
                       "activity_7"), GOOD[2]]
    problems = " | ".join(validate(Verdict(intent="DELETION", intent_reason="x", resources=bad), ctx, RULES))
    assert "activity_7" in problems and "does not show the record's state" in problems


def test_treatment_category_and_rule_mismatches_are_reported():
    bad = [_r("account_1", "account_service", "USER_PROFILE", "rule_03", "REDACT", True, "e1")]
    problems = " | ".join(validate(Verdict(intent="DELETION", intent_reason="x", resources=bad), _ctx(), RULES))
    assert "treatment" in problems and "rule_id" in problems


def test_retain_must_be_compliant_and_account_count_checked():
    bad = [_r("audit_record_3", "security_service", "SECURITY_AUDIT_LOG", "rule_12", "RETAIN", False, "e1")]
    problems = " | ".join(validate(Verdict(intent="DELETION", intent_reason="x", resources=bad), _ctx(), RULES))
    assert "RETAIN" in problems and "account_" in problems


def test_finalize_drops_invalid_resources_and_flags_them():
    bad = GOOD + [_r("search_999", "search_service", "SEARCH_INDEX_ENTRY", "rule_03", "DELETE", False, "e1")]
    verdict, flags = finalize(Verdict(intent="DELETION", intent_reason="x", resources=bad), _ctx(), RULES)
    assert [r.resource_id for r in verdict.resources] == ["account_1", "search_2", "audit_record_3"]
    assert any("search_999" in f for f in flags)


def test_finalize_repairs_bad_citation_with_latest_visible_evidence_and_flags():
    bad = [GOOD[0], _r("search_2", "search_service", "SEARCH_INDEX_ENTRY", "rule_03", "DELETE", False, "e9"), GOOD[2]]
    verdict, flags = finalize(Verdict(intent="DELETION", intent_reason="x", resources=bad), _ctx(), RULES)
    assert verdict.resources[1].evidence_id == "e1"
    assert any("citation" in f for f in flags)
