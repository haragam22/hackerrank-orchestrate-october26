import json
import threading

from llm.gateway import Completion, Usage
from llm.usage import RecordingGateway, UsageLog, current_case
from pipeline import CaseResult
from runner import ResultStore, run_cases


class FakeGateway:
    backend, model = "agentrouter", "gpt-6.1-sol"

    def complete(self, system, messages):
        return Completion(text="{}", usage=Usage("agentrouter", "gpt-6.1-sol", 1000, 100, 5))


def _req(i):
    return {"case_id": f"case_{i}"}


def test_run_cases_resumes_records_usage_per_call_and_survives_exceptions(tmp_path):
    usage = UsageLog(tmp_path / "usage.jsonl", prices={"gpt-6.1-sol": (1.0, 5.0)})
    gw = RecordingGateway(FakeGateway(), usage, purpose="audit")
    store = ResultStore(tmp_path / "cases")
    store.save({"case_id": "case_1", "deletion_status": "COMPLETE", "steps": [], "ok": True, "flags": []})
    seen = []

    def audit(req):
        seen.append(req["case_id"])
        c = gw.complete("s", [{"role": "user", "content": req["case_id"]}])
        if req["case_id"] == "case_3":
            raise RuntimeError("boom")
        return CaseResult(req["case_id"], "COMPLETE", [], True, completions=[c])

    run_cases([_req(1), _req(2), _req(3)], audit, store, usage, parallel=2, log=lambda m: None)
    assert sorted(seen) == ["case_2", "case_3"]                    # case_1 resumed, not redone
    assert store.load("case_3")["ok"] is False and "EXCEPTION" in store.load("case_3")["flags"][0]
    rows = [json.loads(l) for l in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
    # every paid call is logged when it happens, even for the case that crashed afterwards
    assert sorted(r["case_id"] for r in rows) == ["case_2", "case_3"]
    assert all(r["cost_usd"] == 0.0015 for r in rows)


def test_done_ids_is_read_once_per_batch(tmp_path, monkeypatch):
    store = ResultStore(tmp_path / "cases")
    calls = []
    real = store.done_ids
    monkeypatch.setattr(store, "done_ids", lambda: calls.append(1) or real())
    run_cases([_req(i) for i in range(50)], lambda r: CaseResult(r["case_id"], "COMPLETE", [], True), store,
              UsageLog(tmp_path / "u.jsonl"), parallel=4, log=lambda m: None)
    assert len(calls) == 1


def test_current_case_is_thread_local():
    seen = {}

    def worker(name):
        token = current_case.set(name)
        seen[name] = current_case.get()
        current_case.reset(token)

    threads = [threading.Thread(target=worker, args=(f"c{i}",)) for i in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert seen == {f"c{i}": f"c{i}" for i in range(5)}
