"""Scoring of predictions against gold answers.

gold / pred: {case_id: (deletion_status, [step dicts])}.
- status_accuracy: share of gold cases with the right deletion_status.
- step precision/recall/F1: steps match when phase, system_id, resource_id, operation, state and evidence_id
  are all equal (step_no and order ignored).
- field_accuracy: for gold steps aligned to a predicted step on (phase, resource_id), share whose field matches;
  unaligned gold steps count as misses for every field.
- sequence_accuracy: share of cases whose full step list (in order, ignoring step_no) equals the gold list.
"""
from collections import Counter

FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")


def _key(step):
    return tuple(step.get(f, "") for f in FIELDS)


def score(gold, pred):
    status_ok = tp = n_pred = n_gold = seq_ok = 0
    field_hits = Counter()
    per_case = {}
    for cid, (g_status, g_steps) in gold.items():
        p_status, p_steps = pred.get(cid, (None, []))
        g_keys, p_keys = Counter(map(_key, g_steps)), Counter(map(_key, p_steps))
        matched = sum((g_keys & p_keys).values())
        tp += matched
        n_gold += len(g_steps)
        n_pred += len(p_steps)
        ok = p_status == g_status
        status_ok += ok
        seq = [_key(s) for s in g_steps] == [_key(s) for s in p_steps] and cid in pred
        seq_ok += seq
        by_slot = {(s["phase"], s["resource_id"]): s for s in p_steps}
        for s in g_steps:
            other = by_slot.get((s["phase"], s["resource_id"]))
            for f in FIELDS:
                field_hits[f] += bool(other) and other.get(f, "") == s.get(f, "")
        per_case[cid] = {"status_ok": ok, "gold_status": g_status, "pred_status": p_status,
                         "matched_steps": matched, "gold_steps": len(g_steps), "pred_steps": len(p_steps),
                         "sequence_ok": seq}
    n = len(gold) or 1
    precision = tp / n_pred if n_pred else (1.0 if not n_gold else 0.0)
    recall = tp / n_gold if n_gold else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "cases": len(gold), "status_accuracy": status_ok / n, "sequence_accuracy": seq_ok / n,
        "step_precision": precision, "step_recall": recall, "step_f1": f1,
        "field_accuracy": {f: (field_hits[f] / n_gold if n_gold else 1.0) for f in FIELDS},
        "per_case": per_case,
    }
