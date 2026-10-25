"""Format-agnostic extraction of resource ids, identifiers, key=value fields and timestamps."""
import re
from dataclasses import dataclass, field

# Resource ids look like <prefix>_<digits> (account_3960, data_flow_9522, audit_record_7068).
# user_<n> is an identifier; activity_/evidence_/audit_<n>/case_ are evidence or case ids, not resources.
_RESOURCE = re.compile(r"\b([a-z]+(?:_[a-z]+)*_\d+)\b")
_NOT_RESOURCE_PREFIXES = {"user", "activity", "evidence", "audit", "case"}
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+\d[\d\s().-]{6,}\d")
_HASH = re.compile(r"\b[0-9a-f]{64}\b")
_USER_ID = re.compile(r"\buser_\d+\b")
_KV = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("([^"]*)"|\S+)')
_LEADING_TS = re.compile(r"^\W*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)")

# Record-level time fields, in order of preference.
TIME_FIELDS = ("observed_at", "updated_at", "event_time", "captured_at", "recorded_at")
MAX_SCAN_CHARS = 8000  # longest text unit in the dataset is 1,001 chars


def _uniq_sorted(values):
    return sorted(set(values))


def extract_resource_ids(text):
    return _uniq_sorted(m for m in _RESOURCE.findall(text or "") if m.rsplit("_", 1)[0] not in _NOT_RESOURCE_PREFIXES)


def digits(value):
    return re.sub(r"\D", "", value or "")


def extract_identifiers(text):
    text = text or ""
    return {
        "emails": _uniq_sorted(e.lower() for e in _EMAIL.findall(text)),
        "phones": _uniq_sorted(digits(p) for p in _PHONE.findall(text)),
        "hashes": _uniq_sorted(_HASH.findall(text)),
        "user_ids": _uniq_sorted(_USER_ID.findall(text)),
    }


def parse_kv(text):
    return {k: (quoted if quoted is not None and raw.startswith('"') else raw)
            for k, raw, quoted in _KV.findall(text or "")}


def leading_timestamp(text):
    m = _LEADING_TS.match(text or "")
    return m.group(1) if m else ""


@dataclass
class Record:
    """One observable unit of evidence (SQL row, JSON object, text line, activity row)."""
    file_id: str            # file_id, activity_id or audit_id used as evidence_id
    kind: str               # sql | json | text | pdf | activity
    captured_at: str        # when the evidence item was captured (time gate uses this)
    record_time: str        # time stated by the record itself, if any
    location: str           # table / json path / file name
    line_no: int
    text: str               # raw snippet for the agent and skeptic
    fields: dict = field(default_factory=dict)
    system_hint: str = ""
    resource_ids: list = field(default_factory=list)
    emails: list = field(default_factory=list)
    phones: list = field(default_factory=list)
    hashes: list = field(default_factory=list)
    user_ids: list = field(default_factory=list)


def make_record(file_id, kind, captured_at, location, line_no, text, fields=None, system_hint="",
                record_time=""):
    fields = {k: ("" if v is None else str(v)) for k, v in (fields or {}).items()}
    # Patterns run on a bounded prefix: avoids quadratic regex time on pathological lines (security review F10).
    text = text[:MAX_SCAN_CHARS]
    ids = extract_identifiers(text)
    if not record_time:
        record_time = next((fields[f] for f in TIME_FIELDS if fields.get(f)), "") or leading_timestamp(text)
    if not system_hint:
        system_hint = (fields.get("system") or fields.get("data_store") or fields.get("service")
                       or fields.get("logger") or fields.get("target_system") or "")
    return Record(file_id=file_id, kind=kind, captured_at=captured_at, record_time=record_time, location=location,
                  line_no=line_no, text=text[:1500], fields=fields, system_hint=system_hint,
                  resource_ids=extract_resource_ids(text), **ids)
