"""Checks every claim in an agent Verdict against the time-gated evidence.

validate() returns problems as plain sentences that are sent back to the agent. finalize() is the last resort
after the agent's revision rounds; every change it makes is flagged:
- citation / system_id problems are repaired from the evidence;
- a "compliant" claim that the latest evidence contradicts is overridden to non-compliant (SAFETY OVERRIDE):
  the audit must never report data as gone while a captured file still shows it (security review F3);
- resources with other problems (not linked, wrong category/rule, duplicates) are dropped.
It never invents resources.
"""
import csv
import re

from agent.schema import ResourceVerdict
from case_context import _PREFIX_SYSTEM, prefix_of

TREATMENT_BY_CATEGORY = {
    "USER_PROFILE": "DELETE", "SECONDARY_IDENTIFIER": "DELETE", "SEARCH_INDEX_ENTRY": "DELETE",
    "CACHE_ENTRY": "DELETE", "SUPPORT_ATTACHMENT": "DELETE", "USER_EXPORT": "DELETE", "MARKETING_PROFILE": "DELETE",
    "SUPPORT_MESSAGE": "REDACT", "EMAIL_DELIVERY_LOG": "REDACT", "ACCOUNT_TOMBSTONE": "REDACT",
    "ANALYTICS_PROFILE": "ANONYMIZE", "PRODUCT_TELEMETRY": "ANONYMIZE",
    "SECURITY_AUDIT_LOG": "RETAIN", "LEGAL_HOLD_RECORD": "RETAIN",
    "BACKUP_ARCHIVE": "BLOCK_RESTORE", "DATA_FLOW": "DISABLE_FLOW",
}
# Which policy categories a resource type can have (first = default treatment for safety checks).
# A record's category must fit its type: an account can never be a RETAIN security log (security pass H2).
ALLOWED_CATEGORIES = {
    "account": ("USER_PROFILE",), "tombstone": ("ACCOUNT_TOMBSTONE",), "search": ("SEARCH_INDEX_ENTRY",),
    "support": ("SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT", "SECONDARY_IDENTIFIER"),
    "analytics": ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY", "SECONDARY_IDENTIFIER"),
    "export": ("USER_EXPORT",), "backup": ("BACKUP_ARCHIVE",), "data_flow": ("DATA_FLOW",),
    "audit_record": ("SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"), "marketing": ("MARKETING_PROFILE",),
    "object": ("SUPPORT_ATTACHMENT", "USER_EXPORT", "SECONDARY_IDENTIFIER"), "cache": ("CACHE_ENTRY",),
    "email_delivery": ("EMAIL_DELIVERY_LOG",), "telemetry": ("PRODUCT_TELEMETRY",),
}
# Request wording that signals deletion (several languages); used only to question a NOT_DELETION call.
# Measured on all 550 requests: matches every request except the 9 plain non-deletion ones and sample case_26.
_DELETION_WORDS = re.compile(
    r"(?i)\b(delet\w*|eras\w*|remov\w*|close (?:my|this|the) account|closing my account|purge|"
    r"suppr\w*|effac\w*|elimin\w*|borr\w*|exclu\w*|apag\w*|lösch\w*|losch\w*|entfern\w*|verwijder\w*|"
    r"cancel\w* (?:my )?account)\b|削除|삭제|删除|удал")
# A negation shortly before the deletion word in the same clause ("do not close the account", "without removing",
# "not a request to erase it"); "no longer" does not negate the request.
_NEGATED = re.compile(r"(?i)\b(?:not|don'?t|do not|never|without|nicht|kein\w*|pas|não|nao|sin|n't)\b(?!\s+longer)"
                      r"(?:\W+\w+){0,4}\W*$")
# Absence is read only from structured state fields, never from free text (security re-check H2).
_ABSENT_VALUES = {"not_found", "absent", "deleted"}
# Key=value log fields that mean "this lookup found nothing" (security pass N1: logs are state evidence too).
_KV_ABSENT = {"http_status": {"404", "410"}, "rows": {"0"}, "total_hits": {"0"}, "available": {"false"},
              "result": {"miss", "not_found", "absent", "deleted"}}
# Fields of a log line that describe the query, not the record, so an identifier there is not exposure.
_KV_QUERY_FIELDS = {"lookup_key", "correlation_key", "trace_id", "requestid", "request_id"}


def load_rules(dataset_dir):
    with open(dataset_dir / "deletion_rules.csv", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _p(kind, text):
    return (kind, text)


def _evidence_problem(r, ctx):
    store = ctx.world.store
    eid = r.evidence_id
    cap = store.captured.get(eid)
    recs = [x for x in store.records if x.file_id == eid]
    if cap is None and not recs:
        return f"{r.resource_id}: evidence_id {eid!r} does not exist"
    if eid not in store.captured:
        return (f"{r.resource_id}: {eid} is a workflow/activity or identity-audit row, which does not show the "
                f"record's state; cite a captured evidence file (evidence_*) that shows it, or drop the resource")
    if cap > ctx.cutoff:
        return f"{r.resource_id}: evidence {eid} was captured {cap}, after audit_at {ctx.cutoff}"
    mentions = any(r.resource_id in x.resource_ids for x in recs)
    proves_absence = any(a["evidence_id"] == eid for a in ctx.absence(r.resource_id))
    if not (mentions or proves_absence):
        return f"{r.resource_id}: evidence {eid} does not mention it and does not prove its absence"
    return None


def _kv(rec, key):
    """A record field, lower-cased, without the quoting of escaped-JSON log lines (\"x\")."""
    return rec.fields.get(key, "").replace("\\", "").strip("\"' ").lower()


def _is_kv_line(rec):
    return rec.kind == "text" and rec.text.count("|") < 3 and "resource_id" in rec.fields


def _state_records(rid, ctx):
    """Structured observations OF this resource (its own SQL row / JSON record / handover line / key=value log
    line) <= audit_at. A row that merely references the resource (e.g. a search document's source_resource_id)
    does not count, nor does a log line that only records a lookup being started (event=*_started). A PDF / screenshot
    counts only when it shows the subject's identifiers next to the record."""
    out = []
    for rec in ctx.world.by_resource.get(rid, []):
        if rec.captured_at > ctx.cutoff:
            continue
        if rec.kind in ("sql", "json"):
            if rec.fields.get("resource_id") == rid:
                out.append(rec)
        elif rec.kind == "text" and rec.text.count("|") >= 3 and rec.text.split("|")[2].strip() == rid:
            out.append(rec)
        elif _is_kv_line(rec) and _kv(rec, "resource_id") == rid.lower() and not _kv(rec, "event").endswith("_started"):
            out.append(rec)
        elif rec.kind in ("pdf", "png") and rid in rec.resource_ids and _exposes_subject(rec, ctx, False):
            # A document / screen naming the record next to the subject's identifiers shows the data is there.
            out.append(rec)
    return sorted(out, key=lambda x: (x.captured_at, x.line_no))


def _shows_absent(rec):
    """True if the record's own state fields say it was not found / is gone. Free text (notes, messages, file
    names, email local parts) never counts, so evidence content cannot make a present record look deleted."""
    if rec.kind in ("sql", "json"):
        return (_kv(rec, "result") in _ABSENT_VALUES or _kv(rec, "status") in _ABSENT_VALUES
                or _kv(rec, "status_code") in ("404", "410"))
    if rec.kind == "text" and rec.text.count("|") >= 3:
        return any(col.strip() in ("NOT_FOUND", "ABSENT", "DELETED") for col in rec.text.split("|")[3:5])
    return _is_kv_line(rec) and any(_kv(rec, k) in v for k, v in _KV_ABSENT.items())


def deletion_wording(text):
    """Deletion words in the request that are not negated in their own clause (to question NOT_DELETION)."""
    found = []
    for m in _DELETION_WORDS.finditer(text):
        clause = re.split(r"[.!?;,\n]", text[max(0, m.start() - 60):m.start()])[-1]
        if not _NEGATED.search(clause):
            found.append(m.group(0))
    return found


def _still_present(rid, recs, ctx):
    """The record (latest that shows it present) if evidence says it still exists at audit_at, else None.
    SQL rows are compared per table (a row in a replica table stays present until that same table lacks it);
    other observations count only if they are the newest observation and no later absence exists."""
    absences = ctx.absence(rid)
    for rec in sorted(recs, key=lambda x: x.captured_at, reverse=True):
        if _shows_absent(rec):
            continue
        if rec.kind == "sql":
            later_gone = [a for a in absences if a["container"] == rec.location and a["captured_at"] > rec.captured_at]
            if not later_gone:
                return rec
    newest = max(recs, key=lambda x: (x.captured_at, x.line_no))
    if newest.kind != "sql" and not _shows_absent(newest):
        if not [a for a in absences if a["captured_at"] > newest.captured_at]:
            return newest
    return None


def _exposes_subject(rec, ctx, include_user_id):
    if not ctx.ident:
        return False
    subject = ctx.ident.emails | ctx.ident.hashes | ctx.ident.phones | {ctx.user_id}
    if _is_kv_line(rec):
        # Only the record's own fields count; the query key / correlation id is how the line was found.
        own = {k: v for k, v in rec.fields.items() if k.lower() not in _KV_QUERY_FIELDS}
        if not include_user_id:
            subject = subject - {ctx.user_id}
        values = " ".join(own.values()).lower()
        return any(x and str(x).lower() in values for x in subject) or "IDENTIFIABLE" in rec.text
    ids = set(rec.emails) | set(rec.hashes) | set(rec.phones)
    if include_user_id:
        ids |= set(rec.user_ids)
    return bool(ids & subject) or "IDENTIFIABLE" in rec.text


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes")


def expected_system(rid, ctx):
    hint = ctx._system_hint(rid)
    return _PREFIX_SYSTEM.get(prefix_of(rid)) or hint or None


def state_contradiction(r, ctx):
    """A sentence if the latest structured evidence contradicts compliant=true, else None."""
    if not r.compliant:
        return None
    recs = _state_records(r.resource_id, ctx)
    if not recs:
        return None
    latest_cap = max(x.captured_at for x in recs)
    latest = [x for x in recs if x.captured_at == latest_cap]
    shown = latest[-1]
    if r.treatment == "DELETE":
        src = _still_present(r.resource_id, recs, ctx)
        if src is not None:
            return (f"{r.resource_id}: compliant=true (deleted) contradicts {src.file_id} captured {src.captured_at} "
                    f"({src.location}), which still holds the record")
        return None
    if r.treatment in ("ANONYMIZE", "REDACT"):
        hit = [x for x in latest if _exposes_subject(x, ctx, include_user_id=r.treatment == "ANONYMIZE")]
        if hit:
            return (f"{r.resource_id}: compliant=true contradicts {hit[-1].file_id} captured {latest_cap}, which "
                    f"still shows the subject's identifiers")
        return None
    if r.treatment == "BLOCK_RESTORE":
        if (_truthy(_kv(shown, "restore_allowed")) or _truthy(_kv(shown, "eligible"))
                or _kv(shown, "decision") in ("allow", "allowed", "approve", "approved")
                or re.search(r"\|\s*ACTIVE\b", shown.text)):
            return (f"{r.resource_id}: compliant=true (restore blocked) contradicts {shown.file_id} captured "
                    f"{latest_cap}, which shows restore still allowed")
        return None
    if r.treatment == "DISABLE_FLOW":
        if _truthy(_kv(shown, "enabled")) or re.search(r"\|\s*ACTIVE\b", shown.text):
            return (f"{r.resource_id}: compliant=true (flow disabled) contradicts {shown.file_id} captured "
                    f"{latest_cap}, which shows the flow still enabled")
    return None


_WORKFLOW_OP = {"DELETE_RECORD": "DELETE", "REDACT_RECORD": "REDACT", "ANONYMIZE_RECORD": "ANONYMIZE"}


def _workflow_treatment(rid, ctx):
    """The treatment the deletion workflow attempted on this record (<= audit_at), or None.
    The operation is a claim about the result, but a reliable signal of the category: it matched the treatment
    on all 70 sample resources it covers. Used to catch a same-type category swap (security pass N1 follow-up)."""
    ops = [rec for rec in ctx.world.by_resource.get(rid, []) if rec.kind == "activity"
           and rec.captured_at <= ctx.cutoff and rec.fields.get("event_type") in _WORKFLOW_OP]
    return _WORKFLOW_OP[max(ops, key=lambda x: x.captured_at).fields["event_type"]] if ops else None


def _resource_problems(r, ctx, linked, rule_category):
    out = []
    if r.resource_id not in linked:
        out.append(_p("link", f"{r.resource_id}: not linked to the subject (not in list_candidates); remove it or "
                              f"justify with a candidate resource"))
    allowed = ALLOWED_CATEGORIES.get(prefix_of(r.resource_id))
    if allowed and r.data_category not in allowed:
        out.append(_p("category_type", f"{r.resource_id}: {r.data_category!r} is not a valid category for a "
                                       f"{prefix_of(r.resource_id)}_* record (allowed: {', '.join(allowed)})"))
    op = _workflow_treatment(r.resource_id, ctx)
    if (op and op != r.treatment and allowed
            and op in {TREATMENT_BY_CATEGORY[c] for c in allowed}):
        out.append(_p("category_op", f"{r.resource_id}: the deletion workflow attempted {op} on it, but you chose "
                                     f"{r.data_category}/{r.treatment}; re-check which category it is"))
    expected = TREATMENT_BY_CATEGORY.get(r.data_category)
    if expected is None:
        out.append(_p("category", f"{r.resource_id}: unknown data_category {r.data_category!r}"))
    elif expected != r.treatment:
        out.append(_p("category", f"{r.resource_id}: treatment {r.treatment} does not match data_category "
                                  f"{r.data_category} (policy requires {expected})"))
    if r.data_category == "DATA_FLOW":
        if r.rule_id:
            out.append(_p("rule", f"{r.resource_id}: DATA_FLOW has no rule; rule_id must be ''"))
    elif rule_category.get(r.rule_id) != r.data_category:
        out.append(_p("rule", f"{r.resource_id}: rule_id {r.rule_id!r} is not the rule for {r.data_category}"))
    if r.treatment == "RETAIN" and not r.compliant:
        out.append(_p("retain", f"{r.resource_id}: RETAIN records cannot be remediated; include it only if it is "
                                f"present (compliant=true), otherwise drop it and add a flag"))
    system = expected_system(r.resource_id, ctx)
    if system and r.system_id != system:
        out.append(_p("system", f"{r.resource_id}: system_id {r.system_id!r} should be {system!r}"))
    problem = _evidence_problem(r, ctx)
    if problem:
        out.append(_p("evidence", problem))
    contradiction = state_contradiction(r, ctx)
    if contradiction:
        out.append(_p("contradiction", contradiction))
    return out


def _check(verdict, ctx, rules):
    """Returns (case-level problems, {index: [(kind, text)]})."""
    if verdict.intent == "NOT_DELETION":
        words = deletion_wording(ctx.request.get("request_text", ""))
        if words:
            found = sorted(set(words))
            return [f"intent NOT_DELETION but the request contains deletion wording ({', '.join(found)[:80]}); "
                    f"confirm the person is not asking to delete their account or data"], {}
        return [], {}
    linked = {c["resource_id"] for c in ctx.candidates()}
    rule_category = {r["rule_id"]: r["data_category"] for r in rules}
    case_level, per_resource, seen = [], {}, set()
    for i, r in enumerate(verdict.resources):
        problems = _resource_problems(r, ctx, linked, rule_category)
        if r.resource_id in seen:
            problems.append(_p("duplicate", f"{r.resource_id}: duplicate resource entry"))
        seen.add(r.resource_id)
        if problems:
            per_resource[i] = problems
    accounts = [r.resource_id for r in verdict.resources if prefix_of(r.resource_id) == "account"]
    if len(accounts) != 1 and any(prefix_of(c) == "account" for c in linked):
        case_level.append(f"a deletion case concerns exactly one primary account_*; you listed {accounts or 'none'}")
    return case_level, per_resource


def validate(verdict, ctx, rules):
    case_level, per_resource = _check(verdict, ctx, rules)
    return case_level + [text for ps in per_resource.values() for _, text in ps]


def _latest_visible_evidence(rid, ctx):
    obs = [o for o in ctx.timeline(rid) if o["kind"] not in ("activity", "audit")]
    absent = ctx.absence(rid)
    best = max([(o["captured_at"], o["evidence_id"]) for o in obs] +
               [(a["captured_at"], a["evidence_id"]) for a in absent], default=None)
    return best[1] if best else None


_REPAIRABLE = {"evidence", "system", "contradiction"}


def _evidence_shows_unremediated(r, ctx):
    """Why dropping this resource would be unsafe, or '' if it is not.
    Unsafe when it is tied to the subject by identity (hard / trace / co_mentioned link — we know it is theirs but
    could not accept the verdict on it), or when structured evidence shows it still needs treatment."""
    cands = {c["resource_id"]: c for c in ctx.candidates()}
    if r.resource_id not in cands:
        return ""
    identity_links = [l for l in cands[r.resource_id]["links"] if l["type"] != "workflow_burst"]
    if identity_links:
        return f"identity-linked via {identity_links[0]['type']} in {identity_links[0]['evidence_id']}"
    allowed = ALLOWED_CATEGORIES.get(prefix_of(r.resource_id))
    if not allowed:
        return ""
    default = TREATMENT_BY_CATEGORY[allowed[0]]
    probe = r.model_copy(update={"treatment": default, "data_category": allowed[0], "compliant": True})
    return state_contradiction(probe, ctx) or ""


def finalize(verdict, ctx, rules):
    """Make the verdict safe to compile; returns (verdict, flags). Every change is flagged."""
    flags = []
    case_level, per_resource = _check(verdict, ctx, rules)
    flags.extend(f"unresolved: {p}" for p in case_level)
    if verdict.intent == "NOT_DELETION":
        return verdict, flags
    kept = []
    for i, r in enumerate(verdict.resources):
        problems = per_resource.get(i, [])
        kinds = {k for k, _ in problems}
        if not problems:
            kept.append(r)
            continue
        if not kinds <= _REPAIRABLE:
            flags.append(f"dropped {r.resource_id}: {'; '.join(t for _, t in problems)}")
            shows = _evidence_shows_unremediated(r, ctx)
            if shows:
                # Dropping a subject's record that a file still shows would hide a violation (security pass H1/H2).
                flags.append(f"UNSAFE: dropped {r.resource_id} although evidence shows it unremediated ({shows}); "
                             f"case needs review")
            continue
        fixed = r
        if "system" in kinds:
            fixed = fixed.model_copy(update={"system_id": expected_system(r.resource_id, ctx)})
            flags.append(f"system_id repaired for {r.resource_id}: {r.system_id!r} -> {fixed.system_id!r}")
        if "contradiction" in kinds:
            fixed = fixed.model_copy(update={"compliant": False})
            flags.append(f"SAFETY OVERRIDE {r.resource_id}: compliant -> false ("
                         f"{next(t for k, t in problems if k == 'contradiction')})")
        if "evidence" in kinds or "contradiction" in kinds:
            repl = _latest_visible_evidence(r.resource_id, ctx)
            if not repl:
                flags.append(f"dropped {r.resource_id}: no visible evidence")
                continue
            if repl != fixed.evidence_id:
                flags.append(f"citation repaired for {r.resource_id}: {fixed.evidence_id} -> {repl}")
                fixed = fixed.model_copy(update={"evidence_id": repl})
        kept.append(fixed)
    flags.extend(_omitted_unremediated(verdict, kept, ctx))
    return verdict.model_copy(update={"resources": kept}), flags


def _owned_by_other(rid, ctx):
    """True if evidence <= audit_at ties the record to another user_id and never to the subject's (a contested link,
    e.g. a data-catalog LINK_RESOURCE_IDENTITY to someone else): leaving it out is a legitimate call."""
    users = {u for rec in ctx.world.by_resource.get(rid, []) if rec.captured_at <= ctx.cutoff for u in rec.user_ids}
    return bool(users) and ctx.user_id not in users


def _omitted_unremediated(verdict, kept, ctx):
    """UNSAFE flags for subject-linked candidates the verdict left out although evidence shows them unremediated
    (security pass N2). Omission alone is allowed (decoys, ambiguous links); omission of a record the latest
    evidence still shows holding the subject's data is not."""
    listed = {r.resource_id for r in verdict.resources} | {r.resource_id for r in kept}
    out = []
    for c in ctx.candidates():
        rid = c["resource_id"]
        if rid in listed or not any(l["type"] != "workflow_burst" for l in c["links"]) or _owned_by_other(rid, ctx):
            continue
        allowed = ALLOWED_CATEGORIES.get(prefix_of(rid))
        if not allowed:
            continue
        # The right category is unknown (the agent never chose one), so any allowed treatment it fails counts.
        shown = next(filter(None, (state_contradiction(ResourceVerdict(
            resource_id=rid, system_id=expected_system(rid, ctx) or "", data_category=cat,
            treatment=TREATMENT_BY_CATEGORY[cat], rule_id="", compliant=True, evidence_id="", reason=""), ctx)
            for cat in allowed)), None)
        if shown:
            out.append(f"UNSAFE: {rid} is linked to the subject but missing from the verdict, and evidence shows "
                       f"it unremediated ({shown}); case needs review")
    return out


def fallback_resources(ctx, rules):
    """Non-compliant entries for subject-linked records when a deletion verdict ended up empty (final-run decision).
    COMPLETE needs proof that every copy has its treatment; an identity-linked record with no such proof is
    reported. Cites the newest captured file showing it, else the activity row that links it to the subject."""
    rule_for = {r["data_category"]: r["rule_id"] for r in rules}
    out = []
    for c in ctx.candidates():
        rid = c["resource_id"]
        links = [l for l in c["links"] if l["type"] != "workflow_burst"]
        allowed = ALLOWED_CATEGORIES.get(prefix_of(rid))
        if not links or not allowed or _owned_by_other(rid, ctx):
            continue
        cat = allowed[0]
        evidence = _latest_visible_evidence(rid, ctx) or links[0]["evidence_id"]
        out.append(ResourceVerdict(
            resource_id=rid, system_id=expected_system(rid, ctx) or "", data_category=cat,
            treatment=TREATMENT_BY_CATEGORY[cat], rule_id="" if cat == "DATA_FLOW" else rule_for.get(cat, ""),
            compliant=False, evidence_id=evidence, reason="linked to the subject; compliance not proven by evidence"))
    return out
