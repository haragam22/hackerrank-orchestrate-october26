"""Identity index: current snapshot + audit history -> every hard identifier a person has had."""
import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ingest.extract import digits


@dataclass
class Identity:
    user_id: str
    name: str                 # informational only; never used for linking
    email: str                # current email (lowercase)
    emails: set = field(default_factory=set)
    phones: set = field(default_factory=set)   # digits only
    hashes: set = field(default_factory=set)
    changes: list = field(default_factory=list)  # audit rows (audit_id, event_time, field_name)

    def identifiers(self):
        return self.emails | self.phones | self.hashes | {self.user_id}


def _norm(field_name, value):
    value = (value or "").strip()
    if not value:
        return ""
    return digits(value) if field_name == "phone" else value.lower()


class People:
    def __init__(self, identity_rows, audit_rows):
        self._by_user = {}
        self._owner = {}
        for row in identity_rows:
            email = row["email"].strip().lower()
            ident = Identity(user_id=row["user_id"], name=row["name"], email=email)
            ident.emails.add(email)
            if row.get("phone"):
                ident.phones.add(digits(row["phone"]))
            if row.get("email_hash"):
                ident.hashes.add(row["email_hash"].lower())
            self._by_user[row["user_id"]] = ident
        for row in audit_rows:
            ident = self._by_user.get(row["user_id"])
            if ident is None:
                continue
            ident.changes.append({"audit_id": row["audit_id"], "event_time": row["event_time"],
                                  "field_name": row["field_name"]})
            target = {"email": ident.emails, "phone": ident.phones, "email_hash": ident.hashes}.get(row["field_name"])
            if target is not None:
                for v in (row["old_value"], row["new_value"]):
                    if _norm(row["field_name"], v):
                        target.add(_norm(row["field_name"], v))
        for ident in self._by_user.values():
            ident.hashes |= {hashlib.sha256(e.encode("utf-8")).hexdigest() for e in ident.emails}
            for value in ident.identifiers():
                self._owner.setdefault(value, ident.user_id)

    @classmethod
    def from_dataset(cls, dataset_dir):
        dataset_dir = Path(dataset_dir)

        def rows(name):
            with open(dataset_dir / name, encoding="utf-8", newline="") as fh:
                return list(csv.DictReader(fh))

        return cls(rows("user_identities.csv"), rows("user_audit_log.csv"))

    def owner_of(self, identifier):
        if not identifier:
            return None
        key = identifier.strip()
        return self._owner.get(key.lower()) or self._owner.get(digits(key)) if key else None

    def resolve_email(self, email):
        return self._owner.get((email or "").strip().lower())

    def identity(self, user_id):
        return self._by_user.get(user_id)

    def owners_in(self, record):
        """User ids whose hard identifiers appear in a record."""
        found = set()
        for value in record.emails + record.hashes + record.phones + record.user_ids:
            uid = self._owner.get(value)
            if uid:
                found.add(uid)
        return found
