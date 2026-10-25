import json
import textwrap

import pytest

from config import DATASET_DIR
from ingest.extract import extract_identifiers, extract_resource_ids, parse_kv
from ingest.loader import EvidenceStore, cached_windows_ocr, load_evidence
from ingest.parsers import parse_json_file, parse_pdf_file, parse_sql_file, parse_text_file


# ---------- low-level extraction ----------

def test_extract_resource_ids_finds_known_shapes_and_skips_user_ids():
    text = 'resource_id="account_3960" linked data_flow_9522, audit_record_7068 and tombstone_440 for user_21'
    assert extract_resource_ids(text) == ["account_3960", "audit_record_7068", "data_flow_9522", "tombstone_440"]


def test_extract_resource_ids_ignores_non_resource_tokens():
    assert extract_resource_ids("index customer_directory_v3 cache account_service_lookup_v2 tr-9028396") == []
    assert extract_resource_ids("activity_3709 evidence_92 audit_01 case_21 user_5") == []


def test_empty_sql_snapshot_is_not_unparsed_and_keeps_scope(tmp_path):
    sql = tmp_path / "empty.sql"
    sql.write_text("CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT);\n"
                   "CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, email TEXT);\n"
                   "INSERT INTO snapshot_scope VALUES ('account_service', '2026-09-05T07:18:01Z');\n",
                   encoding="utf-8")
    records, coverage = parse_sql_file(sql, "e", "2026-09-05T07:18:01Z")
    assert records == [] and coverage == {"customer_profiles": [], "__scope__": ["account_service"]}


def test_extract_identifiers_normalises_email_case_phone_digits_and_hash():
    h = "4f4b9c98a05a2dad20e52313cce7809d18c3da544754dcd46d728b0173174b26"
    ids = extract_identifiers(f"SIPHO.MOKOENA@GMAIL.COM phone +233 24 991 1328 hash {h} ref user_542")
    assert ids["emails"] == ["sipho.mokoena@gmail.com"]
    assert ids["phones"] == ["233249911328"]
    assert ids["hashes"] == [h]
    assert ids["user_ids"] == ["user_542"]


def test_parse_kv_handles_quoted_and_bare_values():
    line = 'level=DEBUG service=account_service event="profile_lookup_started" lookup_key="a@b.com" rows=1'
    assert parse_kv(line) == {"level": "DEBUG", "service": "account_service", "event": "profile_lookup_started",
                              "lookup_key": "a@b.com", "rows": "1"}


# ---------- parsers on small fixtures ----------

def test_parse_sql_rows_and_table_coverage(tmp_path):
    sql = tmp_path / "snap.sql"
    sql.write_text(textwrap.dedent("""
        BEGIN TRANSACTION;
        CREATE TABLE customer_profiles_primary (resource_id TEXT PRIMARY KEY, user_id TEXT, email TEXT, updated_at TEXT);
        CREATE TABLE customer_profiles_failover_replica (resource_id TEXT PRIMARY KEY, user_id TEXT, email TEXT, updated_at TEXT);
        INSERT INTO customer_profiles_failover_replica VALUES ('account_005','user_05','Leila@Yahoo.com','2026-09-02T22:45:00Z');
        COMMIT;
    """), encoding="utf-8")
    records, coverage = parse_sql_file(sql, "evidence_54", "2026-09-02T23:00:00Z")
    assert len(records) == 1
    r = records[0]
    assert r.resource_ids == ["account_005"] and r.emails == ["leila@yahoo.com"] and r.user_ids == ["user_05"]
    assert r.location == "customer_profiles_failover_replica"
    assert r.record_time == "2026-09-02T22:45:00Z"
    assert r.fields["email"] == "Leila@Yahoo.com"
    assert coverage == {"customer_profiles_primary": [], "customer_profiles_failover_replica": ["account_005"]}


def test_parse_json_inherits_context_from_parents(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps({"snapshots": [{"captured_at": "2026-09-15T12:00:00Z", "data_store": "identity_service",
                                               "collection": "deleted_customer_references", "records": [
            {"resource_id": "tombstone_9178", "subject_reference": "user_542", "email": "addo.kofi@gmail.com",
             "name": "Kofi Addo"}]}]}), encoding="utf-8")
    records = parse_json_file(path, "evidence_51", "2026-09-15T12:00:00Z")
    assert len(records) == 1
    r = records[0]
    assert r.resource_ids == ["tombstone_9178"]
    assert r.system_hint == "identity_service"
    assert r.fields["collection"] == "deleted_customer_references"
    assert r.fields["name"] == "Kofi Addo"
    assert r.emails == ["addo.kofi@gmail.com"] and r.user_ids == ["user_542"]


def test_parse_text_lines_kv_json_and_pipe_formats(tmp_path):
    path = tmp_path / "mixed.log"
    path.write_text("\n".join([
        '2026-09-16T08:52:59Z {"service":"security_service","trace_id":"tr-1","resource_id":"audit_record_4902"}',
        '2026-09-26T07:49:50Z level=DEBUG service=account_service trace_id=rb-1 event=profile_lookup_started '
        'resource_id=account_2064 lookup_key=charlotte.wright@outlook.com',
        '- 2026-10-01T12:00:00Z | analytics_platform | analytics_2034 | ANALYTICS_PROFILE | IDENTIFIABLE | x@y.com',
        'header line without anything',
    ]), encoding="utf-8")
    recs = parse_text_file(path, "evidence_x", "2026-10-01T12:00:00Z")
    assert [r.resource_ids for r in recs] == [["audit_record_4902"], ["account_2064"], ["analytics_2034"]]
    assert recs[0].fields["trace_id"] == "tr-1" and recs[0].record_time == "2026-09-16T08:52:59Z"
    assert recs[1].fields["lookup_key"] == "charlotte.wright@outlook.com" and recs[1].emails == ["charlotte.wright@outlook.com"]
    assert recs[2].system_hint == "analytics_platform" and "IDENTIFIABLE" in recs[2].text
    assert recs[1].line_no == 2


def test_parse_text_keeps_identifier_only_lines_for_trace_linking(tmp_path):
    path = tmp_path / "t.log"
    path.write_text("2026-01-01T00:00:00Z trace_id=tr-9 event=lookup lookup_key=a@b.com\n", encoding="utf-8")
    recs = parse_text_file(path, "e", "2026-01-01T00:00:00Z")
    assert len(recs) == 1 and recs[0].resource_ids == [] and recs[0].emails == ["a@b.com"]


# ---------- real dataset ----------

@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return load_evidence(DATASET_DIR, ocr=cached_windows_ocr(tmp_path_factory.mktemp("ocr")))


def test_all_manifest_files_load_with_provenance(store):
    assert isinstance(store, EvidenceStore)
    assert len(store.files) == 94  # 95 manifest lines minus header
    loaded = {r.file_id for r in store.records} | set(store.coverage)
    missing = set(store.files) - loaded - set(store.unparsed)
    assert missing == set()
    for r in store.records:
        assert r.file_id and r.captured_at and r.text


def test_known_facts_present(store):
    backup = [r for r in store.records if r.file_id == "evidence_93" and "backup_4978" in r.resource_ids]
    assert backup and backup[0].emails == ["k.appiah88@gmail.com"] and backup[0].fields["restore_allowed"] == "1"
    acct = [r for r in store.records if "account_3960" in r.resource_ids and r.file_id == "evidence_06"]
    assert acct
    pdf = [r for r in store.records if r.file_id == "evidence_58" and "support_001" in r.resource_ids]
    assert pdf and "priya.raman@gmail.com" in pdf[0].emails
    hand = [r for r in store.records if r.file_id == "evidence_03" and "analytics_2034" in r.resource_ids]
    assert hand and "IDENTIFIABLE" in hand[0].text


def test_snapshot_coverage_supports_absence_proof(store):
    cov = store.coverage["evidence_54"]
    assert "account_005" in cov["customer_profiles_failover_replica"]
    assert "account_004" not in cov["customer_profiles_primary"]
    assert "account_005" not in cov["customer_profiles_primary"]


def test_activity_rows_loaded_as_records(store):
    act = [r for r in store.records if r.file_id == "activity_3709"]
    assert act and act[0].resource_ids == ["backup_4978"] and act[0].fields["event_type"] == "CHECK_RESTORE_ACCESS"
    assert act[0].captured_at == act[0].record_time == "2026-09-07T09:51:10Z"


def test_every_file_parsed_including_screenshots(store):
    assert store.unparsed == {}


def test_screenshot_ids_verified_against_text_evidence(store):
    admin = [r for r in store.records if r.file_id == "evidence_04"][0]
    assert admin.kind == "png"
    assert {"account_5762", "support_9538"} <= set(admin.resource_ids)
    assert "user_11" in admin.user_ids and "haruka.s1838@zoho.com" in admin.emails
    backup = [r for r in store.records if r.file_id == "evidence_32"][0]
    assert "backup_9287" in backup.resource_ids and "areed1433@gmail.com" in backup.emails


def test_load_without_ocr_leaves_only_pngs_unparsed():
    plain = load_evidence(DATASET_DIR)
    assert plain.unparsed and all(plain.files[f].endswith(".png") for f in plain.unparsed)
