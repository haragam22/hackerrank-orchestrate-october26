"""Validate an output.csv against the submission contract.

Usage: python code/check_output.py [path/to/output.csv]   (default: repo-root output.csv)
Exit code 0 when every check passes.
"""
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATASET_DIR, REPO_ROOT  # noqa: E402

HEADER = ["case_id", "deletion_status", "steps_json"]
_SYSTEM_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_KNOWN = None


def _known_evidence_ids():
    """file_ids, activity_ids and audit_ids that exist in the dataset (cached)."""
    global _KNOWN
    if _KNOWN is None:
        ids = set()
        for name, col in (("evidence_files.csv", "file_id"), ("system_activity.csv", "activity_id"),
                          ("user_audit_log.csv", "audit_id")):
            with open(DATASET_DIR / name, encoding="utf-8", newline="") as fh:
                ids |= {r[col] for r in csv.DictReader(fh)}
        _KNOWN = ids
    return _KNOWN
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
STEP_KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
PHASE_OPS = {
    "FINDING": {"REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
                "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE"},
    "REMEDIATION": {"DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
                    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED"},
    "VERIFICATION": {"VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
                     "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
                     "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED"},
}
PHASE_RANK = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}


def _num(case_id):
    m = re.search(r"(\d+)$", case_id)
    return int(m.group(1)) if m else -1


def _shape_problem(steps):
    """Why steps is not a list of flat step objects, or None (malformed input is reported, never a crash)."""
    if not isinstance(steps, list):
        return "steps_json is not a JSON array"
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            return f"step {i} is not an object"
        if set(s) != STEP_KEYS:
            return f"step {i} keys {sorted(map(str, s))}"
        if not isinstance(s["step_no"], int) or isinstance(s["step_no"], bool):
            return f"step {i} step_no is not an integer"
        if any(not isinstance(s[k], str) for k in STEP_KEYS - {"step_no"}):
            return f"step {i} has a non-text field"
    return None


def _row_problems(case_id, status, steps):
    shape = _shape_problem(steps)
    if shape:
        return [f"{case_id}: {shape}"]
    out = []
    if status == "NOT_APPLICABLE" and steps:
        out.append("NOT_APPLICABLE must have []")
    if status in ("COMPLETE", "INCOMPLETE") and not steps:
        out.append(f"{status} with no steps (placeholder or empty result)")
    if [s.get("step_no") for s in steps] != list(range(1, len(steps) + 1)):
        out.append("step_no not consecutive from 1")
    ranks = []
    for s in steps:
        if set(s) != STEP_KEYS:
            out.append(f"step keys {sorted(s)}")
            continue
        ops = PHASE_OPS.get(s["phase"])
        if ops is None or s["operation"] not in ops:
            out.append(f"bad phase/operation {s['phase']}/{s['operation']}")
            continue
        if ops[s["operation"]] != s["state"]:
            out.append(f"state {s['state']} wrong for {s['operation']}")
        ranks.append(PHASE_RANK[s["phase"]])
        if status == "COMPLETE" and (s["phase"] != "VERIFICATION" or not s["evidence_id"]):
            out.append("COMPLETE allows only evidence-backed VERIFICATION steps")
        if status == "INCOMPLETE" and s["phase"] != "FINDING" and s["evidence_id"]:
            out.append("remediation/verification of INCOMPLETE must have empty evidence_id")
        if s["phase"] == "FINDING" and not s["evidence_id"]:
            out.append("finding without evidence_id")
    # Content checks (security pass L2): known evidence ids, plain system ids, INCOMPLETE needs a finding.
    known = _known_evidence_ids()
    for s in steps:
        if not isinstance(s, dict):
            continue
        if s.get("evidence_id") and s["evidence_id"] not in known:
            out.append(f"evidence_id {s['evidence_id']!r} is not a file_id/activity_id/audit_id in the dataset")
        if not _SYSTEM_ID.match(str(s.get("system_id", ""))):
            out.append(f"system_id {str(s.get('system_id'))[:40]!r} is not a plain system name")
    if status == "INCOMPLETE" and steps and not any(isinstance(s, dict) and s.get("phase") == "FINDING"
                                                     for s in steps):
        out.append("INCOMPLETE without any FINDING step")
    if ranks != sorted(ranks):
        out.append("phases out of order")
    return [f"{case_id}: {p}" for p in out]


def check(path, requests_path=DATASET_DIR / "deletion_requests.csv"):
    """Returns (problems list, summary dict). Unreadable input is reported as a problem, never a crash."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
    except (UnicodeDecodeError, csv.Error) as exc:
        return [f"output file cannot be read as UTF-8 CSV: {type(exc).__name__}"], {"rows": 0}
    with open(requests_path, encoding="utf-8", newline="") as fh:
        expected = [r["case_id"] for r in csv.DictReader(fh)]
    problems = []
    if not rows or rows[0] != HEADER:
        problems.append(f"header must be {HEADER}")
    data = rows[1:]
    ids = [r[0] for r in data if r]
    if len(data) != len(expected):
        problems.append(f"{len(data)} data rows, expected {len(expected)}")
    if sorted(ids) != sorted(expected) or len(set(ids)) != len(ids):
        problems.append("case_ids do not match deletion_requests.csv one-to-one")
    if [_num(i) for i in ids] != sorted(_num(i) for i in ids):
        problems.append("rows not sorted by case_id")
    counts = {s: 0 for s in STATUSES}
    for r in data:
        if len(r) != 3:
            problems.append(f"{r[:1]}: expected 3 columns")
            continue
        if r[1] not in STATUSES:
            problems.append(f"{r[0]}: bad status {r[1]}")
            continue
        counts[r[1]] += 1
        try:
            steps = json.loads(r[2])
        except (ValueError, RecursionError):  # bad JSON, absurd int literals, pathological nesting
            problems.append(f"{r[0]}: steps_json is not valid JSON")
            continue
        if not isinstance(steps, list):
            problems.append(f"{r[0]}: steps_json must be a list")
            continue
        problems.extend(_row_problems(r[0], r[1], steps))
    return problems, {"rows": len(data), "status_counts": counts}


def main(argv=None):
    path = Path((argv or sys.argv[1:] or [str(REPO_ROOT / "output.csv")])[0])
    problems, summary = check(path)
    print(f"{path}: {summary['rows']} rows, statuses {summary['status_counts']}")
    for p in problems[:50]:
        print("FAIL", p)
    print("ALL CHECKS PASSED" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
