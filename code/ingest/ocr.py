"""Screenshot evidence: Windows built-in OCR, then translation of OCR letter/digit confusions inside id
suffixes. An id is verified only if it exactly matches an id already seen in text evidence; otherwise it is
kept as unverified. Ids are never guessed by similarity."""
import re
import subprocess
from pathlib import Path

from ingest.extract import make_record

_SCRIPT = Path(__file__).with_name("winocr.ps1")
# Characters OCR confuses with digits inside an id suffix.
_DIGIT_CONFUSIONS = str.maketrans({"S": "5", "s": "5", "B": "8", "E": "8", "g": "9", "q": "9", "I": "1", "l": "1",
                                   "|": "1", "i": "1", "O": "0", "o": "0", "D": "0", "Z": "2", "z": "2"})
_PREFIXES = ("account", "search", "support", "analytics", "export", "backup", "data_flow", "audit_record",
             "tombstone", "marketing", "object", "cache", "email_delivery", "user")
_ID_CANDIDATE = re.compile(r"\b(" + "|".join(_PREFIXES) + r")\s?_?\s?([0-9A-Za-z|]{1,6})\b")
_EMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def run_windows_ocr(path, timeout_s=120):
    proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(_SCRIPT),
                           str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout_s)
    if proc.returncode != 0:
        raise RuntimeError(f"windows OCR failed: {proc.stderr[:300]}")
    return proc.stdout


def normalize_ocr_text(text, known_ids):
    resource_ids, unverified, user_ids = set(), set(), set()
    for prefix, suffix in _ID_CANDIDATE.findall(text or ""):
        fixed = suffix.translate(_DIGIT_CONFUSIONS)
        if not fixed.isdigit() or not any(c.isdigit() for c in suffix):
            if prefix == "user" and fixed.isdigit():
                user_ids.add(f"user_{fixed}")
            continue
        candidate = f"{prefix}_{fixed}"
        if prefix == "user":
            user_ids.add(candidate)
            continue
        # Only confusable letters were translated; digits are trusted. No edit-distance guessing:
        # a nearby known id can be a different real record (backup_9286 vs backup_9287).
        (resource_ids if candidate in known_ids else unverified).add(candidate)
    emails = {f"{local}@{domain.replace('I', 'l')}".lower() for local, domain in _EMAIL.findall(text or "")}
    return {"resource_ids": sorted(resource_ids), "unverified_ids": sorted(unverified),
            "user_ids": sorted(user_ids), "emails": sorted(emails)}


def parse_png_file(path, file_id, captured_at, known_ids, ocr=run_windows_ocr):
    try:
        raw = ocr(path)
    except Exception:  # OCR is best effort; the loader reports the file as unparsed
        return []
    norm = normalize_ocr_text(raw, known_ids)
    text = (f"[OCR text of screenshot {Path(path).name}; ids normalised: {', '.join(norm['resource_ids'])}; "
            f"unverified ids: {', '.join(norm['unverified_ids']) or 'none'}]\n" + raw)
    rec = make_record(file_id, "png", captured_at, Path(path).name, 1, text,
                      {"ocr_unverified_ids": ",".join(norm["unverified_ids"])})
    # Identifiers come from the normalised OCR, not from the raw (confusable) characters.
    rec.resource_ids, rec.user_ids = norm["resource_ids"], norm["user_ids"]
    rec.emails = sorted(set(rec.emails) | set(norm["emails"]))
    rec.emails = [e for e in rec.emails if e in norm["emails"]]
    return [rec]
