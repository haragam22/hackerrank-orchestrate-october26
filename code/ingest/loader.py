"""Load all participant-facing evidence into one EvidenceStore."""
import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ingest.extract import make_record
from ingest.ocr import parse_png_file, run_windows_ocr
from ingest.parsers import parse_json_file, parse_pdf_file, parse_sql_file, parse_text_file


@dataclass
class EvidenceStore:
    files: dict = field(default_factory=dict)        # file_id -> relative path
    captured: dict = field(default_factory=dict)     # file_id -> captured_at
    records: list = field(default_factory=list)
    coverage: dict = field(default_factory=dict)     # SQL file_id -> {table: [resource_ids], "__scope__": [...]}
    unparsed: dict = field(default_factory=dict)     # file_id -> reason (e.g. PNG awaiting OCR)


def _read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def cached_windows_ocr(cache_dir):
    """Wrap run_windows_ocr with a disk cache keyed by image content hash."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    def ocr(path):
        key = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        hit = cache_dir / f"{key}.txt"
        if hit.exists():
            return hit.read_text(encoding="utf-8")
        text = run_windows_ocr(path)
        hit.write_text(text, encoding="utf-8")
        return text

    return ocr


def load_evidence(dataset_dir, ocr=None):
    """ocr: callable(path) -> text for screenshots; None leaves PNGs unparsed."""
    dataset_dir = Path(dataset_dir)
    store = EvidenceStore()
    images = []
    for row in _read_csv(dataset_dir / "evidence_files.csv"):
        fid, rel, cap = row["file_id"], row["file_path"], row["captured_at"]
        store.files[fid], store.captured[fid] = rel, cap
        path = dataset_dir / rel
        suffix = path.suffix.lower()
        if not path.resolve().is_relative_to(dataset_dir.resolve()):  # security pass M1
            store.unparsed[fid] = "path outside the dataset folder; not read"
            continue
        if suffix == ".png" and ocr is not None:
            images.append((path, fid, cap))  # parsed last, once the known-id universe exists
            continue
        try:  # one malformed file must not stop the whole run (security review F6)
            if suffix == ".sql":
                recs, cov = parse_sql_file(path, fid, cap)
                store.coverage[fid] = cov
            elif suffix == ".json":
                recs = parse_json_file(path, fid, cap)
            elif suffix in (".log", ".txt"):
                recs = parse_text_file(path, fid, cap)
            elif suffix == ".pdf":
                recs = parse_pdf_file(path, fid, cap)
            else:
                store.unparsed[fid] = f"no parser for {suffix}"
                continue
        except (Exception, RecursionError) as exc:
            store.unparsed[fid] = f"parse failed: {type(exc).__name__}: {str(exc)[:200]}"
            continue
        # An empty SQL snapshot is still evidence: its coverage proves absence.
        if not recs and suffix != ".sql":
            store.unparsed[fid] = "no records extracted"
        store.records.extend(recs)

    for row in _read_csv(dataset_dir / "system_activity.csv"):
        text = ",".join(f"{k}={v}" for k, v in row.items())
        store.records.append(make_record(row["activity_id"], "activity", row["event_time"], "system_activity.csv",
                                         0, text, row, system_hint=row["target_system"],
                                         record_time=row["event_time"]))

    # Identity-change rows, so audit_* ids can be read and cited by id (security review F14).
    for row in _read_csv(dataset_dir / "user_audit_log.csv"):
        text = ",".join(f"{k}={v}" for k, v in row.items())
        store.records.append(make_record(row["audit_id"], "audit", row["event_time"], "user_audit_log.csv", 0,
                                         text, row, system_hint="identity_service", record_time=row["event_time"]))

    known_ids = {rid for r in store.records for rid in r.resource_ids}
    for path, fid, cap in images:
        recs = parse_png_file(path, fid, cap, known_ids, ocr=ocr)
        if not recs:
            store.unparsed[fid] = "OCR failed"
        store.records.extend(recs)
    return store
