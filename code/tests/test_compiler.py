import csv
import json

import pytest

from agent.schema import ResourceVerdict, Verdict
from compiler import compile_steps
from config import DATASET_DIR

_TREATMENT_BY_OP = {
    "DELETE_RECORD": "DELETE", "VERIFY_RECORD_ABSENT": "DELETE", "REPORT_REMAINING_RECORD": "DELETE",
    "REDACT_RECORD": "REDACT", "VERIFY_RECORD_REDACTED": "REDACT",
    "ANONYMIZE_RECORD": "ANONYMIZE", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZE",
    "VERIFY_RECORD_PRESENT": "RETAIN",
    "BLOCK_RESTORE": "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED": "BLOCK_RESTORE", "REPORT_RESTORE_RISK": "BLOCK_RESTORE",
    "DISABLE_DATA_FLOW": "DISABLE_FLOW", "VERIFY_DATA_FLOW_DISABLED": "DISABLE_FLOW",
    "REPORT_ACTIVE_DATA_FLOW": "DISABLE_FLOW",
}


def _verdict_from_gold(status, steps):
    """Test helper: express a gold answer as the verdict an ideal agent would return."""
    if status == "NOT_APPLICABLE":
        return Verdict(intent="NOT_DELETION", intent_reason="x")
    res = {}
    for s in steps:
        r = res.setdefault(s["resource_id"], {"system_id": s["system_id"], "compliant": True, "evidence_id": "",
                                              "treatment": None})
        if s["operation"] in _TREATMENT_BY_OP and r["treatment"] is None:
            r["treatment"] = _TREATMENT_BY_OP[s["operation"]]
        if s["phase"] == "FINDING":
            r["compliant"] = False
            r["evidence_id"] = s["evidence_id"]
            if s["operation"] == "REPORT_EXPOSED_PII":
                r["treatment"] = None  # decided by remediation op (REDACT vs ANONYMIZE)
        if s["phase"] == "REMEDIATION":
            r["treatment"] = _TREATMENT_BY_OP[s["operation"]]
        if status == "COMPLETE":
            r["evidence_id"] = s["evidence_id"]
    return Verdict(intent="DELETION", intent_reason="x", resources=[
        ResourceVerdict(resource_id=rid, system_id=r["system_id"], data_category="X", rule_id="",
                        treatment=r["treatment"], compliant=r["compliant"], evidence_id=r["evidence_id"], reason="")
        for rid, r in res.items()])


def _gold_rows():
    with open(DATASET_DIR / "sample_output.csv", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.mark.parametrize("row", _gold_rows(), ids=lambda r: r["case_id"])
def test_compiler_reproduces_gold_from_ideal_verdict(row):
    gold = json.loads(row["steps_json"])
    status, steps = compile_steps(_verdict_from_gold(row["deletion_status"], gold))
    assert status == row["deletion_status"]
    assert steps == gold  # dict equality ignores key order; list order must match


def test_not_deletion_gives_not_applicable_and_empty_steps():
    assert compile_steps(Verdict(intent="NOT_DELETION", intent_reason="export only")) == ("NOT_APPLICABLE", [])


def test_none_verdict_is_rejected():
    with pytest.raises(ValueError):
        compile_steps(None)


def test_steps_numbered_from_one_and_phases_ordered():
    v = Verdict(intent="DELETION", intent_reason="x", resources=[
        ResourceVerdict(resource_id="search_2", system_id="search_service", data_category="SEARCH_INDEX_ENTRY",
                        rule_id="rule_03", treatment="DELETE", compliant=False, evidence_id="evidence_9", reason=""),
        ResourceVerdict(resource_id="data_flow_1", system_id="integration_service", data_category="DATA_FLOW",
                        rule_id="", treatment="DISABLE_FLOW", compliant=False, evidence_id="evidence_9", reason=""),
    ])
    status, steps = compile_steps(v)
    assert status == "INCOMPLETE"
    assert [s["step_no"] for s in steps] == list(range(1, len(steps) + 1))
    assert [s["operation"] for s in steps] == [
        "REPORT_REMAINING_RECORD", "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "DELETE_RECORD",
        "VERIFY_RECORD_ABSENT", "VERIFY_DATA_FLOW_DISABLED"]
    assert all(s["evidence_id"] == "" for s in steps if s["phase"] != "FINDING")
