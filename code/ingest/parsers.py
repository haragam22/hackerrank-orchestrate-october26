"""Per-format parsers. Each returns Records carrying file_id + captured_at provenance."""
import json
import re
from pathlib import Path

from ingest.extract import make_record, parse_kv
from sqlsafe import open_snapshot

_PIPE_TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


def parse_sql_file(path, file_id, captured_at):
    """Execute the snapshot into restricted in-memory SQLite (no ATTACH / file creation).
    Returns (records, coverage{table: [resource_ids]})."""
    conn = open_snapshot(path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        scope = {}
        if "snapshot_scope" in tables:
            scope = dict(conn.execute("SELECT origin_system, captured_at FROM snapshot_scope").fetchall())
        records, coverage = [], {}
        for table in tables:
            if table == "snapshot_scope":
                continue
            cur = conn.execute(f'SELECT * FROM "{table}"')
            cols = [c[0] for c in cur.description]
            coverage[table] = []
            for i, row in enumerate(cur.fetchall(), 1):
                fields = dict(zip(cols, row))
                text = f"{table}: " + json.dumps(fields, ensure_ascii=False)
                rec = make_record(file_id, "sql", captured_at, table, i, text, fields)
                if "resource_id" in fields and fields["resource_id"]:
                    coverage[table].append(fields["resource_id"])
                records.append(rec)
        if scope:
            coverage["__scope__"] = sorted(scope)
        return records, coverage
    finally:
        conn.close()


def parse_json_file(path, file_id, captured_at):
    """Every dict that has a resource_id becomes a Record; scalar context from ancestors is inherited."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records = []

    def walk(node, context, trail):
        if isinstance(node, dict):
            scalars = {k: v for k, v in node.items() if not isinstance(v, (dict, list))}
            if "resource_id" in node:
                fields = {**context, **{k: v for k, v in node.items()}}
                fields = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                          for k, v in fields.items()}
                hint = context.get("data_store") or (trail[-2] if len(trail) >= 2 else "")
                text = f"{'/'.join(trail)}: " + json.dumps(node, ensure_ascii=False)
                records.append(make_record(file_id, "json", captured_at, "/".join(trail), len(records) + 1,
                                           text, fields, system_hint=hint))
            child_ctx = {**context, **scalars}
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, child_ctx, trail + [k])
        elif isinstance(node, list):
            for item in node:
                walk(item, context, trail)

    walk(data, {}, [])
    return records


def _line_fields(line):
    brace = line.find("{")
    if brace != -1:
        try:
            obj = json.loads(line[brace:])
            if isinstance(obj, dict):
                return obj, ""
        except json.JSONDecodeError:
            pass
    fields = parse_kv(line)
    hint = ""
    if " | " in line:
        parts = [p.strip(" -") for p in line.split("|")]
        if parts and _PIPE_TS.fullmatch(parts[0] or ""):
            fields.setdefault("time", parts[0])
            if len(parts) > 1:
                hint = parts[1]
    if "service" not in fields:
        m = re.match(r"^\S+\s+\[[A-Z]+\]\s+\[([a-z_]+)\]\s+\[([^\]]+)\]", line)
        if m:
            hint = m.group(1)
            fields.setdefault("trace_id", m.group(2))
        m = re.match(r"^\S+\s+<\d+>\d+\s+\S+\s+([a-z_]+)\s+(tr-\S+)", line)
        if m:
            hint = m.group(1)
            fields.setdefault("trace_id", m.group(2))
        m = re.match(r"^\S+\s+\|\s+[A-Z]+\s+\|\s+([a-z_]+)\s+\|\s+(\S+)\s+\|", line)
        if m:
            hint = m.group(1)
            fields.setdefault("trace_id", m.group(2))
    if "requestId" in fields and "trace_id" not in fields:
        fields["trace_id"] = fields["requestId"]
    return fields, hint


def parse_text_file(path, file_id, captured_at):
    """Logs / plain text: one Record per line that mentions a resource id or an identifier."""
    records = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = line.rstrip()
        if not line.strip():
            continue
        fields, hint = _line_fields(line)
        rec = make_record(file_id, "text", captured_at, Path(path).name, i, line, fields, system_hint=hint)
        if rec.resource_ids or rec.emails or rec.user_ids or rec.hashes or rec.phones:
            records.append(rec)
    return records


def parse_pdf_file(path, file_id, captured_at):
    """PDF text layer; whole document is one Record (facts on a page belong together)."""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    return [make_record(file_id, "pdf", captured_at, Path(path).name, 1, text)] if text.strip() else []
