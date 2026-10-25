"""Wiring: build the shared World once, then audit one case end to end:
agent investigation (with validator feedback) -> finalize -> deterministic compile."""
import re
from dataclasses import dataclass, field

from agent.loop import investigate
from agent.skeptic import Skeptic
from case_context import CaseContext
from compiler import compile_steps
from config import CACHE_DIR, DATASET_DIR
from ingest.loader import cached_windows_ocr, load_evidence
from people import People
from tools import Toolbox
from validator import fallback_resources, finalize, load_rules, validate
from world import World, load_requests, parse_ts


def build_world(dataset_dir=DATASET_DIR, cache_dir=CACHE_DIR):
    store = load_evidence(dataset_dir, ocr=cached_windows_ocr(cache_dir / "ocr"))
    return World(store, People.from_dataset(dataset_dir), load_requests(dataset_dir))


@dataclass
class CaseResult:
    case_id: str
    deletion_status: str
    steps: list
    ok: bool                       # False when the agent produced no usable verdict
    flags: list = field(default_factory=list)
    fallback_used: bool = False
    turns: int = 0
    validation_rounds: int = 0
    completions: list = field(default_factory=list)
    verdict: dict | None = None


_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_CASE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # case ids become file names (security pass H3)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_STATUSES = {"COMPLETE", "PARTIALLY_COMPLETE", "NOT_STARTED"}


def request_problems(request):
    """Input validation: a malformed request is rejected before any model call."""
    problems = []
    if not _CASE_ID.fullmatch(str(request.get("case_id", ""))):
        problems.append(f"case_id {str(request.get('case_id', ''))[:40]!r} must be 1-64 letters, digits, _ or -")
    if not _EMAIL.match(str(request.get("email", "")).strip()):
        problems.append(f"email {request.get('email', '')!r} is not an email address")
    for field_name in ("requested_at", "reported_at", "audit_at"):
        value = str(request.get(field_name, ""))
        try:
            ok = bool(_TS.match(value)) and parse_ts(value) is not None
        except ValueError:
            ok = False
        if not ok:
            problems.append(f"{field_name} {value!r} is not an ISO-8601 UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)")
    if request.get("reported_status") not in _STATUSES:
        problems.append(f"reported_status {request.get('reported_status')!r} is not one of {sorted(_STATUSES)}")
    if not isinstance(request.get("request_text", ""), str):
        problems.append("request_text is not text")
    return problems


def audit_case(world, request, gateway, max_turns, dataset_dir=DATASET_DIR, rules=None, use_skeptic=False):
    invalid = request_problems(request)
    if invalid:
        return CaseResult(str(request.get("case_id", "")), "INCOMPLETE", [], False,
                          [f"INVALID REQUEST: {p}" for p in invalid])
    rules = rules or load_rules(dataset_dir)
    ctx = CaseContext(world, request)
    toolbox = Toolbox(ctx, dataset_dir)
    skeptic = Skeptic(gateway, toolbox) if use_skeptic else None
    res = investigate(gateway, toolbox, max_turns=max_turns, validate=lambda v: validate(v, ctx, rules),
                      review=skeptic)
    if skeptic:
        res.completions.extend(skeptic.completions)
        res.flags.extend(skeptic.flags)
        res.flags.extend(f"skeptic challenged: {c}"[:300] for c in res.review_challenges)
    flags = list(res.flags)
    fallback = any(c.fallback_used for c in res.completions)
    if fallback:
        flags.append("FALLBACK MODEL USED for at least one call")
    if res.verdict is None:
        # No usable verdict: emit a schema-valid placeholder and mark the case for re-run (--resume).
        flags.append("NO VERDICT: agent failed; placeholder row, re-run this case")
        return CaseResult(request["case_id"], "INCOMPLETE", [], False, flags, fallback, res.turns,
                          res.validation_rounds, res.completions, None)
    verdict, fix_flags = finalize(res.verdict, ctx, rules)
    flags.extend(fix_flags)
    fallback_findings = []
    if verdict.intent == "DELETION" and not verdict.resources:
        # Nothing proven compliant: report subject-linked records instead of an empty COMPLETE (final-run decision).
        fallback_findings = fallback_resources(ctx, rules)
        if fallback_findings:
            verdict = verdict.model_copy(update={"resources": fallback_findings})
            flags.append("FALLBACK FINDINGS: empty verdict; reported unproven subject-linked records "
                         + ", ".join(r.resource_id for r in fallback_findings) + "; review")
    flags.extend(f"agent: {f}" for f in verdict.flags)
    status, steps = compile_steps(verdict)
    ok = True
    if any(f.startswith("UNSAFE") for f in fix_flags):
        ok = False  # a dropped resource is still shown unremediated: never a trusted result (security pass H1/H2)
    if any(f.startswith("unresolved:") for f in fix_flags):
        ok = False  # e.g. no / several primary accounts: the verdict is structurally wrong (security pass N2)
    if fallback_findings:
        ok = False  # deterministic, unreviewed findings
    if verdict.intent == "DELETION" and not verdict.resources:
        # A deletion audit with nothing verified must never pass silently as "COMPLETE, []" (security review F4).
        ok = False
        dropped = len(res.verdict.resources)
        flags.append(f"EMPTY RESULT: deletion request with no supported resources "
                     f"({dropped} proposed, all dropped); review or re-run" if dropped else
                     "EMPTY RESULT: deletion request but the agent found no resources; review or re-run")
    return CaseResult(request["case_id"], status, steps, ok, flags, fallback, res.turns, res.validation_rounds,
                      res.completions, verdict.model_dump())
