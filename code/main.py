"""Ghost Data Still Exists - deletion-audit agent.

Usage (from the repository root):
    python code/main.py                      # audit all cases in dataset/deletion_requests.csv -> output.csv
    python code/main.py --limit 5            # first 5 cases only (smoke run)
    python code/main.py --cases case_33 case_40
    python code/main.py --no-resume          # re-run cases that already have a saved result
    python code/main.py --assemble-only      # rebuild output.csv from saved per-case results

Per-case results are saved under code/.cache/runs/<run>/cases/ so an interrupted run resumes where it stopped.
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATASET_DIR, REPO_ROOT, get_settings  # noqa: E402
from llm.factory import build_gateway  # noqa: E402
from llm.usage import RecordingGateway, UsageLog  # noqa: E402
from pipeline import audit_case, build_world  # noqa: E402
from runner import ResultStore, assemble_output, run_cases, save_run_info  # noqa: E402
from validator import load_rules  # noqa: E402


def read_requests(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--requests", default=str(DATASET_DIR / "deletion_requests.csv"))
    ap.add_argument("--out", default=str(REPO_ROOT / "output.csv"))
    ap.add_argument("--run-name", default="final")
    ap.add_argument("--cases", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--parallel", type=int)
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--assemble-only", action="store_true")
    args = ap.parse_args(argv)

    settings = get_settings()
    requests = read_requests(args.requests)
    run_dir = settings.cache_dir / "runs" / args.run_name
    store = ResultStore(run_dir / "cases")
    selected = [r for r in requests if not args.cases or r["case_id"] in set(args.cases)]
    if args.limit:
        selected = selected[:args.limit]

    if not args.assemble_only:
        print(f"model={settings.backend}/{settings.model} fallback={settings.fallback_backend}/"
              f"{settings.fallback_model} max_turns={settings.max_turns}")
        print("building evidence world ...")
        world = build_world()
        rules = load_rules(DATASET_DIR)
        usage = UsageLog(run_dir / "usage.jsonl", prices=settings.prices())
        gateway = RecordingGateway(build_gateway(settings), usage, purpose="audit")  # logs every paid call
        save_run_info(run_dir, settings)
        run_cases(selected, lambda req: audit_case(world, req, gateway, settings.max_turns, rules=rules,
                                                     use_skeptic=settings.use_skeptic),
                  store, usage, parallel=args.parallel or settings.parallel, resume=not args.no_resume)

    summary = assemble_output([r["case_id"] for r in requests], store, args.out)
    print(f"wrote {args.out}: {summary['rows']} rows; missing={len(summary['missing'])} "
          f"failed={len(summary['failed'])}")
    if summary["failed"]:
        print("failed cases (re-run with resume):", " ".join(summary["failed"][:30]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
