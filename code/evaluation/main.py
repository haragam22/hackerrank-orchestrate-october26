"""Evaluation harness.

Usage (from the repository root):
    python code/evaluation/main.py                 # run agent on 32 samples + custom cases, write reports
    python code/evaluation/main.py --score-only    # re-score saved results without model calls
    python code/evaluation/main.py --usage-run final   # write usage_report.md from the full run's usage log

Writes code/evaluation/evaluation_report.md (metrics measured on this run) and, with --usage-run,
code/evaluation/usage_report.md.
"""
import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CODE_DIR, DATASET_DIR, get_settings  # noqa: E402
from evaluation.custom_cases import CUSTOM_CASES, build_request, check_custom  # noqa: E402
from evaluation.metrics import FIELDS, score  # noqa: E402

EVAL_DIR = CODE_DIR / "evaluation"
_SAMPLES = [f"case_{i:02d}" for i in range(1, 33)]
# Cases debugged live while building the agent; never part of the holdout.
DEBUGGED = {"case_04", "case_09", "case_11", "case_21", "case_28", "case_32"}
HOLDOUT = sorted(random.Random(2026).sample(sorted(set(_SAMPLES) - DEBUGGED), 12))
TUNE = [c for c in _SAMPLES if c not in HOLDOUT]


def _rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _pct(x):
    return f"{100 * x:.1f}%"


def _metrics_table(m):
    lines = ["| Metric | Value |", "|---|---|",
             f"| Cases | {m['cases']} |",
             f"| Case-level status accuracy | {_pct(m['status_accuracy'])} |",
             f"| Step precision (exact step match) | {_pct(m['step_precision'])} |",
             f"| Step recall | {_pct(m['step_recall'])} |",
             f"| Step F1 | {_pct(m['step_f1'])} |",
             f"| Sequence accuracy (whole ordered list exact) | {_pct(m['sequence_accuracy'])} |"]
    lines += [f"| Field accuracy: {f} | {_pct(v)} |" for f, v in m["field_accuracy"].items()]
    return "\n".join(lines)


def render_evaluation_report(splits, custom_results, run_info, notes=""):
    out = ["# Evaluation report", "",
           "All numbers below were **measured** by `python code/evaluation/main.py` on the run described here; "
           "nothing is estimated.", "",
           "## Run", "", "\n".join(f"- {k}: {v}" for k, v in run_info.items()), "",
           "## Method", "",
           "- Gold: `dataset/sample_output.csv` (32 public cases). Holdout = 12 cases drawn with a fixed seed from the "
           "samples that were not debugged live; tune = the other 20.",
           f"- Holdout cases: {', '.join(HOLDOUT)}",
           "- Honesty note: every sample gold answer was read while designing the evidence linking, so the holdout "
           "measures prompt over-fitting, not a fully unseen distribution.",
           "- Step match = phase, system_id, resource_id, operation, state and evidence_id all equal. "
           "Field accuracy aligns gold steps to predicted steps on (phase, resource_id).", ""]
    for name, title in (("all", "All 32 samples"), ("tune", "Tune split (20)"), ("holdout", "Holdout split (12)")):
        if name in splits:
            out += [f"## {title}", "", _metrics_table(splits[name]), ""]
    if "all" in splits:
        out += ["## Per-case results (all samples)", "",
                "| Case | Gold | Predicted | Steps matched / gold / predicted | Sequence exact |", "|---|---|---|---|---|"]
        for cid, pc in sorted(splits["all"]["per_case"].items()):
            out.append(f"| {cid} | {pc['gold_status']} | {pc['pred_status']} | "
                       f"{pc['matched_steps']} / {pc['gold_steps']} / {pc['pred_steps']} | {pc['sequence_ok']} |")
        out.append("")
    out += ["## Additional evaluation cases created by the team", "",
            "Defined in `code/evaluation/custom_cases.py` (purpose and expected outcome are documented there).", "",
            "| Case | Purpose | Passed | Detail |", "|---|---|---|---|"]
    out += [f"| {cid} | {purpose} | {'yes' if ok else 'no'} | {detail} |" for cid, purpose, ok, detail in custom_results]
    out += ["", notes]
    return "\n".join(out)


def render_usage_report(rows, title, n_requests):
    by_model = defaultdict(lambda: {"calls": 0, "cached": 0, "in": 0, "out": 0, "cost": 0.0, "fallback": 0})
    for r in rows:
        m = by_model[(r["backend"], r["model"])]
        m["calls"] += 1
        m["cached"] += bool(r.get("cached"))
        m["in"] += r["input_tokens"]
        m["out"] += r["output_tokens"]
        m["cost"] += r.get("cost_usd") or 0.0
        m["fallback"] += bool(r.get("fallback_used"))
    tot = {k: sum(m[k] for m in by_model.values()) for k in ("calls", "cached", "in", "out", "cost", "fallback")}
    n = max(n_requests, 1)
    out = [f"# Token usage and cost — {title}", "",
           "Measured from the per-call usage log written during the run (`UsageLog`). Cost = input tokens × "
           "$1.00/1M + output tokens × $5.00/1M (AgentRouter `gpt-6.1-sol` console price); cache hits and the "
           "local Ollama fallback cost $0.", "",
           "| Provider / model | Calls | Cache hits | Fallback calls | Input tokens | Output tokens | Total tokens | Cost |",
           "|---|---|---|---|---|---|---|---|"]
    for (backend, model), m in sorted(by_model.items()):
        out.append(f"| {backend} / {model} | {m['calls']:,} | {m['cached']:,} | {m['fallback']:,} | {m['in']:,} | "
                   f"{m['out']:,} | {m['in'] + m['out']:,} | ${m['cost']:.4f} |")
    out.append(f"| **Overall** | {tot['calls']:,} | {tot['cached']:,} | {tot['fallback']:,} | {tot['in']:,} | "
               f"{tot['out']:,} | {tot['in'] + tot['out']:,} | ${tot['cost']:.4f} |")
    out += ["", f"- Requests: {n_requests:,}",
            f"- Average tokens per request: {(tot['in'] + tot['out']) / n:,.0f} "
            f"({tot['in'] / n:,.0f} in / {tot['out'] / n:,.0f} out)",
            f"- Average model calls per request: {tot['calls'] / n:.2f}",
            f"- Estimated cost per request: ${tot['cost'] / n:.4f}", ""]
    return "\n".join(out)


def _load_usage(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-name", default="eval_samples")
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--usage-run", help="write usage_report.md from code/.cache/runs/<name>/usage.jsonl")
    args = ap.parse_args(argv)
    settings = get_settings()

    if args.usage_run:
        run_dir = settings.cache_dir / "runs" / args.usage_run
        rows = _load_usage(run_dir / "usage.jsonl")
        n = len(list((run_dir / "cases").glob("*.json")))
        (EVAL_DIR / "usage_report.md").write_text(
            render_usage_report(rows, f"run '{args.usage_run}'", n), encoding="utf-8")
        print(f"wrote {EVAL_DIR / 'usage_report.md'} from {len(rows)} calls / {n} cases")
        return 0

    from llm.usage import RecordingGateway, UsageLog
    from runner import ResultStore, load_run_info, run_cases, save_run_info

    samples = {r["case_id"]: r for r in _rows(DATASET_DIR / "sample_deletion_requests.csv")}
    gold = {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"]))
            for r in _rows(DATASET_DIR / "sample_output.csv")}
    customs = [build_request(c, samples[c["base_case"]]) for c in CUSTOM_CASES]
    run_dir = settings.cache_dir / "runs" / args.run_name
    store = ResultStore(run_dir / "cases")
    if not args.score_only:
        from llm.factory import build_gateway
        from pipeline import audit_case, build_world
        from validator import load_rules

        world = build_world()
        for req in customs:  # custom requests must be known to the world (burst/ownership use requests)
            world.requests[req["case_id"]] = req
        rules = load_rules(DATASET_DIR)
        usage = UsageLog(run_dir / "usage.jsonl", prices=settings.prices())
        gateway = RecordingGateway(build_gateway(settings), usage, purpose="eval")  # logs every paid call
        save_run_info(run_dir, settings)
        run_cases(list(samples.values()) + customs,
                  lambda req: audit_case(world, req, gateway, settings.max_turns, rules=rules,
                                                     use_skeptic=settings.use_skeptic),
                  store, usage, parallel=settings.parallel, resume=not args.no_resume)

    pred = {}
    for cid in list(samples) + [c["case_id"] for c in CUSTOM_CASES]:
        res = store.load(cid)
        if res:
            pred[cid] = (res["deletion_status"], res["steps"])
    splits = {"all": score(gold, pred),
              "tune": score({c: gold[c] for c in TUNE}, pred),
              "holdout": score({c: gold[c] for c in HOLDOUT}, pred)}
    custom_results = []
    for c in CUSTOM_CASES:
        res = store.load(c["case_id"])
        if res is None:
            custom_results.append((c["case_id"], c["purpose"], False, "not run"))
            continue
        ok, detail = check_custom(c, gold[c["base_case"]], res, store.load(c["base_case"]))
        custom_results.append((c["case_id"], c["purpose"], ok, detail))
    usage_rows = _load_usage(run_dir / "usage.jsonl")
    # Configuration as recorded when the run happened; runs before run_info.json existed fall back to settings.
    recorded = load_run_info(run_dir) or {"model": f"{settings.backend}/{settings.model}",
                                          "max_turns": settings.max_turns, "skeptic": settings.use_skeptic,
                                          "note": "configuration not recorded by this run; current settings shown"}
    run_info = {"run": args.run_name, **recorded, "cases scored": len(pred),
                "model calls logged": len(usage_rows),
                "cost of this evaluation run (USD)": f"{sum(r.get('cost_usd') or 0 for r in usage_rows):.4f}"}
    notes_path = EVAL_DIR / "report_notes.md"
    notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
    (EVAL_DIR / "evaluation_report.md").write_text(
        render_evaluation_report(splits, custom_results, run_info, notes), encoding="utf-8")
    for name, m in splits.items():
        print(f"{name:8} status={_pct(m['status_accuracy'])} stepF1={_pct(m['step_f1'])} "
              f"seq={_pct(m['sequence_accuracy'])} evidence={_pct(m['field_accuracy']['evidence_id'])}")
    print("custom:", sum(ok for _, _, ok, _ in custom_results), "/", len(custom_results), "passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
