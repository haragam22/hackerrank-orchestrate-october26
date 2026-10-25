from evaluation.metrics import score

S1 = {"step_no": 1, "phase": "FINDING", "system_id": "search_service", "resource_id": "search_1",
      "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "evidence_9"}
S2 = {"step_no": 2, "phase": "REMEDIATION", "system_id": "search_service", "resource_id": "search_1",
      "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": ""}
S3 = {"step_no": 3, "phase": "VERIFICATION", "system_id": "search_service", "resource_id": "search_1",
      "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": ""}


def test_perfect_prediction_scores_one_everywhere():
    gold = {"c1": ("INCOMPLETE", [S1, S2, S3]), "c2": ("NOT_APPLICABLE", [])}
    m = score(gold, gold)
    assert m["cases"] == 2
    assert m["status_accuracy"] == 1.0 and m["sequence_accuracy"] == 1.0
    assert m["step_precision"] == m["step_recall"] == m["step_f1"] == 1.0
    assert all(v == 1.0 for v in m["field_accuracy"].values())


def test_wrong_evidence_hurts_step_match_and_field_accuracy_only_for_that_field():
    pred_s1 = dict(S1, evidence_id="evidence_1")
    m = score({"c1": ("INCOMPLETE", [S1, S2, S3])}, {"c1": ("INCOMPLETE", [pred_s1, S2, S3])})
    assert m["status_accuracy"] == 1.0
    assert m["step_recall"] == 2 / 3 and m["sequence_accuracy"] == 0.0
    assert m["field_accuracy"]["evidence_id"] == 2 / 3 and m["field_accuracy"]["operation"] == 1.0


def test_missing_case_counts_as_wrong_and_extra_steps_lower_precision():
    gold = {"c1": ("INCOMPLETE", [S1, S2, S3]), "c2": ("COMPLETE", [S3])}
    pred = {"c1": ("INCOMPLETE", [S1, S2, S3, dict(S3, resource_id="search_2", step_no=4)])}
    m = score(gold, pred)
    assert m["status_accuracy"] == 0.5
    assert m["step_precision"] == 3 / 4 and m["step_recall"] == 3 / 4
    assert m["per_case"]["c2"]["status_ok"] is False


def test_order_matters_for_sequence_not_for_step_match():
    m = score({"c1": ("INCOMPLETE", [S1, S2, S3])}, {"c1": ("INCOMPLETE", [S2, S1, S3])})
    assert m["step_f1"] == 1.0 and m["sequence_accuracy"] == 0.0
