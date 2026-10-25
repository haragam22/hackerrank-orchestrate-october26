# Flow

Update this file whenever an entry point or data path changes.

## Entry points
| Command | Purpose |
|---|---|
| `python code/main.py` | Audit all cases in `dataset/deletion_requests.csv` → root `output.csv` (resumable; `--limit`, `--cases`, `--assemble-only`) |
| `python code/evaluation/main.py --run-name <n>` | Run agent on 32 samples + 6 custom cases, score → `code/evaluation/evaluation_report.md` |
| `python code/evaluation/main.py --usage-run final` | `code/evaluation/usage_report.md` from the full run's usage log |
| `python code/check_output.py [path]` | Validate an output.csv against the submission contract |
| `python code/package.py` | Build code.zip (excludes caches/secrets) |
| `python code/agent/debug_run.py case_21 ...` | Dev: verbose agent trace for chosen samples |
| `python code/llm/smoke.py` | Dev: one live model call |

## Per-case pipeline (implemented)
`runner.run_cases` (thread pool, resume from `code/.cache/runs/<run>/cases/*.json`) →
`pipeline.audit_case`: `CaseContext` → `agent.loop.investigate` (JSON tool loop; on finish `validator.validate`
problems go back to the agent, ≤ 2 rounds; then, once, `agent.skeptic.Skeptic` challenges the validated verdict
and the investigator re-examines with one more tool turn, validator again) → `validator.finalize` (drop unsupported resources / repair citations,
each flagged) → `compiler.compile_steps` → saved result (status, steps, flags, verdict, usage) →
`runner.assemble_output` writes root `output.csv` (all case ids, numeric order, placeholder for missing/failed).

## Model calls (implemented, Phase 0)
`call_json(gateway, system, messages, Schema)` → `CachedGateway` (disk, `code/.cache/llm/`) →
`FallbackGateway` (retry transient errors with backoff) → `ClaudeCodeGateway` (`claude -p` subprocess → AgentRouter)
or, on failure, `OllamaGateway` (local) with `fallback_used=True`. Each reply is JSON-extracted and validated
against the pydantic schema; invalid replies are retried with the error fed back (max 2 retries).
Usage per call → `UsageLog` jsonl.

## Evidence ingest (implemented, Phase 1)
`ingest.loader.load_evidence(DATASET_DIR, ocr=cached_windows_ocr(code/.cache/ocr))` →
for each manifest row: `.sql` → `parse_sql_file` (in-memory SQLite, rows + coverage), `.json` → `parse_json_file`
(dicts with `resource_id`, ancestor context inherited), `.log/.txt` → `parse_text_file` (lines with ids or identifiers;
kv / JSON / pipe / bracket / syslog formats), `.pdf` → `parse_pdf_file`; then `system_activity.csv` rows; then PNGs
via OCR (`ingest.ocr.parse_png_file`) using the id universe from all text records. Output: `EvidenceStore`.

## Case context and tools (implemented, Phase 2)
`People.from_dataset` (identifier → user_id, incl. history and sha256 of every email) →
`World(store, people, load_requests)` indexes ownership links per resource (`hard`, `co_mentioned`,
`trace`) and deletion-worker activity → `CaseContext(world, request)` gives time-gated `identity()`,
`candidates()` (+ `workflow_burst` within ±2 h of `reported_at`, excluding resources owned by others),
`timeline(rid)`, `absence(rid)` → `Toolbox(ctx, DATASET_DIR).call(name, args)` exposes them to the agent with
`read_evidence` and read-only `query_snapshot`.

## Data path (per run)
1. Ingest `dataset/evidence/**` via `evidence_files.csv` → records, re-parsed every run (only OCR text is
   cached, in `code/.cache/ocr/`); plus `system_activity.csv` rows (`activity`) and `user_audit_log.csv` rows
   (`audit`, readable/citable ids, not state proof).
2. Load identities + audit log → identity index (`people.py`) → `World` ownership/trace links.
3. For each case (parallel, resumable):
   1. Time-gated case context (identity, candidates, workflow window) for `(user, audit_at)`.
   2. Investigator agent loop: model JSON action → tool → result … → verdict.
   3. Validator checks → problems back to the agent (≤ 2 rounds).
   4. Skeptic (once, after the first verdict that passes validation) → challenges → investigator revises with one
      more tool turn → validator again.
   5. `finalize` (repair citation/system_id, SAFETY OVERRIDE on contradicted compliance, drop unsupported,
      UNSAFE for dropped/omitted subject records still shown unremediated → case `ok=false`; an empty deletion
      verdict gets fallback findings for unproven subject-linked records, `ok=false`) →
      compiler → result saved to `code/.cache/runs/<run>/cases/<case_id>.json`; every model call is logged when it
      returns (`llm.usage.RecordingGateway` → `code/.cache/runs/<run>/usage.jsonl`); run settings in `run_info.json`.
4. Assemble root `output.csv` (sorted by `case_id`), write usage log.
