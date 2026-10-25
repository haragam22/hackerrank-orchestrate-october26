"""Deterministic rendering of an accepted Verdict into (deletion_status, steps).

Order: phase (FINDING, REMEDIATION, VERIFICATION), then the operation order of the problem statement's table,
then system_id, then resource_id. Findings cite evidence; remediation and post-remediation verification use
an empty evidence_id; a COMPLETE case has verification steps only, each citing its evidence.
"""

OPERATION_ORDER = [
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
_PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}

# treatment -> (finding op, finding state, remediation op, remediation state, verification op, verification state)
_PLAN = {
    "DELETE": ("REPORT_REMAINING_RECORD", "PRESENT", "DELETE_RECORD", "ABSENT", "VERIFY_RECORD_ABSENT", "ABSENT"),
    "REDACT": ("REPORT_EXPOSED_PII", "PRESENT", "REDACT_RECORD", "REDACTED", "VERIFY_RECORD_REDACTED", "REDACTED"),
    "ANONYMIZE": ("REPORT_EXPOSED_PII", "PRESENT", "ANONYMIZE_RECORD", "ANONYMIZED",
                  "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    "RETAIN": (None, None, None, None, "VERIFY_RECORD_PRESENT", "PRESENT"),
    "BLOCK_RESTORE": ("REPORT_RESTORE_RISK", "ACTIVE", "BLOCK_RESTORE", "BLOCKED", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
    "DISABLE_FLOW": ("REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "DISABLE_DATA_FLOW", "DISABLED",
                     "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
}


def _step(phase, r, op, state, evidence):
    return {"phase": phase, "system_id": r.system_id, "resource_id": r.resource_id, "operation": op,
            "state": state, "evidence_id": evidence}


def compile_steps(verdict):
    if verdict is None:
        raise ValueError("no verdict to compile")
    if verdict.intent == "NOT_DELETION":
        return "NOT_APPLICABLE", []
    resources = verdict.resources
    complete = all(r.compliant for r in resources)
    steps = []
    for r in resources:
        f_op, f_state, m_op, m_state, v_op, v_state = _PLAN[r.treatment]
        if complete:
            steps.append(_step("VERIFICATION", r, v_op, v_state, r.evidence_id))
            continue
        if not r.compliant and f_op:
            steps.append(_step("FINDING", r, f_op, f_state, r.evidence_id))
            steps.append(_step("REMEDIATION", r, m_op, m_state, ""))
        steps.append(_step("VERIFICATION", r, v_op, v_state, ""))
    steps.sort(key=lambda s: (_PHASE_ORDER[s["phase"]], OPERATION_ORDER.index(s["operation"]),
                              s["system_id"], s["resource_id"]))
    numbered = [{"step_no": i, **s} for i, s in enumerate(steps, 1)]
    return ("COMPLETE" if complete else "INCOMPLETE"), numbered
