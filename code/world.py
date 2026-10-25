"""Global indexes over all evidence, shared by every case: ownership links, trace groups, activity bursts."""
import csv
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BURST_HOURS = 2  # workflow actions for a case fall within ±2 h of its reported_at (measured on samples)
WORKFLOW_SOURCES = {"deletion_worker"}


def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_requests(dataset_dir):
    rows = []
    for name in ("deletion_requests.csv", "sample_deletion_requests.csv"):
        with open(Path(dataset_dir) / name, encoding="utf-8", newline="") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


class World:
    def __init__(self, store, people, requests):
        self.store = store
        self.people = people
        self.requests = {r["case_id"]: r for r in requests}
        self.by_resource = defaultdict(list)
        # resource -> [(captured_at, user_id, evidence_id, link_type)]
        self.links = defaultdict(list)
        for rec in store.records:
            for rid in rec.resource_ids:
                self.by_resource[rid].append(rec)
        self._index_direct_links()
        self._index_trace_links()
        self.workflow = sorted((r for r in store.records if r.kind == "activity"
                                and r.fields.get("source_system") in WORKFLOW_SOURCES),
                               key=lambda r: r.record_time)

    def _index_direct_links(self):
        for rec in self.store.records:
            if not rec.resource_ids:
                continue
            if rec.kind == "activity" and rec.fields.get("event_type") != "LINK_RESOURCE_IDENTITY":
                continue  # ordinary activity rows carry no identity
            owners = self.people.owners_in(rec)
            link_type = "hard" if len(rec.resource_ids) == 1 else "co_mentioned"
            for rid in rec.resource_ids:
                for uid in owners:
                    self.links[rid].append((rec.captured_at, uid, rec.file_id, link_type))

    def _index_trace_links(self):
        groups = defaultdict(list)
        for rec in self.store.records:
            trace = rec.fields.get("trace_id")
            if trace and rec.kind == "text":
                groups[(rec.file_id, trace)].append(rec)
        for (file_id, _), recs in groups.items():
            owners = set().union(*(self.people.owners_in(r) for r in recs))
            if not owners:
                continue
            for rec in recs:
                if self.people.owners_in(rec):
                    continue  # already a direct link
                for rid in rec.resource_ids:
                    for uid in owners:
                        self.links[rid].append((rec.captured_at, uid, file_id, "trace"))

    def owners(self, resource_id, cutoff):
        """{user_id: [(link_type, evidence_id, captured_at)]} visible at cutoff."""
        out = defaultdict(list)
        for cap, uid, eid, kind in self.links.get(resource_id, []):
            if cap <= cutoff:
                out[uid].append((kind, eid, cap))
        return out

    def burst(self, request):
        rep = parse_ts(request["reported_at"])
        lo, hi = rep - timedelta(hours=BURST_HOURS), rep + timedelta(hours=BURST_HOURS)
        cutoff = request["audit_at"]
        return [r for r in self.workflow if lo <= parse_ts(r.record_time) <= hi and r.record_time <= cutoff]
