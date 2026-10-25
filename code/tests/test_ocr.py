import pytest

from config import DATASET_DIR
from ingest.ocr import normalize_ocr_text, parse_png_file, run_windows_ocr

KNOWN = {"account_5762", "support_9538", "backup_9287", "search_428", "audit_record_003", "data_flow_1121"}


@pytest.mark.parametrize("raw,expected_ids", [
    ("user_ll • account _5762", {"account_5762"}),
    ("support_9S3E created", {"support_9538"}),
    ("backup_g287 restored", {"backup_9287"}),
    ("search_42B email", {"search_428"}),
    ("aud it_record_003", set()),               # too mangled to trust: dropped, not guessed
])
def test_normalize_snaps_confusable_ids_to_known_universe(raw, expected_ids):
    result = normalize_ocr_text(raw, KNOWN)
    assert set(result["resource_ids"]) == expected_ids


def test_normalize_keeps_unknown_but_wellformed_ids_as_unverified():
    result = normalize_ocr_text("backup_9286 restored", KNOWN)
    assert result["resource_ids"] == []
    assert result["unverified_ids"] == ["backup_9286"]


def test_normalize_translates_confusable_letters_deterministically():
    result = normalize_ocr_text("backup_928B", {"backup_9283", "backup_9288"})
    assert result["resource_ids"] == ["backup_9288"]


def test_normalize_fixes_user_ids_and_email_domains():
    result = normalize_ocr_text("user_ll user 17 areed1433@gmaiI.com", set())
    assert result["user_ids"] == ["user_11", "user_17"]
    assert result["emails"] == ["areed1433@gmail.com"]


def test_parse_png_builds_record_with_fake_ocr(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"fake")
    recs = parse_png_file(img, "evidence_04", "2026-09-26T11:00:00Z", KNOWN,
                          ocr=lambda p: "Haruka Sato\nuser_ll • account _5762\nharuka.s1838@zoho.com")
    assert len(recs) == 1
    r = recs[0]
    assert r.kind == "png" and r.resource_ids == ["account_5762"] and r.user_ids == ["user_11"]
    assert r.emails == ["haruka.s1838@zoho.com"]
    assert "OCR" in r.text  # text is marked as OCR output for the agent


def test_parse_png_ocr_failure_returns_no_records(tmp_path):
    def boom(path):
        raise RuntimeError("ocr unavailable")
    assert parse_png_file(tmp_path / "x.png", "e", "t", KNOWN, ocr=boom) == []


def test_windows_ocr_reads_real_screenshot():
    text = run_windows_ocr(DATASET_DIR / "evidence" / "images" / "account_admin_console_20260926.png")
    assert "haruka.s1838@zoho.com" in text
