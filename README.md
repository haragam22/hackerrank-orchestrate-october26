# Ghost Data Still Exists — deletion-audit agent

HackerRank Orchestrate (October 2026). For every request in `dataset/deletion_requests.csv` the agent decides
whether deletion was actually requested, reconstructs the person's current and historical identifiers, finds every
copy of their data in the evidence as it stood at `audit_at`, applies `deletion_rules.csv`, and writes
`COMPLETE` / `INCOMPLETE` / `NOT_APPLICABLE` with the ordered finding → remediation → verification steps to
`output.csv` at the repository root.

The full challenge contract is in `problem_statement.md`.

## Setup

Tested on Windows 11 with Python 3.14.

1. Install the Python dependencies (pinned):
   ```bash
   pip install -r code/requirements.txt
   ```
2. Install the Claude Code CLI (`claude`, tested with 2.1.310). It is used headless as the HTTP client for
   AgentRouter (`claude -p --bare --tools "" --max-turns 1`); no Claude Code tools or MCP servers are enabled.
3. Create `.env` in the repository root (never committed or packaged):
   ```text
   AGENTROUTER_API_KEY=<your key>
   ```
   The key is read from the environment only and passed to the `claude` subprocess through an environment
   variable; it is redacted from every error message and never logged.
4. OCR for screenshots uses the Windows built-in OCR engine through PowerShell (`code/ingest/winocr.ps1`); no
   extra install. OCR text is cached under `code/.cache/` after the first run.
5. Optional fallback: a local Ollama server with `qwen3.5:9b-q4_K_M`. It is only used when AgentRouter fails after
   retries; every such answer is flagged, never cached, and re-run on the next resume.

Settings come from environment variables (defaults shown): `AUDIT_MODEL=gpt-6.1-sol`, `AUDIT_MAX_TURNS=3`,
`AUDIT_SKEPTIC=1`, `AUDIT_PARALLEL=6`, `AUDIT_TIMEOUT_S=300`, `AUDIT_RETRIES=3`, `AUDIT_PRICE_IN_PER_M=1.0`,
`AUDIT_PRICE_OUT_PER_M=5.0` (see `code/config.py`).

## Run

All commands run from the repository root.

| Command | What it does |
|---|---|
| `python code/main.py` | Audit all 518 requests → root `output.csv`. Resumable: per-case results are saved in `code/.cache/runs/final/cases/`; re-running the same command skips finished cases and re-runs cases saved with `ok=false` (model replies are cached by prompt, so an unchanged prompt replays the same answer at no cost; `--no-resume --cases …` forces a case). |
| `python code/main.py --limit 5` / `--cases case_33 case_40` | Smoke run on a few cases. |
| `python code/main.py --assemble-only` | Rebuild `output.csv` from saved results without model calls. |
| `python code/check_output.py` | Check `output.csv` against the contract: 518 sorted rows, valid statuses/steps, phase order, evidence rules, evidence ids that exist. |
| `python code/evaluation/main.py --run-name <name>` | Run and score the 32 public samples + 6 custom cases → `code/evaluation/evaluation_report.md`. `--score-only` re-scores a saved run. |
| `python code/evaluation/main.py --usage-run final` | Write `code/evaluation/usage_report.md` from the full run's per-call usage log. |
| `python -m pytest code/tests -q` | Test suite (offline; no paid calls). |
| `python code/sim/run_sim.py --users 1000 --seed 2026` | 1,000-user simulation through the real pipeline with a local mock model. |
| `python code/package.py` | Build `code.zip` (this README + `code/`, without caches, `.env`, data or archives). |

## Architecture

```text
dataset/ ──► ingest (SQL via read-only sandbox, JSON, logs, PDF text, screenshot OCR) ──► evidence records
         ──► identity resolution (current + historical emails/phones/hashes, user_audit_log)
         ──► World: resource ↔ person links (hard ids, trace ids, workflow burst near reported_at)
per case ──► CaseContext (everything time-gated to audit_at: candidates, timelines, absence proofs)
         ──► agent loop (gpt-6.1-sol, JSON-action tool protocol, ≤ 3 turns) with tools over the context
         ──► validator (deterministic checks → problems back to the agent, ≤ 2 rounds)
         ──► skeptic (one adversarial review of the verdict) → revision → validator
         ──► finalize (flagged repairs / safety overrides) ──► compiler (deterministic step order) ──► output.csv
```

- **Model access:** AgentRouter `gpt-6.1-sol` through the Claude Code CLI. Native function calling is unreliable
  on this backend, so tool use is a JSON-action protocol: each turn the model picks a tool and arguments, Python
  runs it and returns the result. Every reply is schema-validated (pydantic) with retries.
- **Time gate (strict):** nothing captured after `audit_at` is used, not even for ownership.
- **Investigation:** the agent sees the case's candidate resources with their links, time-ordered evidence
  timelines, workflow events near `reported_at`, absence proofs (a complete snapshot of the same table without the
  row) and replica drift, and cites the evidence that proves each resource's state.
- **Validator:** every claim is checked against the evidence: resource linked to the subject, category allowed for
  the resource type, treatment = policy, rule id, system id, evidence exists and is captured ≤ `audit_at`, and the
  latest state evidence does not contradict a "compliant" claim. A contradicted claim is overridden to
  non-compliant (flagged). Dropped or omitted subject records that evidence still shows unremediated mark the case
  `ok=false` for review instead of passing silently. A deletion verdict that would end up empty instead reports
  the subject-linked records whose compliance is not proven (fallback findings, flagged for review), because
  COMPLETE needs proof for every copy.
- **Compiler:** deterministic: findings with evidence, remediation (disable flows and block restores before
  changing records) and verification with empty evidence for INCOMPLETE; evidence-backed verification only for
  COMPLETE; `[]` for NOT_APPLICABLE; steps in problem-statement table order.
- **Prompts:** `code/agent/prompts.py` (investigator, skeptic, shared policy table). Code layout and data flow:
  `code/docs/technical.md`, `code/docs/flow.md`; decisions with measurements: `code/docs/decisions.md`.

## Evaluation

`code/evaluation/evaluation_report.md` (final scored run `eval_v5`: all 32 public samples, and a seeded 12-case
holdout not used for prompt tuning; every sample answer was read while designing evidence linking, so the holdout
is not fully blind):

| | Status accuracy | Step F1 | Sequence accuracy |
|---|---|---|---|
| All 32 samples | 100% | 92.9% | 56.2% |
| Holdout (12) | 100% | 93.1% | 58.3% |

These sample scores were measured before the last fix of the full run (screenshot/PDF text reached the agent cut
to 240–600 characters; now shown in full, and the 10 affected cases were re-run) and were not re-measured.

Six custom evaluation cases pass 6/6: data-access request, marketing opt-out, instructions injected into
`request_text`, the same request in another language, verifying an earlier self-service deletion, and an
`audit_at` moved before an evidence capture (time gate). The report lists the
failures found in each run and the improvements made. Token usage and cost of the full 518-case run that produced
`output.csv`: `code/evaluation/usage_report.md`.

## Limitations

- 9 of the 518 rows are flagged for review (listed in `code/evaluation/evaluation_report.md`): their format is
  valid but the evidence is thin (e.g. a primary account seen only in a workflow activity row).
- Sequence accuracy (exact step list) is lower than step F1: most misses are one extra or missing step on a
  borderline resource (e.g. a co-mentioned record or a decoy flow).
- Results depend on one hosted model; AgentRouter latency varies (about 9 s per call when measured, much slower at
  times). The run is resumable for that reason.
- Screenshot text relies on Windows OCR (PowerShell); elsewhere screenshots without a cached OCR result are
  reported as unparsed files and their facts are not used.
- Cases flagged `ok=false` (validator safety flags, fallback model, failed calls) are listed by `main.py`; they are
  retried on resume and should be reviewed if they persist.
- The deletion-wording check that questions a NOT_DELETION answer covers the languages seen in the data (English,
  French, Spanish, Portuguese, German, Dutch, Japanese, Korean, Chinese, Russian); other languages rely on the model.
