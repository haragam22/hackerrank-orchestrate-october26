"""Per-case, time-gated view of the World. Everything here only sees evidence captured at or before audit_at."""
import re

# Which snapshot containers can prove a resource absent: keyword in the table/collection name per id prefix.
# Order matters: specific types first, the generic account keywords last ("customer_analytics_profiles" is an
# analytics container, not an account container).
_CONTAINER_KEYWORDS = {
    "search": ("search",), "analytics": ("analytics",), "support": ("support",), "export": ("export",),
    "data_flow": ("connector", "data_flow"), "backup": ("backup", "restore"), "audit_record": ("audit",),
    "tombstone": ("deleted_customer", "tombstone"), "account": ("profile", "account"),
}


def container_type(name):
    lowered = name.lower()
    return next((prefix for prefix, kws in _CONTAINER_KEYWORDS.items() if any(k in lowered for k in kws)), None)
_PREFIX_SYSTEM = {
    "account": "account_service", "search": "search_service", "analytics": "analytics_platform",
    "support": "support_platform", "export": "export_service", "data_flow": "integration_service",
    "backup": "backup_service", "audit_record": "security_service", "tombstone": "identity_service",
}


def prefix_of(resource_id):
    return resource_id.rsplit("_", 1)[0]


class CaseContext:
    def __init__(self, world, request, ownership_any_time=False):
        self.world = world
        self.request = request
        self.cutoff = request["audit_at"]
        # Ownership (who a resource belongs to) may optionally use evidence captured after audit_at;
        # resource *state* and citations always use only evidence <= audit_at.
        self.owner_cutoff = "9999" if ownership_any_time else self.cutoff
        self.user_id = world.people.resolve_email(request["email"])
        self.ident = world.people.identity(self.user_id) if self.user_id else None

    def identity(self):
        if not self.ident:
            return {"resolved": False, "email": self.request["email"],
                    "flags": ["request email matches no known identity"]}
        changes = [c for c in self.ident.changes if c["event_time"] <= self.cutoff]
        return {"resolved": True, "user_id": self.user_id,
                "request_email_is_current": self.request["email"].lower() == self.ident.email,
                "emails": sorted(self.ident.emails), "phones": sorted(self.ident.phones),
                "email_hashes": sorted(self.ident.hashes), "identity_changes": changes}

    def candidates(self):
        """Resources linked to the subject, each with every supporting link."""
        if not self.user_id:
            return []
        found = {}

        def add(rid, link):
            entry = found.setdefault(rid, {"resource_id": rid, "links": []})
            if link not in entry["links"]:
                entry["links"].append(link)

        for rid, links in self.world.links.items():
            for cap, uid, eid, kind in links:
                if uid == self.user_id and cap <= self.owner_cutoff:
                    link = {"type": kind, "evidence_id": eid, "captured_at": cap}
                    if cap > self.cutoff:
                        link["after_audit"] = True  # ownership only; never cite this evidence
                    add(rid, link)
        for rec in self.world.burst(self.request):
            for rid in rec.resource_ids:
                others = set(self.world.owners(rid, self.owner_cutoff)) - {self.user_id}
                if others:
                    continue  # provably someone else's record
                add(rid, {"type": "workflow_burst", "evidence_id": rec.file_id, "captured_at": rec.captured_at,
                          "event_type": rec.fields.get("event_type", ""),
                          "reported_result": rec.fields.get("reported_result", "")})
        for entry in found.values():
            entry["links"].sort(key=lambda l: (l["type"] == "workflow_burst", l["captured_at"]))
            entry["system_hint"] = self._system_hint(entry["resource_id"])
        return sorted(found.values(), key=lambda e: e["resource_id"])

    def _system_hint(self, rid):
        for rec in self.world.by_resource.get(rid, []):
            if rec.captured_at <= self.cutoff and rec.kind == "activity":
                return rec.fields.get("target_system", "")
        return _PREFIX_SYSTEM.get(prefix_of(rid), "")

    def timeline(self, resource_id, limit=40):
        recs = [r for r in self.world.by_resource.get(resource_id, []) if r.captured_at <= self.cutoff]
        recs.sort(key=lambda r: (r.record_time or r.captured_at, r.captured_at))
        return [{"evidence_id": r.file_id, "kind": r.kind, "captured_at": r.captured_at,
                 "record_time": r.record_time, "system_hint": r.system_hint,
                 "text": r.text[:1600] if r.kind in ("png", "pdf") else r.text[:600]}
                for r in recs[-limit:]]

    def absence(self, resource_id):
        """Snapshots (<= audit_at) whose container for this resource type does not contain it."""
        prefix = prefix_of(resource_id)
        out = []
        store = self.world.store
        for fid, cov in store.coverage.items():
            cap = store.captured.get(fid, "")
            if not cap or cap > self.cutoff:
                continue
            scope = cov.get("__scope__")
            if scope and _PREFIX_SYSTEM.get(prefix) not in scope:
                continue
            for container, ids in cov.items():
                if container == "__scope__":
                    continue
                typed = any(prefix_of(i) == prefix for i in ids)
                named = container_type(container) == prefix and not any(prefix_of(i) != prefix for i in ids)
                if (typed or named) and resource_id not in ids:
                    out.append({"evidence_id": fid, "captured_at": cap, "container": container})
        return sorted(out, key=lambda a: a["captured_at"])
