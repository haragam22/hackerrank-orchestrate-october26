# Implementation plan

Hackathon mode: each phase has light gates (its own tests pass + a quick smoke check). The full
safety/quality check pass happens once, in Phase 8, before the full 518-case run.

Status values: `NOT STARTED` · `IN PROGRESS` · `DONE` · `BLOCKED`.

---

## Phase 0 — Model gateway
**Status:** DONE (2026-10-25)
**Prerequisites:** `.env` with `AGENTROUTER_API_KEY` (done, git-ignored); `claude` CLI installed (2.1.310); Ollama running.
**Work:** `code/llm/` gateways (Claude Code/AgentRouter, Ollama), JSON extraction + schema check, retry/backoff, response cache, usage log.
**Done when:**
- [x] One call to `gpt-6.1-sol` returns schema-valid JSON through the gateway. (`python code/llm/smoke.py`: 867 in / 53 out tokens, 17 s, fallback_used=False)
- [x] Malformed reply triggers a retry — `test_call_json_retries_malformed_then_succeeds_with_error_feedback`.
- [x] Usage record written per call; key never appears in logs or cache — `test_usage_log_appends_jsonl_with_flags`, `test_claude_gateway_parses_result_and_usage_and_keeps_key_out_of_args`; grep of code/, cache and log.txt for the key: 0 hits.
- [x] Fallback answers are flagged and never cached — `test_router_uses_fallback_after_retries_and_flags_it`, `test_cache_does_not_store_fallback_answers`.

## Phase 1 — Ingest & facts
**Status:** DONE (2026-10-25)
**Prerequisites:** none (OCR uses Windows built-in engine; no LLM needed).
**Work:** parsers for SQL, JSON, TXT/logs, PDF; Windows OCR for PNG with confusable-letter translation and exact-match verification; activity rows as records; SQL coverage for absence proof.
**Done when:**
- [x] All 94 manifest files ingest without crash; every record has provenance + captured_at — `test_all_manifest_files_load_with_provenance`, `test_every_file_parsed_including_screenshots`.
- [x] Spot check: `backup_4978` (evidence_93), `account_3960` (evidence_06), `support_001` (PDF evidence_58), handover `analytics_2034` — `test_known_facts_present`.
- [x] OCR reads `account_5762` / `support_9538` / `user_11` from evidence_04 — `test_screenshot_ids_verified_against_text_evidence`.

## Phase 2 — Identity graph, ledger, time gate, tools
**Status:** DONE (2026-10-25)
**Prerequisites:** Phase 1.
**Work:** `people.py` (identity index), `world.py` (ownership/trace links, workflow bursts), `case_context.py`
(time-gated candidates, timeline, absence), `tools.py` (agent tools, read-only, time-gated).
**Done when:**
- [x] All 518 request emails resolve (418 current / 100 historical) — `test_all_eval_emails_resolve_current_or_historical`.
- [x] Name-only matches never become candidates — `test_name_only_match_is_never_a_candidate`.
- [x] Tools never return evidence after `audit_at` — `test_hard_link_and_time_gate`, `test_read_evidence_refuses_files_after_audit_at`, `test_query_snapshot_is_read_only_and_time_gated`.
- [x] Sample candidate recall measured: **103/111 = 92.8%** (gate ≥ 85%) — `test_sample_candidate_recall_is_measured`.
  *Gate changed from "every gold resource":* 8 gold resources are unreachable under the strict time gate
  (6 subject audit records visible only after `audit_at` or nowhere, `audit_record_4902` only in a later log,
  `account_004` in no evidence). Precision: 217 candidates for 111 gold; only 6 extras carry an identity link,
  108 are burst-only (agent decides).

## Phase 3 — Investigator agent
**Status:** DONE (2026-10-25) — loop, schema, prompt, tools (`code/agent/`, `tools.py`); unit tests `test_agent_loop.py`.
**Prerequisites:** Phases 0, 2.
**Work:** system prompt, tool dispatcher, JSON action loop, verdict schema, turn cap.
**Done when:**
- [x] Agent finishes all 32 samples within the turn cap with schema-valid verdicts — eval_v2: 32/32 ok, max 3 turns, 0 unresolved validator problems; unit `test_turn_cap_forces_finish`, `test_malformed_reply_is_retried_by_schema_layer`.
- [x] Injection test case does not change the verdict — eval_v2 `custom_03_prompt_injection` passed (same steps as the agent's case_09 answer, gold status).

## Phase 4 — Validator & compiler
**Status:** DONE (2026-10-25) — compiler, validator, feedback loop, citation rule.
**Prerequisites:** Phase 3.
**Work:** claim checks, feedback loop (≤2 rounds), citation rule, step rendering and ordering, CSV writer.
**Done when:**
- [x] Compiler reproduces all 32 sample `steps_json` exactly when fed the gold verdicts — `test_compiler_reproduces_gold_from_ideal_verdict` (32 params).
- [x] Validator rejects a fabricated resource id and a post-`audit_at` citation — `test_unknown_unlinked_and_duplicate_resources_are_reported`, `test_evidence_after_audit_or_not_mentioning_resource_is_reported`, `test_validator_feedback_then_finalize_drops_unsupported_resource`.
- [x] Citation rule chosen and recorded in `decisions.md` — eval_v2: 0 citation mismatches on steps matching gold on (phase, resource, operation); evidence field loss comes only from missing/extra steps.

## Phase 5 — Skeptic
**Status:** DONE (2026-10-26)
**Prerequisites:** Phase 4.
**Work:** skeptic prompt + schema (`agent/skeptic.py`), one review round after validation, investigator keeps final say.
**Done when:**
- [x] Skeptic runs on samples; holdout accuracy with skeptic ≥ without — eval_v5 (skeptic, final code) holdout
  status 100% / step F1 93.1% vs eval_v2 (no skeptic) 100% / 90.6%. eval_v3 failed the gate (91.7% / 88.3%);
  causes fixed (shared policy table, absence-container bug, concrete-reason rule). v2→v5 also includes the
  status-match signal and security fixes (combined effect, user decision to use v2 as baseline).
  Unit: `test_skeptic.py`, `test_review_runs_once_after_valid_finish_and_investigator_keeps_final_say`,
  `test_skeptic_challenge_triggers_revision_and_is_flagged`.

## Phase 6 — Evaluation harness
**Status:** DONE (2026-10-25) — eval_v1–v5 run live; report written; cost measured ($0.078/case without skeptic, ~$0.14/case with skeptic → ~$73 for 518).
**Prerequisites:** Phase 4.
**Work:** metrics (status, step, field, sequence), 20/12 split, 6 custom cases (export-only, marketing opt-out, injection, German, Spanish verify-deletion, time gate) in `code/evaluation/custom_cases.py`.
**Done when:**
- [x] `python code/evaluation/main.py` prints metrics and writes `code/evaluation/evaluation_report.md` (unit: `test_reports_render_measured_numbers`; live: eval_v1, eval_v2).
- [x] Real cost per case measured on samples: $2.98 / 38 cases without skeptic (~$41 for 518), $5.33 / 38 with skeptic (eval_v3, ~$0.14/case → ~$73 for 518); the with-skeptic projection is in `STATUS.md`.

## Phase 7 — Runner
**Status:** DONE (2026-10-25) — `code/main.py` + `runner.py`; resume verified live; output format verified. Full run is part of Phase 8 (user decision).
**Prerequisites:** Phases 4, 6.
**Work:** parallel execution, checkpoint/resume, output assembly, usage report.
**Done when:**
- [x] Killing a run mid-way and re-running resumes without redoing finished cases — live 2026-10-25
  (`python code/main.py --run-name resume_check --limit 3 --parallel 1`): run 1 saved case_33 (16:43:03) and was
  killed; the restarted run left case_33's file untouched and saved case_34 (17:03:40); stopped by the user after that
  because AgentRouter latency was ~15–20 min/case. Unit: `test_result_store_round_trip_and_resume_logic`, `test_run_cases_resumes_records_usage_per_call_and_survives_exceptions`, `test_done_ids_is_read_once_per_batch`.
- [x] `output.csv` format: 518 rows, sorted, valid JSON in every row, contract rules — 2026-10-25:
  `python code/main.py --assemble-only --out <scratch>` → `python code/check_output.py <scratch>`: all STRUCTURAL checks passed
  (header, 518 rows one-to-one, numeric order, statuses, JSON). Placeholder rows (`INCOMPLETE`, `[]`) are now
  reported by check_output (security fix F12) and are expected until the Phase 8 full run fills every case. `check_output.py` passes on the 32 gold sample rows
  (`test_sample_gold_passes_every_check`); unit `test_assemble_output_writes_every_case_sorted_with_placeholders`.

## Phase 8 — Pre-run checks, full run, packaging
**Status:** gate items verified (2026-10-26; phase-reviewer not run, user closed reviews) — full run, output check, reports and code.zip done; hand-in is the user's step.
**Prerequisites:** Phases 0–7 DONE; reviews done (phase-reviewer, user-sim, security passes) with no open
critical/high security findings (focused re-verification of the final-pass fixes pending at time of writing).
**Work:** pre-run checks, latency check, full 518-case run, output check, usage + evaluation reports, README,
code.zip via `code/package.py`, docs/log, hand-in.
**Done when:**
- [x] Full test suite green, 0 skipped (478 passed, 2026-10-26).
- [ ] Security pass: no secrets in repo/zip/log; `.env` ignored; read-only SQL tool cannot write; prompt-injection test passes.
  2026-10-25/26: two security-breaker passes; all findings fixed with regression tests
  (`test_security_fixes.py`, `test_security_round2.py`, `test_security_round3.py`). Repo secret scan clean and `.env`
  ignored (2026-10-26). Round 3/4 re-checks fixed (`test_security_round3.py`, `_round4.py`); reviews closed by the
  user. Final `code.zip` scanned: no key or 12+ char fragment, no `.env`/cache/dataset/log/output (2026-10-26).
- [x] User simulation pass bar still met (`python code/sim/run_sim.py`; round 3, 2026-10-26).
- [x] Holdout metrics recorded honestly in `code/evaluation/evaluation_report.md` (eval_v5; regenerated 2026-10-26).
- [x] Full 518-case run done; `code/evaluation/usage_report.md` from that run ($49.82, 2,062 calls).
- [x] `python code/check_output.py` passes on the real root `output.csv` (ALL CHECKS PASSED; 9 valid rows flagged for review).
- [x] README has setup + run instructions; `code.zip` built by `code/package.py` excludes `dataset/`, `.env`, caches, `sim/out`, venvs.
