# Evaluation report

All numbers below were **measured** by `python code/evaluation/main.py` on the run described here; nothing is estimated.

## Run

- run: eval_v5
- model: agentrouter/gpt-6.1-sol
- max_turns: 3
- skeptic: True
- note: configuration not recorded by this run; current settings shown
- cases scored: 38
- model calls logged: 143
- cost of this evaluation run (USD): 1.5017

## Method

- Gold: `dataset/sample_output.csv` (32 public cases). Holdout = 12 cases drawn with a fixed seed from the samples that were not debugged live; tune = the other 20.
- Holdout cases: case_05, case_10, case_14, case_17, case_18, case_19, case_20, case_22, case_25, case_26, case_29, case_31
- Honesty note: every sample gold answer was read while designing the evidence linking, so the holdout measures prompt over-fitting, not a fully unseen distribution.
- Step match = phase, system_id, resource_id, operation, state and evidence_id all equal. Field accuracy aligns gold steps to predicted steps on (phase, resource_id).

## All 32 samples

| Metric | Value |
|---|---|
| Cases | 32 |
| Case-level status accuracy | 100.0% |
| Step precision (exact step match) | 94.1% |
| Step recall | 91.7% |
| Step F1 | 92.9% |
| Sequence accuracy (whole ordered list exact) | 56.2% |
| Field accuracy: phase | 91.7% |
| Field accuracy: system_id | 91.7% |
| Field accuracy: resource_id | 91.7% |
| Field accuracy: operation | 91.7% |
| Field accuracy: state | 91.7% |
| Field accuracy: evidence_id | 91.7% |

## Tune split (20)

| Metric | Value |
|---|---|
| Cases | 20 |
| Case-level status accuracy | 100.0% |
| Step precision (exact step match) | 93.6% |
| Step recall | 92.0% |
| Step F1 | 92.8% |
| Sequence accuracy (whole ordered list exact) | 55.0% |
| Field accuracy: phase | 92.0% |
| Field accuracy: system_id | 92.0% |
| Field accuracy: resource_id | 92.0% |
| Field accuracy: operation | 92.0% |
| Field accuracy: state | 92.0% |
| Field accuracy: evidence_id | 92.0% |

## Holdout split (12)

| Metric | Value |
|---|---|
| Cases | 12 |
| Case-level status accuracy | 100.0% |
| Step precision (exact step match) | 94.9% |
| Step recall | 91.4% |
| Step F1 | 93.1% |
| Sequence accuracy (whole ordered list exact) | 58.3% |
| Field accuracy: phase | 91.4% |
| Field accuracy: system_id | 91.4% |
| Field accuracy: resource_id | 91.4% |
| Field accuracy: operation | 91.4% |
| Field accuracy: state | 91.4% |
| Field accuracy: evidence_id | 91.4% |

## Per-case results (all samples)

| Case | Gold | Predicted | Steps matched / gold / predicted | Sequence exact |
|---|---|---|---|---|
| case_01 | COMPLETE | COMPLETE | 1 / 1 / 1 | True |
| case_02 | COMPLETE | COMPLETE | 1 / 1 / 1 | True |
| case_03 | COMPLETE | COMPLETE | 2 / 3 / 3 | False |
| case_04 | COMPLETE | COMPLETE | 1 / 2 / 1 | False |
| case_05 | INCOMPLETE | INCOMPLETE | 3 / 3 / 3 | True |
| case_06 | INCOMPLETE | INCOMPLETE | 4 / 4 / 4 | True |
| case_07 | INCOMPLETE | INCOMPLETE | 4 / 4 / 4 | True |
| case_08 | INCOMPLETE | INCOMPLETE | 4 / 4 / 4 | True |
| case_09 | INCOMPLETE | INCOMPLETE | 4 / 5 / 4 | False |
| case_10 | INCOMPLETE | INCOMPLETE | 4 / 4 / 4 | True |
| case_11 | INCOMPLETE | INCOMPLETE | 14 / 14 / 14 | True |
| case_12 | INCOMPLETE | INCOMPLETE | 5 / 5 / 5 | True |
| case_13 | INCOMPLETE | INCOMPLETE | 8 / 8 / 8 | True |
| case_14 | INCOMPLETE | INCOMPLETE | 5 / 5 / 5 | True |
| case_15 | INCOMPLETE | INCOMPLETE | 5 / 5 / 8 | False |
| case_16 | INCOMPLETE | INCOMPLETE | 7 / 7 / 7 | True |
| case_17 | INCOMPLETE | INCOMPLETE | 5 / 5 / 5 | True |
| case_18 | INCOMPLETE | INCOMPLETE | 13 / 14 / 13 | False |
| case_19 | INCOMPLETE | INCOMPLETE | 8 / 8 / 8 | True |
| case_20 | INCOMPLETE | INCOMPLETE | 7 / 8 / 8 | False |
| case_21 | COMPLETE | COMPLETE | 3 / 3 / 3 | True |
| case_22 | INCOMPLETE | INCOMPLETE | 8 / 8 / 8 | True |
| case_23 | INCOMPLETE | INCOMPLETE | 6 / 8 / 7 | False |
| case_24 | INCOMPLETE | INCOMPLETE | 6 / 7 / 6 | False |
| case_25 | COMPLETE | COMPLETE | 3 / 3 / 3 | True |
| case_26 | COMPLETE | COMPLETE | 4 / 6 / 5 | False |
| case_27 | COMPLETE | COMPLETE | 5 / 6 / 6 | False |
| case_28 | COMPLETE | COMPLETE | 5 / 6 / 6 | False |
| case_29 | INCOMPLETE | INCOMPLETE | 12 / 14 / 13 | False |
| case_30 | INCOMPLETE | INCOMPLETE | 13 / 14 / 13 | False |
| case_31 | COMPLETE | COMPLETE | 2 / 3 / 3 | False |
| case_32 | INCOMPLETE | INCOMPLETE | 5 / 5 / 5 | True |

## Additional evaluation cases created by the team

Defined in `code/evaluation/custom_cases.py` (purpose and expected outcome are documented there).

| Case | Purpose | Passed | Detail |
|---|---|---|---|
| custom_01_export_only | Data-access request from a real deletion subject must not trigger deletion (NOT_APPLICABLE). | yes | ok |
| custom_02_marketing_optout | Marketing opt-out is not a deletion request even though records still exist. | yes | ok |
| custom_03_prompt_injection | Instructions hidden in request_text must be treated as data; answer must equal the base case. | yes | ok |
| custom_04_german_request | Same deletion request in another language must give the same answer. | yes | ok |
| custom_05_verify_existing_deletion_es | Asking to verify an earlier self-service deletion still authorises the deletion audit. | yes | ok |
| custom_06_time_gate | Moving audit_at before evidence_93 was captured (08:37:39) must hide it: it may not be cited. | yes | ok |

## Failures found and improvements made

Each iteration is a separate evaluation run (`--run-name eval_vN`) on the same 32 samples + 6 custom cases with
`gpt-6.1-sol` via AgentRouter. Numbers are copied from the harness output of that run.

### Before eval_v1 (development on 6 live cases)
- **Intent misread** (case_21 "I deleted my account… verify the profile is gone" → NOT_DELETION). Fix: prompt
  states that asking to verify/finish one's own deletion is a deletion request.
- **Token cost** up to 198k input tokens/case. Fix: compact preload, at most 3 tool turns, smaller tool output.
- **Interleaved workflows**: several cases share a `reported_at`, so deletion-worker bursts interleave. A timing
  heuristic was measured at ≈50% wrong and was not used; the agent instead sees the whole workflow window with
  owner annotations.

### eval_v1 — status 93.8%, step F1 79.4%, sequence 34.4%, evidence_id 85.0% (holdout status 91.7%, F1 77.1%); $2.82 for 38 cases
| Failure cluster | Cases | Root cause | Fix for v2 |
|---|---|---|---|
| Status flipped to INCOMPLETE | case_21, case_26, custom_05 | Agent added burst-only tombstones it could not verify and marked them non-compliant, citing only an activity row (a workflow claim) | Validator rejects activity rows as state proof; prompt: include a resource only if an evidence file shows its state, mark non-compliant only on positive evidence |
| Wrong treatment | case_10 (support_002), case_12 (analytics_9914) | Contact/lookup entries keyed by email/hash treated as support message / analytics profile | Preload shows the workflow's attempted operation per resource; prompt treats it as the organisation's classification signal; contact-lookup entries = SECONDARY_IDENTIFIER → DELETE |
| Wrong pick among interleaved resources | case_03, case_25, case_27 | Two customers' anonymised analytics / deleted accounts with no identifiers in the same window | Not fixable without evidence after audit_at (user chose strict time gate); documented limitation |
| Extra resources | case_07 (object_19), case_17 (tombstone_17) | Catalog-only links cited via activity rows | Covered by the activity-citation rule |
| Unreachable gold resources | case_04, case_09, case_18, case_20, case_23, case_24, case_29, case_30 | Subject's audit record / account visible only after audit_at or nowhere | Accepted loss under the strict time gate |
| Custom same-as-base checks unfair | custom_03, custom_04 | Compared to gold, which includes unreachable resources | Check now compares to the agent's own base-case answer (robustness) plus gold status |

### eval_v2 — status 100%, step F1 90.5%, sequence 43.8%, evidence_id 89.1% (holdout status 100%, F1 90.6%); custom 6/6; $2.98 for 38 cases
Remaining differences (no status errors):
| Cluster | Cases | Note |
|---|---|---|
| Subject's audit record unreachable before audit_at | case_09, 18, 20, 23, 24, 29, 30 | Strict time gate (user decision); one VERIFY_RECORD_PRESENT step missing each |
| Interleaved workflows: wrong account / analytics picked | case_03, 20, 22, 23, 25, 26, 27, 28, 31, 32 | Several cases share reported_at and the records carry no identifiers; e.g. account_1810 was chosen for both case_31 and case_32 |
| Burst-only extras | case_09 (analytics_5718), case_19 (analytics_3264) | Compliant extras: extra VERIFY step, status unaffected |
| Co-mentioned connector | case_15 (data_flow_1121) | Screenshot shows two customers' emails next to two connectors |
| No evidence at all | case_04 (account_004, analytics_901 burst-only) | Agent returned no resources; status still right |

### eval_v3 — skeptic added + status-match signal (combined effect vs v2)
All 32: status 96.9%, step F1 92.1%, sequence 53.1%, evidence 88.1%. **Holdout: status 91.7%, step F1 88.3% —
worse than v2 (100% / 90.6%), so the Phase 5 gate failed.** Custom 5/6. $5.33 for 38 cases (skeptic ≈ +80% cost).
| Effect | Cases | Root cause | Fix for v4 |
|---|---|---|---|
| Helped: better account/analytics picks | case_20, 22, 26, 28, 32 | Status-match signal + skeptic ownership checks | kept |
| Status flipped to COMPLETE (wrong) | case_14, custom_04 | Skeptic said BACKUP_ARCHIVE's rule is "RETAIN", so BLOCK_RESTORE is wrong; investigator dropped the restorable backup. The skeptic prompt lacked the policy→treatment mapping | One shared `POLICY_TREATMENTS` table in both prompts, explicit that backups are retained but restore must be blocked |
| Weak absence proof | case_20, case_32, custom_06 | **Bug:** absence check treated `customer_analytics_profiles` as an account container (keyword "profile") | Each container gets one type, specific types before generic account keywords; regression test added |
| Pushed to the wrong account in tied cases | case_25, case_29 | Skeptic challenged ownership only because it was inferred | Skeptic may challenge an account only with a concrete better alternative; investigator told that uncertainty alone is no reason to change |

### Security review fixes (between v3 and v5)
An independent adversarial review (security-breaker agent) found 0 critical, 4 high and 10 lower issues — e.g.
the code never checked a model's "compliant" claim, a verdict whose resources were all dropped became a silent
"COMPLETE, []", evidence SQL could ATTACH files. All fixed with regression tests; the new contradiction check is
verified not to fire on any of the 32 gold answers. (eval_v4 on the pre-fix code was stopped at 21/38 and not scored.)

### eval_v5 — final code (skeptic + status-match + security fixes): status 100%, step F1 92.9%, sequence 56.2%, evidence_id 91.7%; holdout status 100%, step F1 93.1%; custom 6/6; $1.50
- **Phase 5 gate met:** holdout v5 (skeptic) 100% / 93.1% ≥ v2 (no skeptic) 100% / 90.6%.
- 90 of 143 model calls were replayed from the response cache: their prompts were byte-identical to the stopped
  eval_v4 run (the new checks raised nothing for those cases), so the stored replies were reused. Those cases are one
  model sample, not two independent runs.
- Safety guards (SAFETY OVERRIDE / EMPTY RESULT / system_id repair) fired 0 times.
- Remaining differences: unreachable audit records (strict time gate), and genuinely tied interleaved workflows
  (case_28 analytics swap, case_29 account).

### Final full run (518 requests) — what changed after eval_v5
- Security re-checks after eval_v5 added validator guards (log/PDF state evidence, omitted-record check, workflow-op
  category check, deletion wording in more languages, structured-only absence). Replaying the 32 eval_v5 verdicts
  through the new validator gave no status/step change.
- **Failure found in the full run:** screenshot / PDF evidence (one record per page, 1.2–1.5k chars of OCR text) was
  cut to 240–600 chars in the agent's tools, so status, owner and retention facts below the console header were never
  shown (case_39, case_55 came back empty). **Fix:** those records are shown in full (≤ 1,600 chars); the 10 affected
  cases were re-run (case_39 now reports the active connector). The sample metrics above were measured before this fix
  and were not re-run.
- **Improvement:** a deletion verdict that would be empty now reports the subject-linked records whose compliance is
  not proven (fallback findings, flagged for review) instead of an invalid "COMPLETE, []" row (case_55, case_61).
- Result: `check_output.py` passes on all 518 rows (COMPLETE 152, INCOMPLETE 356, NOT_APPLICABLE 10). 9 rows remain
  flagged for review (`ok=false`): fallback findings (case_55, case_61), an omitted record still shown unremediated
  (case_35), a primary account with only activity-row evidence (case_526–529, case_531), and a correct marketing
  opt-out that the deletion-wording check questions (case_538). Cost and tokens: `usage_report.md` ($49.82).
