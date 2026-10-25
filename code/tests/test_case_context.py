import csv
import json

import pytest

from case_context import CaseContext
from config import DATASET_DIR
from ingest.extract import make_record
from ingest.loader import EvidenceStore, cached_windows_ocr, load_evidence
from people import People
from world import World, load_requests


def _rows(name):
    with open(DATASET_DIR / name, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def people():
    return People.from_dataset(DATASET_DIR)


@pytest.fixture(scope="module")
def world(people, tmp_path_factory):
    store = load_evidence(DATASET_DIR, ocr=cached_windows_ocr(tmp_path_factory.mktemp("ocr")))
    return World(store, people, load_requests(DATASET_DIR))


# ---------- people ----------

def test_all_eval_emails_resolve_current_or_historical(people):
    kinds = [people.resolve_email(r["email"]) for r in _rows("deletion_requests.csv")]
    assert all(k is not None for k in kinds)
    current = sum(1 for r in _rows("deletion_requests.csv")
                  if people.identity(people.resolve_email(r["email"])).email == r["email"].lower())
    assert (current, len(kinds) - current) == (418, 100)


def test_identity_includes_history_and_hashes_of_every_email(people):
    import hashlib
    uid = people.resolve_email("arthur-fontaine83@proton.me")  # historical email (audit_197)
    assert uid == "user_225"
    ident = people.identity(uid)
    assert ident.emails == {"arthur-fontaine83@proton.me", "arthur-fontaine@proton.me"}
    for e in ident.emails:
        assert hashlib.sha256(e.encode()).hexdigest() in ident.hashes
    assert people.owner_of("Arthur-Fontaine83@Proton.me") == uid
    assert people.resolve_email("l.fontaine02@yahoo.com") != people.resolve_email("l.fontaine46@yahoo.com")


def test_unknown_email_resolves_to_none(people):
    assert people.resolve_email("nobody@nowhere.example") is None


# ---------- linking rules on a tiny synthetic world ----------

def _tiny_world(records, requests):
    people = People(
        [{"user_id": "user_1", "name": "Ana Lima", "email": "ana@x.com", "phone": "+1 555 0100",
          "email_hash": "", "updated_at": "2026-01-01T00:00:00Z"},
         {"user_id": "user_2", "name": "Ana Limas", "email": "other@x.com", "phone": "+1 555 0200",
          "email_hash": "", "updated_at": "2026-01-01T00:00:00Z"}], [])
    store = EvidenceStore(records=records, files={}, captured={}, coverage={})
    return World(store, people, requests)


REQ = {"case_id": "case_1", "email": "ana@x.com", "requested_at": "2026-09-01T00:00:00Z",
       "reported_status": "COMPLETE", "reported_at": "2026-09-02T12:00:00Z", "audit_at": "2026-09-05T00:00:00Z",
       "request_text": "delete"}


def _rec(fid, cap, text, kind="text", fields=None):
    return make_record(fid, kind, cap, "f", 1, text, fields or {})


def test_name_only_match_is_never_a_candidate():
    w = _tiny_world([_rec("e1", "2026-09-03T00:00:00Z", "support_5 requester Ana Lima")], [REQ])
    ctx = CaseContext(w, REQ)
    assert ctx.candidates() == []


def test_hard_link_and_time_gate():
    recs = [_rec("e1", "2026-09-03T00:00:00Z", "search_7 email=ana@x.com"),
            _rec("e2", "2026-09-09T00:00:00Z", "export_9 email=ana@x.com")]  # after audit_at
    ctx = CaseContext(_tiny_world(recs, [REQ]), REQ)
    cands = {c["resource_id"]: c for c in ctx.candidates()}
    assert set(cands) == {"search_7"}
    assert cands["search_7"]["links"][0]["type"] == "hard" and cands["search_7"]["links"][0]["evidence_id"] == "e1"
    assert ctx.timeline("export_9") == []


def test_trace_link_joins_lines_sharing_trace_id_in_same_file():
    a = _rec("e1", "2026-09-03T00:00:00Z", "trace_id=tr-1 event=lookup lookup_key=ana@x.com",
             fields={"trace_id": "tr-1"})
    b = _rec("e1", "2026-09-03T00:00:00Z", "trace_id=tr-1 event=read resource_id=audit_record_4",
             fields={"trace_id": "tr-1"})
    c = _rec("e9", "2026-09-03T00:00:00Z", "trace_id=tr-1 resource_id=search_8", fields={"trace_id": "tr-1"})
    ctx = CaseContext(_tiny_world([a, b, c], [REQ]), REQ)
    cands = {x["resource_id"]: x for x in ctx.candidates()}
    assert set(cands) == {"audit_record_4"}  # different file with same trace id does not link
    assert cands["audit_record_4"]["links"][0]["type"] == "trace"


def test_workflow_burst_candidates_exclude_resources_owned_by_someone_else():
    burst_mine = _rec("activity_1", "2026-09-02T11:00:00Z", "DELETE_RECORD account_10",
                      kind="activity", fields={"source_system": "deletion_worker", "event_type": "DELETE_RECORD",
                                               "event_time": "2026-09-02T11:00:00Z", "resource_id": "account_10"})
    burst_other = _rec("activity_2", "2026-09-02T11:05:00Z", "DELETE_RECORD account_20",
                       kind="activity", fields={"source_system": "deletion_worker", "event_type": "DELETE_RECORD",
                                                "event_time": "2026-09-02T11:05:00Z", "resource_id": "account_20"})
    far = _rec("activity_3", "2026-09-01T01:00:00Z", "DELETE_RECORD account_30",
               kind="activity", fields={"source_system": "deletion_worker", "event_type": "DELETE_RECORD",
                                        "event_time": "2026-09-01T01:00:00Z", "resource_id": "account_30"})
    owned = _rec("e5", "2026-09-03T00:00:00Z", "account_20 email=other@x.com")
    ctx = CaseContext(_tiny_world([burst_mine, burst_other, far, owned], [REQ]), REQ)
    cands = {x["resource_id"]: x for x in ctx.candidates()}
    assert set(cands) == {"account_10"}
    assert cands["account_10"]["links"][0]["type"] == "workflow_burst"


def test_activity_rows_are_not_hard_links_but_link_resource_identity_is():
    plain = _rec("activity_1", "2026-09-03T00:00:00Z", "UPDATE_RECORD analytics_3 ana@x.com",
                 kind="activity", fields={"event_type": "UPDATE_RECORD", "source_system": "x",
                                          "event_time": "2026-09-03T00:00:00Z"})
    link = _rec("activity_2", "2026-09-03T00:00:00Z", "Data catalog linked export_4 to ana@x.com and user_1",
                kind="activity", fields={"event_type": "LINK_RESOURCE_IDENTITY", "source_system": "data_catalog",
                                         "event_time": "2026-09-03T00:00:00Z"})
    ctx = CaseContext(_tiny_world([plain, link], [REQ]), REQ)
    assert {x["resource_id"] for x in ctx.candidates()} == {"export_4"}


def test_absence_uses_covering_snapshot_containers():
    store = EvidenceStore(records=[], files={"e1": "a.sql", "e2": "b.sql"},
                          captured={"e1": "2026-09-03T00:00:00Z", "e2": "2026-09-10T00:00:00Z"},
                          coverage={"e1": {"customer_profiles": ["account_99"], "support_records": []},
                                    "e2": {"customer_profiles": []}})
    people = People([{"user_id": "user_1", "name": "A", "email": "ana@x.com", "phone": "", "email_hash": "",
                      "updated_at": ""}], [])
    ctx = CaseContext(World(store, people, [REQ]), REQ)
    absent = ctx.absence("account_10")
    assert [a["evidence_id"] for a in absent] == ["e1"]  # e2 is after audit_at
    assert ctx.absence("account_99") == []


def test_absence_ignores_containers_of_other_resource_types():
    # an empty analytics table must not prove an account absent (both names contain "profiles")
    store = EvidenceStore(records=[], files={"e1": "a.sql"}, captured={"e1": "2026-09-03T00:00:00Z"},
                          coverage={"e1": {"customer_analytics_profiles": [], "customer_profiles": []}})
    people = People([{"user_id": "user_1", "name": "A", "email": "ana@x.com", "phone": "", "email_hash": "",
                      "updated_at": ""}], [])
    ctx = CaseContext(World(store, people, [REQ]), REQ)
    assert [a["container"] for a in ctx.absence("account_10")] == ["customer_profiles"]
    assert [a["container"] for a in ctx.absence("analytics_10")] == ["customer_analytics_profiles"]


# ---------- real dataset ----------

def _gold(case_id):
    for row in _rows("sample_output.csv"):
        if row["case_id"] == case_id:
            return {s["resource_id"] for s in json.loads(row["steps_json"])}


def _sample(case_id):
    return next(r for r in _rows("sample_deletion_requests.csv") if r["case_id"] == case_id)


def test_case_21_candidates_and_time_gate(world):
    ctx = CaseContext(world, _sample("case_21"))
    cands = {c["resource_id"] for c in ctx.candidates()}
    assert {"audit_record_3737", "account_6440", "analytics_5335"} <= cands
    assert all(t["captured_at"] <= "2026-09-15T12:00:00Z" for t in ctx.timeline("audit_record_3737"))
    assert "evidence_02" not in {t["evidence_id"] for t in ctx.timeline("audit_record_3737")}


def test_case_01_account_absent_in_failover_snapshot(world):
    ctx = CaseContext(world, _sample("case_01"))
    assert "evidence_54" in {a["evidence_id"] for a in ctx.absence("account_001")}


def test_sample_candidate_recall_is_measured(world):
    total = found = 0
    for req in _rows("sample_deletion_requests.csv"):
        cands = {c["resource_id"] for c in CaseContext(world, req).candidates()}
        gold = _gold(req["case_id"])
        total += len(gold)
        found += len(gold & cands)
    recall = found / total
    print(f"sample candidate recall {found}/{total} = {recall:.2%}")
    assert recall >= 0.85
