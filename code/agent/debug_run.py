"""Dev tool: run the live agent on given sample cases and print verdict vs gold resources.

Run: python code/agent/debug_run.py case_21 case_09
"""
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATASET_DIR, get_settings  # noqa: E402
from llm.factory import build_gateway  # noqa: E402
from llm.usage import UsageLog  # noqa: E402
from pipeline import build_world, investigate_case  # noqa: E402


def _rows(name):
    with open(DATASET_DIR / name, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def main(case_ids):
    settings = get_settings()
    gateway = build_gateway(settings)
    world = build_world()
    reqs = {r["case_id"]: r for r in _rows("sample_deletion_requests.csv")}
    gold = {r["case_id"]: r for r in _rows("sample_output.csv")}
    log = UsageLog(settings.cache_dir / "usage_debug.jsonl", prices=settings.prices())

    def one(cid):
        started = time.time()
        res = investigate_case(world, reqs[cid], gateway, settings.max_turns)
        for c in res.completions:
            log.record(c, case_id=cid, purpose="investigator")
        return cid, res, time.time() - started

    with ThreadPoolExecutor(max_workers=settings.parallel) as pool:
        for cid, res, secs in pool.map(one, case_ids):
            g = {(s["resource_id"], s["operation"]) for s in json.loads(gold[cid]["steps_json"])}
            cost = sum(log.cost(c.usage, c.cached) or 0 for c in res.completions)
            print(f"\n=== {cid} gold={gold[cid]['deletion_status']} turns={res.turns} forced={res.forced_finish} "
                  f"{secs:.0f}s tokens_in={sum(c.usage.input_tokens for c in res.completions)} "
                  f"tokens_out={sum(c.usage.output_tokens for c in res.completions)} cost=${cost:.3f} "
                  f"fallback={any(c.fallback_used for c in res.completions)} flags={res.flags}")
            print("gold:", sorted(g))
            if res.verdict:
                v = res.verdict
                print("intent:", v.intent, "|", v.intent_reason[:100])
                for r in v.resources:
                    print(f"  {r.resource_id:20} {r.data_category:20} {r.treatment:13} compliant={r.compliant!s:5} "
                          f"{r.evidence_id:12} {r.reason[:90]}")
                print("agent flags:", v.flags)
            for t in res.transcript:
                print("  >", t["thought"][:120], [c["tool"] for c in t["calls"]])


if __name__ == "__main__":
    main(sys.argv[1:])
