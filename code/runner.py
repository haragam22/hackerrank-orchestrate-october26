"""Batch execution: per-case results on disk (resumable), parallel audit, output.csv assembly."""
import csv
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from llm.usage import current_case

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def case_sort_key(case_id):
    m = re.search(r"(\d+)$", case_id)
    return (int(m.group(1)) if m else 0, case_id)


class ResultStore:
    """One JSON file per case; written atomically so an interrupted run can resume."""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, case_id):
        """Case ids come from the input CSV: only plain names, always inside this folder (security pass H3)."""
        if not _SAFE_ID.fullmatch(str(case_id)):
            raise ValueError(f"unsafe case_id {str(case_id)[:40]!r}")
        path = (self.dir / f"{case_id}.json").resolve()
        if path.parent != self.dir.resolve():
            raise ValueError(f"case_id {case_id!r} resolves outside the results folder")
        return path

    def save(self, result):
        path = self._path(result["case_id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def load(self, case_id):
        try:
            path = self._path(case_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        # On a case-insensitive file system CASE_7.json also answers for case_7 (security pass L2-new).
        return data if data.get("case_id") == case_id else None

    def done_ids(self):
        done = set()
        for path in self.dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("ok"):
                done.add(data["case_id"])
        return done


def assemble_output(case_ids, store, out_path):
    """Write output.csv for every case id (sorted numerically); missing/failed cases get a placeholder row."""
    missing, failed = [], []
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["case_id", "deletion_status", "steps_json"])
        for cid in sorted(case_ids, key=case_sort_key):
            res = store.load(cid)
            if res is None:
                missing.append(cid)
                writer.writerow([cid, "INCOMPLETE", "[]"])
                continue
            if not res.get("ok"):
                failed.append(cid)
            writer.writerow([cid, res["deletion_status"],
                             json.dumps(res["steps"], ensure_ascii=False, separators=(",", ":"))])
    return {"rows": len(case_ids), "missing": missing, "failed": failed}


def save_run_info(run_dir, settings):
    """Record the configuration a run actually used, so later re-scoring reports it correctly."""
    info = {"model": f"{settings.backend}/{settings.model}", "max_turns": settings.max_turns,
            "skeptic": settings.use_skeptic, "fallback": f"{settings.fallback_backend}/{settings.fallback_model}"}
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    (Path(run_dir) / "run_info.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info


def load_run_info(run_dir):
    path = Path(run_dir) / "run_info.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def result_to_dict(case_result, usage_log):
    usage = [{"model": c.usage.model, "backend": c.usage.backend, "input_tokens": c.usage.input_tokens,
              "output_tokens": c.usage.output_tokens, "cached": c.cached, "fallback_used": c.fallback_used,
              "cost_usd": usage_log.cost(c.usage, c.cached)} for c in case_result.completions]
    return {"case_id": case_result.case_id, "deletion_status": case_result.deletion_status,
            "steps": case_result.steps, "ok": case_result.ok, "flags": case_result.flags,
            "fallback_used": case_result.fallback_used, "turns": case_result.turns,
            "validation_rounds": case_result.validation_rounds, "verdict": case_result.verdict, "usage": usage}


def run_cases(requests, audit_fn, store, usage_log, parallel=6, resume=True, log=print):
    """audit_fn(request) -> CaseResult. Saves each result as soon as it finishes.
    Usage is logged per call by llm.usage.RecordingGateway (wrap the gateway with it); this sets the case id."""
    done = store.done_ids() if resume else set()
    todo = [r for r in requests if r["case_id"] not in done]
    log(f"{len(requests) - len(todo)} cases already done; running {len(todo)} with parallel={parallel}")
    lock = threading.Lock()
    started = time.time()
    finished = 0

    def one(req):
        token = current_case.set(req["case_id"])
        try:
            res = audit_fn(req)
        except Exception as exc:  # one broken case must not stop the batch
            return {"case_id": req["case_id"], "deletion_status": "INCOMPLETE", "steps": [], "ok": False,
                    "flags": [f"EXCEPTION: {type(exc).__name__}: {exc}"[:400]], "usage": []}
        finally:
            current_case.reset(token)
        return result_to_dict(res, usage_log)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(one, r): r["case_id"] for r in todo}
        for fut in as_completed(futures):
            result = fut.result()
            try:
                store.save(result)
            except (ValueError, OSError) as exc:  # one unsavable case must not stop a paid batch
                log(f"case {str(result.get('case_id'))[:40]!r} not saved: {exc}")
                continue
            with lock:
                finished += 1
                log(f"[{finished}/{len(todo)} {time.time() - started:.0f}s] {result['case_id']} "
                    f"{result['deletion_status']} steps={len(result['steps'])} ok={result['ok']}"
                    + (f" flags={len(result['flags'])}" if result["flags"] else ""))
    return finished
