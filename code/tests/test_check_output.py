import csv
import json

from check_output import check
from config import DATASET_DIR


def _write(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        w.writerows(rows)


def _reqs(tmp_path, ids):
    p = tmp_path / "req.csv"
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["case_id"])
        w.writerows([[i] for i in ids])
    return p


def test_sample_gold_passes_every_check():
    problems, summary = check(DATASET_DIR / "sample_output.csv", DATASET_DIR / "sample_deletion_requests.csv")
    assert problems == [] and summary["rows"] == 32


def test_detects_order_count_status_and_step_errors(tmp_path):
    good = {"step_no": 1, "phase": "VERIFICATION", "system_id": "s", "resource_id": "r",
            "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "evidence_1"}
    bad_state = dict(good, state="PRESENT")
    out = tmp_path / "o.csv"
    _write(out, [["case_10", "COMPLETE", json.dumps([bad_state])],
                 ["case_2", "NOT_APPLICABLE", json.dumps([good])],
                 ["case_3", "MAYBE", "[]"]])
    problems, _ = check(out, _reqs(tmp_path, ["case_2", "case_3", "case_10", "case_11"]))
    text = " | ".join(problems)
    for needle in ("3 data rows", "do not match", "not sorted", "state PRESENT wrong", "must have []",
                   "bad status MAYBE"):
        assert needle in text


def test_empty_complete_or_incomplete_rows_are_rejected(tmp_path):
    out = tmp_path / "o.csv"
    _write(out, [["case_1", "COMPLETE", "[]"], ["case_2", "INCOMPLETE", "[]"], ["case_3", "NOT_APPLICABLE", "[]"]])
    text = " | ".join(check(out, _reqs(tmp_path, ["case_1", "case_2", "case_3"]))[0])
    assert "case_1: COMPLETE with no steps" in text and "case_2: INCOMPLETE with no steps" in text
    assert "case_3" not in text


def test_incomplete_rules(tmp_path):
    finding = {"step_no": 2, "phase": "FINDING", "system_id": "s", "resource_id": "r",
               "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": ""}
    verify = {"step_no": 1, "phase": "VERIFICATION", "system_id": "s", "resource_id": "r",
              "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "evidence_1"}
    out = tmp_path / "o.csv"
    _write(out, [["case_1", "INCOMPLETE", json.dumps([verify, finding])]])
    text = " | ".join(check(out, _reqs(tmp_path, ["case_1"]))[0])
    assert "phases out of order" in text
    assert "finding without evidence_id" in text and "must have empty evidence_id" in text
    _write(out, [["case_1", "COMPLETE", json.dumps([dict(verify, step_no=3)])]])
    assert "step_no not consecutive" in " | ".join(check(out, _reqs(tmp_path, ["case_1"]))[0])
