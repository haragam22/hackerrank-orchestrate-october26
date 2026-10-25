# Simulated users — deletion-audit agent

Project type: ML pipeline with a paid model → per the user-sim reference, each simulated user is one request
audited end to end through the real pipeline (`pipeline.audit_case`: ingest/World, time-gated tools, agent loop,
validator, skeptic hook, finalize, compiler) with the **model mocked locally** (`sim/mock_model.py`). The real-model
run is the Phase 8 full run (manual). Seeded: same seed → same users and same mock behaviour.

## Who "uses" it
The operator runs `python code/main.py` on a batch of privacy requests. The "users" are the requests (people asking
for deletion, export, opt-out …) plus the model the pipeline talks to. Both are untrusted.

## Request mix (weights)
| Class | Weight | Journeys |
|---|---|---|
| Valid typical | 70 % | Real identity from `user_identities.csv`; current (80 %) or historical (20 %) email; timestamps copied from a real request; deletion wording in EN/ES/FR/DE/JA/PT/HI templates |
| Valid edge | 20 % | Export / opt-out wording (NOT_APPLICABLE expected), uppercase email, 20k-char text, emoji/unicode, prompt-injection text, audit_at before all evidence (2025-01-01), audit_at far in the future, unknown email |
| Invalid | 10 % | Malformed timestamps, empty / non-email email, unknown reported_status, empty case_id — must be rejected with an `INVALID REQUEST` flag and no model call |

## Mock model personas (per case, seeded)
| Persona | Weight | Behaviour |
|---|---|---|
| good | 70 % | One tool turn (timeline + absence of the first candidate), then a plausible verdict built from candidates |
| sloppy | 20 % | Malformed first reply, unknown tool, non-string args, then a verdict with a bogus resource, wrong system_id and wrong category; repeats it on every revision request |
| hostile | 10 % | Runaway / ATTACH SQL, read of post-audit evidence, then a verdict claiming everything compliant and citing activity rows |
Skeptic replies: 50 % no challenges, 30 % one challenge, 10 % malformed JSON, 10 % schema-invalid kind.

## Per-user checks (correctness invariants)
- No exception escapes `audit_case` (crash).
- Invalid input → `ok=False`, `INVALID REQUEST` flag, zero model calls.
- Every produced row passes the output contract (`check_output._row_problems`) unless it is a flagged non-ok row.
- Every cited evidence id exists and was captured at or before the request's `audit_at`.
- `NOT_APPLICABLE` ⇒ `[]`.
- Latency per user (offline, no network) and process memory across the run.
