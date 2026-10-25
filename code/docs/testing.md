# Testing

## Automated
- `python -m pytest code/tests -q` — unit tests (added per phase).
- `python code/evaluation/main.py` — sample + custom-case scoring (Phase 6).

The suite command is also in `.claude/test-command`. Unit tests fake only external boundaries
(the `claude` subprocess, Ollama HTTP, sleep); no network in tests.

## Evaluation (live model, costs money: ~$0.14/case)
- `python code/evaluation/main.py --run-name eval_vN` — 32 samples + 6 custom cases; prints status / step F1 /
  sequence / evidence accuracy for all, tune (20) and holdout (12) and writes `code/evaluation/evaluation_report.md`.
  Use a new run name per prompt/code change (results are cached per run name); `--score-only` re-scores.
- Custom cases live in `code/evaluation/custom_cases.py` (NOT_APPLICABLE variants, injection, other language,
  time gate).

## Output contract check (free, no model calls)
- `python code/check_output.py [path]` (default: root `output.csv`) — header, 518 rows one-to-one with
  `deletion_requests.csv`, numeric order, allowed statuses, valid JSON, step keys, phase/operation/state table,
  consecutive step_no, phase order, evidence rules for COMPLETE / INCOMPLETE / NOT_APPLICABLE. Exit 0 = pass.
- Dry assembly without touching the real output: `python code/main.py --assemble-only --run-name <run> --out <scratch>.csv`.

## Security regression tests
- `code/tests/test_security_fixes.py` — one or more tests per security finding F1–F14, incl. "validator raises no
  contradiction/system problem on any of the 32 gold answers".
- `code/tests/test_security_round2.py` — final pass H1–H3, M1–M2, L1–L3.
- `code/tests/test_security_round3.py` — re-verification N1–N2, L1-new–L5-new: attack matrix (all-compliant,
  omitted, renamed, same-type category swap) over the 22 INCOMPLETE samples must never give `ok=True COMPLETE`;
  no SAFETY OVERRIDE on any gold answer.
- `code/tests/test_security_round4.py` — re-check H1–H2, M1, L1–L3: deletion wording on all 550 requests (540 flagged,
  10 measured exceptions), free-text absence words cannot hide a present record, no UNSAFE/unresolved on any
  gold answer, line-break role markers, pathological check_output rows.
- Manual replay check after any validator change: re-finalize the stored eval_v5 verdicts
  (`code/.cache/runs/eval_v5/cases/*.json`) and confirm statuses/steps unchanged and no new UNSAFE/SAFETY flags.

## User simulation (offline, no paid calls)
- `python code/sim/run_sim.py [--users 1000 --seed 2026 --workers 8]` — ≥ 1,000 seeded users (70 % valid / 20 %
  edge / 10 % invalid, see `code/sim/users.md`) through the real pipeline with a local mock model (good / sloppy /
  hostile). Prints crashes, valid error rate, invalid-rejection rate, p95 latency, memory growth and
  `PASS BAR MET` (exit 0). Per-user results: `code/sim/out/results.jsonl` (git-ignored). Report:
  `code/docs/user-sim-report.md`.

## Packaging
- `python code/package.py [--out code.zip]` — README.md + code/ without `code/.cache`, `__pycache__`, `.env`,
  archives; prints whether the required report files are present. Dependencies: `pip install -r code/requirements.txt`.

## Manual checks
- Gateway smoke: `python code/llm/smoke.py` → prints validated JSON, backend, tokens, latency and
  `fallback_used`. Expect `backend=agentrouter model=gpt-6.1-sol fallback_used=False`.
  To test the fallback path: `AUDIT_BACKEND=ollama python code/llm/smoke.py` (local) or temporarily
  set `AUDIT_CLAUDE_BIN=missing` and check that a `FLAG: fallback used` line is printed.
- Output sanity: `output.csv` has 518 rows, sorted, every `steps_json` parses (Phase 7).

Add new commands and manual checks here as they are created.

## Pre-run dry run (before the one paid full run)
- `python code/main.py --assemble-only --run-name eval_v5 --requests dataset/sample_deletion_requests.csv --out <scratch>/output.csv`
  then `check_output.check(<scratch>/output.csv, dataset/sample_deletion_requests.csv)` → 0 problems.
- `evaluation.main.render_usage_report(...)` on `code/.cache/runs/eval_v5/usage.jsonl` into a scratch file (the
  `--usage-run` CLI writes the real report, so use it only for the final run).
- `python code/package.py --out <scratch>/code.zip` → inspect names, scan for the key and excluded paths.
- `python code/main.py --requests dataset/sample_deletion_requests.csv --cases case_03 case_09 --run-name prerun_check --out <scratch>.csv`.
- `code/tests/test_fallback.py` — empty deletion verdict → fallback findings (case_55 restore risk citing evidence_32,
  case_61 remaining attachment citing activity_4007), never for records owned by another user.
- `code/tests/test_tools.py::test_screenshot_and_pdf_text_is_not_cut_to_a_line_limit` — screenshot/PDF text reaches
  the agent in full.

