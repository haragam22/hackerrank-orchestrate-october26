# Status — resume point

**Date:** 2026-10-26
**Phase:** 0–7 DONE; 8 gate items all verified (2026-10-26) — phase-reviewer not run for Phase 8 (user closed reviews). Full run finished; `output.csv` passes `check_output.py` (518 rows: COMPLETE 152,
INCOMPLETE 356, NOT_APPLICABLE 10); `code/evaluation/usage_report.md` from the run ($49.82, 2,062 calls, 0 fallback);
`code.zip` built by `code/package.py` (77 files, scanned clean). **Remaining: hand-in by the user** (step 10).
Test suite: `python -m pytest code/tests -q` → 478 passed, 0 skipped. Sim round 4 PASS (re-run after final fixes).

## Final run facts (for the interview)
- Run `final`: 518 cases, ~5.4 h, median 48 s/call, 6 parallel. Then: retry pass of 9 flagged cases (cache replay,
  $0, no change); screenshot/PDF truncation bug fixed and 10 affected cases re-run; empty-verdict fallback added
  (case_55, case_61 rebuilt from cache).
- 9 rows still flagged `ok=false` (review, rows are valid): case_35 (omitted record still shown unremediated),
  case_55/61 (fallback findings), case_526–529/531 (primary account with only activity-row evidence),
  case_538 (correct marketing opt-out questioned by the deletion-wording check).
- Logs: `code/.cache/final_run.log`, `final_retry.log`, `final_ocrfix.log` (not packaged).

## Security / reviews — CLOSED (user decision 2026-10-26: no further review rounds)
- 4 security-breaker passes; every critical/high finding fixed with regression tests
  (`test_security_fixes.py`, `_round2`, `_round3`, `_round4`). Accepted residuals: decisions.md 2026-10-26 round 4.
- After the last fixes: tests 474 passed / 0 skipped; eval_v5 replay unchanged; attack matrix 0 trusted COMPLETE;
  sim round 4 PASS.

## Final configuration (frozen)
- Model: AgentRouter `gpt-6.1-sol` via `claude -p` (fallback Ollama qwen3.5:9b, always flagged). $1 / $5 per 1M tokens.
- `AUDIT_MAX_TURNS=3`, `AUDIT_SKEPTIC=1`, `AUDIT_PARALLEL=6`; strict audit_at time gate (user decision).
- Latency last measured 2026-10-26 ~00:45: 9–12 s per call (normal).

## Evaluation history (details: code/evaluation/report_notes.md; report: code/evaluation/evaluation_report.md)
| Run | Code | All 32: status / step F1 / seq | Holdout status / F1 | Custom | Cost |
|---|---|---|---|---|---|
| eval_v1 | first agent | 93.8% / 79.4% / 34.4% | 91.7% / 77.1% | 3/6 | $2.82 |
| eval_v2 | activity-proof + workflow-op fixes | 100% / 90.5% / 43.8% | 100% / 90.6% | 6/6 | $2.98 |
| eval_v3 | + skeptic + status-match | 96.9% / 92.1% / 53.1% | 91.7% / 88.3% | 5/6 | $5.33 |
| eval_v5 | + skeptic fixes + security fixes (final) | **100% / 92.9% / 56.2%** | **100% / 93.1%** | 6/6 | $1.50 (90/143 cached) |
Later validator changes (final security pass + re-verification N1/N2) re-checked offline on the 32 eval_v5
verdicts: no status/step change, no new flags.

## Phase 8 — exact steps (user approves start; full run is paid, ~$73)
1. **Pre-run checks (free):** `python -m pytest code/tests -q` (all pass, 0 skipped);
   `python code/sim/run_sim.py --users 1000 --seed 2026` (PASS BAR MET); secret scan (key in no file but .env);
   `git check-ignore .env code/.cache code/sim/out`.
2. **Latency check:** `python code/llm/smoke.py` (expect ~10–20 s). If calls take minutes, tell the user before
   starting (full run could take 20+ h).
3. **Full run (paid):** `python code/main.py` (run name `final`, resumable). Re-run the same command to retry
   cases saved with `ok=false` (failed / EMPTY RESULT / UNSAFE / placeholder). Watch progress via
   `code/.cache/runs/final/cases/` count.
4. **Output check:** `python code/check_output.py` on root `output.csv` → must print ALL CHECKS PASSED
   (518 rows, no placeholder/empty COMPLETE/INCOMPLETE rows, evidence ids exist). Investigate any flagged rows
   (`code/.cache/runs/final/cases/<id>.json` flags).
5. **Usage report:** `python code/evaluation/main.py --usage-run final` → `code/evaluation/usage_report.md`.
6. **Evaluation report:** keep eval_v5 as the scored run (`python code/evaluation/main.py --run-name eval_v5
   --score-only` regenerates it with `report_notes.md`); add a short "final run" note (cases, flags, cost).
7. **README.md (repo root):** setup (Python 3.14, `pip install -r code/requirements.txt`, Claude Code CLI,
   `.env` with `AGENTROUTER_API_KEY`, Windows OCR), run commands (main, evaluation, check_output, sim, package),
   architecture summary (ingest → identity/World → agent + tools → validator → skeptic → finalize → compiler),
   evaluation summary, honest limitations; replace the old zip command with `python code/package.py`.
8. **Package:** `python code/package.py` → `code.zip` (must print ok for README, main.py, both reports; exit 0);
   inspect the zip: no `.cache`, `sim/out`, `.env`, archives; secret scan of the zip.
9. **Docs + log:** tick Phase 8 gates in implementation.md, update STATUS.md, decisions/testing as needed, log.txt.
10. **Hand-in (user):** `output.csv`, `code.zip`, `log.txt` (chat transcript) at
    https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
    Commit only if the user asks (via /quiz).

## Interview prep (after hand-in)
- 2-min trace of one case (e.g. case_09) through every file; probe pack (time gate, link types, status-match,
  citation rule, skeptic gate history, security findings); only measured numbers (table above).

## Pending / risks
- AgentRouter latency varies (9 s – 20 min per call observed).
- CVE scan of dependencies not run (offline); versions pinned in `code/requirements.txt`.
- `.env` `AGENTROUTER_BASE_URL` typo (`/v1/reponses`) unused.

## Repo state
- Branch `main`; nothing committed (user's call, via /quiz). Uncommitted: `.gitignore`, `.claude/`, `CLAUDE.md`,
  `log.txt`, `code/`. `big.log` (security-probe leftover) deleted.
