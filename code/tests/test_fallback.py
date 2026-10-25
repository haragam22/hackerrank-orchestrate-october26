"""Empty deletion verdicts get deterministic fallback findings (final-run decision, case_55 / case_61 shape)."""
import csv
import json

import pytest

from tests.test_security_fixes import tool_world  # noqa: F401  (fixture re-export)


def _req(case_id):
    from config import DATASET_DIR
    with open(DATASET_DIR / "deletion_requests.csv", encoding="utf-8", newline="") as fh:
        return next(r for r in csv.DictReader(fh) if r["case_id"] == case_id)


def _empty_gateway():
    from llm.gateway import Completion, Usage
    reply = json.dumps({"action": "finish", "verdict": {"intent": "DELETION", "intent_reason": "deletion requested",
                                                         "resources": [], "flags": []}})

    class GW:
        backend = model = "f"

        def complete(self, s, m):
            return Completion(reply, Usage("f", "f"))
    return GW()


@pytest.mark.parametrize("case_id,rid,finding,evidence", [
    ("case_55", "backup_9287", "REPORT_RESTORE_RISK", "evidence_32"),
    ("case_61", "object_19", "REPORT_REMAINING_RECORD", "activity_4007"),
])
def test_empty_deletion_verdict_reports_unproven_linked_records(tool_world, case_id, rid, finding, evidence):
    from pipeline import audit_case
    res = audit_case(tool_world, _req(case_id), _empty_gateway(), max_turns=1)
    assert res.deletion_status == "INCOMPLETE"
    first = res.steps[0]
    assert (first["phase"], first["resource_id"], first["operation"], first["evidence_id"]) == \
        ("FINDING", rid, finding, evidence)
    assert any(s["phase"] == "VERIFICATION" for s in res.steps)
    assert res.ok is False and any(f.startswith("FALLBACK FINDINGS") for f in res.flags)


def test_fallback_skips_records_owned_by_someone_else(tool_world):
    from case_context import CaseContext
    from config import DATASET_DIR
    from validator import fallback_resources, load_rules
    req = next(r for r in csv.DictReader(open(DATASET_DIR / "sample_deletion_requests.csv", encoding="utf-8"))
               if r["case_id"] == "case_15")
    rids = {r.resource_id for r in fallback_resources(CaseContext(tool_world, req), load_rules(DATASET_DIR))}
    assert "data_flow_1121" not in rids  # catalogued to user_14, not the subject
