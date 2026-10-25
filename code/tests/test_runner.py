import csv
import json

from runner import ResultStore, assemble_output, case_sort_key


def test_case_sort_key_is_numeric():
    ids = ["case_100", "case_33", "case_550", "case_99"]
    assert sorted(ids, key=case_sort_key) == ["case_33", "case_99", "case_100", "case_550"]


def test_result_store_round_trip_and_resume_logic(tmp_path):
    store = ResultStore(tmp_path)
    store.save({"case_id": "case_1", "deletion_status": "COMPLETE", "steps": [], "ok": True, "flags": []})
    store.save({"case_id": "case_2", "deletion_status": "INCOMPLETE", "steps": [], "ok": False, "flags": ["x"]})
    assert store.load("case_1")["deletion_status"] == "COMPLETE"
    assert store.done_ids() == {"case_1"}          # failed cases are re-run on resume
    assert store.load("case_404") is None


def test_assemble_output_writes_every_case_sorted_with_placeholders(tmp_path):
    store = ResultStore(tmp_path / "cases")
    step = {"step_no": 1, "phase": "VERIFICATION", "system_id": "account_service", "resource_id": "account_1",
            "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "evidence_9"}
    store.save({"case_id": "case_100", "deletion_status": "COMPLETE", "steps": [step], "ok": True, "flags": []})
    store.save({"case_id": "case_34", "deletion_status": "NOT_APPLICABLE", "steps": [], "ok": True, "flags": []})
    out = tmp_path / "output.csv"
    summary = assemble_output(["case_100", "case_34", "case_35"], store, out)
    with open(out, encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["case_id", "deletion_status", "steps_json"]
    assert [r[0] for r in rows[1:]] == ["case_34", "case_35", "case_100"]
    assert rows[1][1:] == ["NOT_APPLICABLE", "[]"]
    assert rows[2][1] == "INCOMPLETE" and rows[2][2] == "[]"   # missing case -> placeholder
    assert json.loads(rows[3][2]) == [step]
    assert summary == {"rows": 3, "missing": ["case_35"], "failed": []}
