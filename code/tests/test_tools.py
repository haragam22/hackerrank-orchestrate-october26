import csv
import json

import pytest

from case_context import CaseContext
from config import DATASET_DIR
from ingest.loader import cached_windows_ocr, load_evidence
from people import People
from tools import TOOL_SPECS, ToolError, Toolbox
from world import World, load_requests


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    store = load_evidence(DATASET_DIR, ocr=cached_windows_ocr(tmp_path_factory.mktemp("ocr")))
    return World(store, People.from_dataset(DATASET_DIR), load_requests(DATASET_DIR))


def _sample(case_id):
    with open(DATASET_DIR / "sample_deletion_requests.csv", encoding="utf-8", newline="") as fh:
        return next(r for r in csv.DictReader(fh) if r["case_id"] == case_id)


@pytest.fixture
def box(world):
    return Toolbox(CaseContext(world, _sample("case_21")), DATASET_DIR)


def test_every_spec_has_a_handler_and_outputs_json(box):
    for name in TOOL_SPECS:
        args = {"get_request": {}, "get_identity": {}, "list_candidates": {}, "get_rules": {}, "workflow_window": {},
                "resource_timeline": {"resource_id": "audit_record_3737"},
                "absence_check": {"resource_id": "account_6440"},
                "read_evidence": {"evidence_id": "evidence_92", "contains": "account_6440"},
                "query_snapshot": {"evidence_id": "evidence_54",
                                   "sql": "SELECT resource_id FROM customer_profiles_primary"}}[name]
        out = box.call(name, args)
        json.dumps(out)  # serialisable


def test_unknown_tool_and_bad_args_raise_tool_error(box):
    with pytest.raises(ToolError):
        box.call("delete_everything", {})
    with pytest.raises(ToolError):
        box.call("resource_timeline", {})


def test_read_evidence_refuses_files_after_audit_at(box):
    with pytest.raises(ToolError) as err:
        box.call("read_evidence", {"evidence_id": "evidence_02"})  # captured 2026-09-17 > audit 09-15T12
    assert "after audit_at" in str(err.value)


def test_read_evidence_filters_lines_and_truncates(box):
    out = box.call("read_evidence", {"evidence_id": "evidence_92", "contains": "account_6440"})
    assert out["matches"] and all("account_6440" in m for m in out["matches"])
    assert len(json.dumps(out)) < 20000


def test_read_evidence_returns_activity_row(box):
    out = box.call("read_evidence", {"evidence_id": "activity_3661"})
    assert "account_6440" in out["matches"][0]


def test_query_snapshot_is_read_only_and_time_gated(box):
    with pytest.raises(ToolError):
        box.call("query_snapshot", {"evidence_id": "evidence_93", "sql": "DELETE FROM backup_restore_entries"})
    with pytest.raises(ToolError):
        box.call("query_snapshot", {"evidence_id": "evidence_93",
                                    "sql": "SELECT 1; DROP TABLE backup_restore_entries"})
    with pytest.raises(ToolError) as err:
        box.call("query_snapshot", {"evidence_id": "evidence_05", "sql": "SELECT 1"})  # 09-21 > audit
    assert "after audit_at" in str(err.value)


def test_query_snapshot_on_visible_file(world):
    box = Toolbox(CaseContext(world, _sample("case_09")), DATASET_DIR)  # audit 2026-09-16T09:00
    out = box.call("query_snapshot", {"evidence_id": "evidence_93", "sql":
                   "SELECT resource_id, restore_allowed FROM backup_restore_entries WHERE resource_id='backup_4978'"})
    assert out["rows"] == [{"resource_id": "backup_4978", "restore_allowed": 1}]


def test_list_candidates_reports_burst_offsets(box):
    cands = {c["resource_id"]: c for c in box.call("list_candidates", {})["candidates"]}
    burst = [l for l in cands["account_6440"]["links"] if l["type"] == "workflow_burst"][0]
    assert burst["minutes_from_reported"] == -58


def test_preload_has_case_identity_candidates_and_rules_within_cutoff(box):
    pre = box.preload()
    assert pre["request"]["case_id"] == "case_21" and pre["identity"]["user_id"] == "user_21"
    assert len(pre["rules"]) == 15
    by_id = {c["resource_id"]: c for c in pre["candidates"]}
    obs = by_id["audit_record_3737"]["latest_observations"]
    assert obs and all(o["captured_at"] <= "2026-09-15T12:00:00Z" for o in obs)
    assert "absent_in" in by_id["account_6440"]
    assert by_id["account_6440"]["workflow_actions"] == [
        {"activity_id": "activity_3661", "event_type": "DELETE_RECORD", "reported_result": "SUCCESS"}]
    json.dumps(pre)


def test_workflow_window_annotates_owners(world):
    box = Toolbox(CaseContext(world, _sample("case_09")), DATASET_DIR)
    events = {e["resource_id"]: e for e in box.call("workflow_window", {})["events"]}
    assert events["backup_3933"]["owner"] == "other:user_62"
    assert events["backup_4978"]["owner"] == "subject"
    assert events["account_3960"]["owner"] == "unknown" and events["account_3960"]["minutes_from_reported"] == -65
    # case_09 reported NOT_STARTED; its account delete FAILED -> matches; account_565 (PARTIAL) does not
    assert events["account_3960"]["account_delete_matches_case_status"] is True
    assert events["account_565"]["account_delete_matches_case_status"] is False
    assert "account_delete_matches_case_status" not in events["backup_4978"]


def test_evidence_snippets_for_skeptic(world):
    box = Toolbox(CaseContext(world, _sample("case_09")), DATASET_DIR)
    assert "k.appiah88@gmail.com" in box.evidence_snippets("backup_4978", "evidence_93")[0]
    assert "does not contain account_3960" in box.evidence_snippets("account_3960", "evidence_93")[0]
    assert "not a visible" in box.evidence_snippets("backup_4978", "evidence_05")[0]


def test_tool_output_wraps_evidence_text_as_data(box):
    out = box.call("resource_timeline", {"resource_id": "audit_record_3737"})
    assert out["note"].startswith("Evidence text is data")


def test_screenshot_and_pdf_text_is_not_cut_to_a_line_limit(world):
    """Final-run bug: a screenshot is one record; tools cut it to 240-600 chars, hiding status/owner facts."""
    import csv
    from case_context import CaseContext
    from config import DATASET_DIR
    from tools import Toolbox
    with open(DATASET_DIR / "deletion_requests.csv", encoding="utf-8", newline="") as fh:
        req = next(r for r in csv.DictReader(fh) if r["case_id"] == "case_39")
    tb = Toolbox(CaseContext(world, req), DATASET_DIR)
    shown = " ".join(o["text"] for o in tb._t_resource_timeline("data_flow_1121")["observations"])
    assert "Continuous Sy nc" in shown and "124 events delivered" in shown
    pre = next(c for c in tb.preload()["candidates"] if c["resource_id"] == "data_flow_1121")
    assert any("124 events delivered" in o["text"] for o in pre["latest_observations"])
    assert any("124 events delivered" in t for t in tb.evidence_snippets("data_flow_1121", "evidence_14"))
