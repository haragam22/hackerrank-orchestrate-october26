# User simulation — Ghost Data Still Exists deletion-audit agent — 2026-10-26
Users: 1,000 · seed 2026 · mix 70/20/10 (valid / edge / invalid) · harness `python code/sim/run_sim.py --users 1000 --seed 2026 --workers 8`

Each user = one generated deletion request audited end to end through the real pipeline (`pipeline.audit_case`:
evidence World, time-gated tools, agent loop, validator, skeptic, finalize, compiler) with a seeded **local mock
model** (good 70 % / sloppy 20 % / hostile 10 %); no paid model calls. Users and personas: `code/sim/users.md`.
The real-model run is the Phase 8 full run. Pass bar: `code/docs/decisions.md` (stated before the first run).

| Round | Crashes | Error rate (valid+edge) | Invalid rejected | p95 latency | Peak memory growth | Result |
|---|---|---|---|---|---|---|
| 1 (40-user smoke, seed 1) | 0 | 0 / 36 | 4 / 4 | 2.34 s | 2 MB | pass |
| 1 (1,000 users, seed 2026) | 0 | 0 / 905 | 95 / 95 | 1.67 s | 4 MB | **pass** |
| 2 (1,000 users, seed 2026, after final security fixes) | 0 | 0 / 905 | 95 / 95 | 1.23 s | 7 MB | **pass** |
| 3 (1,000 users, seed 2026, after re-verification fixes N1/N2) | 0 | 0 / 905 | 95 / 95 | 0.57 s | 7 MB | **pass** |
| 4 (1,000 users, seed 2026, after round-4 security fixes) | 0 | 0 / 905 | 95 / 95 | 1.10 s | 7 MB | **pass** |
Wall time 110 s with 8 workers; latency p50 0.60 s, max 22.0 s.

## What the run exercised (from per-user flags, `code/sim/out/results.jsonl`)
- Validator feedback to the agent: 1,250 problems raised (sloppy/hostile mock verdicts: bogus resources, wrong
  system_id/category, activity-row citations, forged "compliant").
- Skeptic: 161 challenges delivered; 109 malformed/schema-invalid skeptic replies failed open with a flag.
- `INVALID REQUEST` rejections: 95 (all invalid users, 0 model calls each).
- `EMPTY RESULT` flags: 95 (random people with no linked evidence, unknown emails) — flagged `ok=false`, not
  silently "COMPLETE, []".
- `SAFETY OVERRIDE`, citation and system_id repairs fired on hostile/sloppy verdicts; every resulting row still passed
  the output contract and cited only files captured at or before the user's `audit_at`.

## Failure clusters
None — no crashes and no invariant violations in round 1.
One product change was made while building the sim (before the measured run): requests are validated up front
(`pipeline.request_problems`), so malformed input is rejected cleanly instead of failing deep in the pipeline.
Tests: `test_invalid_requests_are_rejected_before_any_model_call`, `test_every_real_request_passes_input_validation`.

## Remaining risks
- The mock model is not the real model: accuracy is measured separately on the 32 samples (eval_v5) and the
  real-model behaviour on 518 cases is only observed in the Phase 8 full run.
- Worst-case latency 22 s (hostile users: a runaway SQL query is stopped at its 3 s deadline, plus thread
  contention); bounded, but slow users can occur when the model misbehaves.
- Real latency is dominated by AgentRouter (9 s–20 min per call observed), which the offline sim does not measure.
