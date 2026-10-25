"""Safe SQLite handling for evidence snapshots (security review F1, F2).

Evidence .sql files and model-written queries run in an in-memory database that cannot attach or create files,
and model queries are limited in time, value size and rows.
"""
import re
import sqlite3
import time
from pathlib import Path

_MAX_VALUE_BYTES = 1_000_000
_VALUE_CHARS = 500


class SqlRejected(Exception):
    pass


def _deny_files(action, arg1, arg2, dbname, source):
    # ATTACH/DETACH create or open database files; VACUUM INTO attaches internally.
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _new_connection():
    conn = sqlite3.connect(":memory:")
    conn.set_authorizer(_deny_files)
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_VALUE_BYTES)
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    return conn


def open_snapshot(path):
    """Execute an evidence SQL file into a fresh restricted in-memory database."""
    conn = _new_connection()
    try:
        conn.executescript(Path(path).read_text(encoding="utf-8"))
    except Exception:
        conn.close()
        raise
    return conn


_SELECT = re.compile(r"(?is)^\s*(select|with)\b")
_WRITE = re.compile(r"(?i)\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex)\b")


def run_select(conn, sql, timeout_s=3.0, max_rows=20):
    """Run one read-only SELECT with a deadline; values longer than 500 chars are cut."""
    stmt = (sql or "").strip().rstrip(";")
    if not isinstance(sql, str) or not _SELECT.match(stmt) or ";" in stmt or _WRITE.search(stmt):
        raise SqlRejected("only a single read-only SELECT statement is allowed")
    deadline = time.monotonic() + timeout_s
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    conn.execute("PRAGMA query_only = ON")
    try:
        cur = conn.execute(stmt)
        cols = [c[0] for c in cur.description or []]
        raw = cur.fetchmany(max_rows)
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc):
            raise SqlRejected(f"query exceeded the {timeout_s:.0f}s time limit")
        raise SqlRejected(f"sql error: {exc}")
    except sqlite3.DataError as exc:
        raise SqlRejected(f"value too large: {exc}")
    except sqlite3.Error as exc:
        raise SqlRejected(f"sql error: {exc}")
    finally:
        conn.set_progress_handler(None, 0)

    def cut(v):
        if isinstance(v, (bytes, bytearray)):
            return f"<{len(v)} bytes>"
        if isinstance(v, str) and len(v) > _VALUE_CHARS:
            return v[:_VALUE_CHARS] + "...[cut]"
        return v

    return [{c: cut(v) for c, v in zip(cols, row)} for row in raw]
