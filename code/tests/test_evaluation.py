from evaluation.custom_cases import CUSTOM_CASES, build_request, check_custom
from evaluation.main import HOLDOUT, TUNE, render_evaluation_report, render_usage_report

STEP = {"step_no": 1, "phase": "VERIFICATION", "system_id": "account_service", "resource_id": "account_1",
        "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "evidence_9"}


def test_split_is_disjoint_and_covers_all_samples():
    assert len(HOLDOUT) == 12 and len(TUNE) == 20
    assert not set(HOLDOUT) & set(TUNE)
    assert set(HOLDOUT) | set(TUNE) == {f"case_{i:02d}" for i in range(1, 33)}
    # cases debugged live during development are never in the holdout
    assert not set(HOLDOUT) & {"case_04", "case_09", "case_11", "case_21", "case_28", "case_32"}


def test_custom_cases_are_at_least_three_and_buildable():
    assert len(CUSTOM_CASES) >= 3
    base = {"case_id": "case_09", "email": "a@b.c", "audit_at": "2026-09-16T09:00:00Z", "request_text": "x"}
    req = build_request(CUSTOM_CASES[5], base)
    assert req["case_id"] == "custom_06_time_gate" and req["audit_at"] == "2026-09-16T08:00:00Z"


def test_check_custom_expectations():
    na = CUSTOM_CASES[0]
    assert check_custom(na, None, {"deletion_status": "NOT_APPLICABLE", "steps": []})[0]
    assert not check_custom(na, None, {"deletion_status": "INCOMPLETE", "steps": [STEP]})[0]
    same = CUSTOM_CASES[2]
    base_pred = {"deletion_status": "COMPLETE", "steps": [STEP]}
    assert check_custom(same, ("COMPLETE", [STEP]), {"deletion_status": "COMPLETE", "steps": [STEP]}, base_pred)[0]
    assert not check_custom(same, ("COMPLETE", [STEP]), {"deletion_status": "INCOMPLETE", "steps": [STEP]},
                            base_pred)[0]
    other = dict(STEP, resource_id="account_2")
    ok, detail = check_custom(same, ("COMPLETE", []), {"deletion_status": "COMPLETE", "steps": [other]}, base_pred)
    assert not ok and "account_2" in detail
    gate = CUSTOM_CASES[5]
    leaked = dict(STEP, evidence_id="evidence_93")
    ok, detail = check_custom(gate, None, {"deletion_status": "INCOMPLETE", "steps": [leaked]})
    assert not ok and "evidence_93" in detail


def test_reports_render_measured_numbers():
    metrics = {"cases": 2, "status_accuracy": 0.5, "sequence_accuracy": 0.0, "step_precision": 0.75,
               "step_recall": 0.6, "step_f1": 0.666, "field_accuracy": {"evidence_id": 0.5}, "per_case": {
                   "case_01": {"status_ok": True, "gold_status": "COMPLETE", "pred_status": "COMPLETE",
                               "matched_steps": 1, "gold_steps": 1, "pred_steps": 1, "sequence_ok": True}}}
    md = render_evaluation_report({"all": metrics, "tune": metrics, "holdout": metrics},
                                  [("custom_01", "purpose", True, "ok")], run_info={"run": "eval"}, notes="NOTES")
    assert "50.0%" in md and "custom_01" in md and "NOTES" in md
    rows = [{"case_id": "c1", "model": "gpt-6.1-sol", "backend": "agentrouter", "input_tokens": 1000,
             "output_tokens": 100, "cost_usd": 0.0015, "cached": False, "fallback_used": False}]
    usage = render_usage_report(rows, title="Run", n_requests=1)
    assert "gpt-6.1-sol" in usage and "1,000" in usage and "$0.0015" in usage
