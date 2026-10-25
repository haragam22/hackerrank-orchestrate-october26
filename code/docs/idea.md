# Idea — Ghost Data Still Exists deletion-audit agent

## Problem in one paragraph
Northstar Shop's deletion workflow reports "Complete" while personal data survives in replicas,
logs, support attachments, exports, search indexes, analytics, archives and backups — and sync or
restore jobs can recreate it. For each of 518 requests in `dataset/deletion_requests.csv` we must
decide `COMPLETE` / `INCOMPLETE` / `NOT_APPLICABLE` and emit an ordered `steps_json`
(findings → remediation → verification) that is exactly right per resource, operation and cited
evidence, using only evidence available at or before the case's `audit_at`.

## Our approach in one paragraph
Code turns every evidence file into a time-stamped, provenance-tagged **ledger** and builds a
**hard-identifier identity graph**. Then, for every case, an **LLM agent** investigates through tools
(identity, candidate resources, timelines, raw evidence, rules), and **makes the final decision**:
intent, which resources are in scope, their state at `audit_at`, and the treatment each needs.
A **skeptic agent** challenges the verdict, a **validator** checks every claim against the ledger
and bounces errors back to the agent, and a deterministic **compiler** renders the canonical
ordered `steps_json`.

## Why this shape (lessons from previous judge reviews)
| Previous feedback | What we do |
|---|---|
| Deterministic pipeline with no agent on the scoring path | The agent's verdict is what becomes `output.csv`; code only gathers facts and enforces format. |
| Model outputs drifted / not enforced | JSON schema on every model reply, retry on malformed, bounded turns, validator feedback loop. |
| One missed first-class constraint cascaded downstream | The verdict is computed first (intent, identity, per-resource state); status and steps are derived only from it. |
| Messy inputs undefined | Explicit behaviour for unknown email, conflicting evidence, post-`audit_at` evidence, unreadable images, weak identity links. |
| Accuracy claims not backed by measurement | Holdout evaluation (20 tune / 12 holdout samples) plus custom cases; report only what was measured. |
| Docs/tests didn't match code | Docs updated in the same change as code; tests exercise real behaviour. |
| Transcript lacked real back-and-forth | Every turn logged to root `log.txt` per AGENTS.md. |

## Goals
- High case-status and step-level accuracy across the whole dataset, not just samples.
- Agent on the critical path, with guardrails that make its output safe.
- Fully runnable from the terminal; resumable; reproducible as far as an LLM allows.
- Honest evaluation and usage reports.

## Non-goals
- No UI. No training/fine-tuning. No use of organizer-only files.
- No perfect generality beyond this dataset's evidence formats.

## Anti-goals (failure even if it "works")
- Hardcoded answers, case-family shortcuts or rules copied from sample outputs.
- Faking client headers or bypassing any provider's access control.
- Citing evidence captured after `audit_at`.
- Including resources linked only by weak identifiers (e.g. name match).
- Secrets in code, logs, or `code.zip`.

## Models (decided)
- Primary: **AgentRouter `gpt-6.1-sol`**, called through the official Claude Code CLI in headless mode
  (`claude -p --bare --tools ""`), with our own JSON-action tool protocol.
- Fallback: local Ollama `qwen3.5:9b-q4_K_M` behind the same interface.
- Screenshots: offline OCR → LLM structuring. (moondream tested and rejected.)
