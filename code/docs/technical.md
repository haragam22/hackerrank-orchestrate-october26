# Technical design

Language: Python 3.14 (installed). Entry point: `python code/main.py`.
Libraries already present: `pydantic`, `jsonschema`, `pypdf`/`pdfplumber`, `pytest`, stdlib `sqlite3`.
To add: one offline OCR library for PNG evidence (choice in Phase 1, recorded in `decisions.md`).

## 1. Components

```
dataset/ ─► [1 INGEST] ─► [2 IDENTITY GRAPH] ─► [3 LEDGER]  (code)
                                                   │
             per case ┌────────────────────────────┘
                      ▼
            [4 INVESTIGATOR AGENT] ◄─ JSON tool actions ─► tool layer (time-gated)
                      │ verdict (schema)
            [5 SKEPTIC AGENT] ── challenges ──► 4
                      │
            [6 VALIDATOR] ── errors, max 2 rounds ──► 4
                      │ accepted verdict
            [7 COMPILER] ─► output.csv row
            [8 RUNNER]  checkpoint · cache · usage · parallelism
            [9 EVAL]    sample holdout · custom cases · metrics
```

### 1.1 Ingest (`code/ingest/`)
- **SQL** (`dataset/evidence/databases/*.sql`): executed into in-memory SQLite per file. Known tables
  include `customer_profiles*`, `customer_data_*` (primary / read / regional / reporting / failover
  replicas), `support_records`, `customer_search_documents`, `customer_analytics_profiles`,
  `customer_exports`, `connector_subscriptions`, `backup_restore_entries`,
  `security_audit_records`, `snapshot_scope`.
- **JSON / TXT records**: parsed by code (snapshot bundles, service resource exports, export jobs,
  connector configs, audit ledger, handover text).
- **Logs**: formats seen include `key=value` lines, RFC5424 syslog with key=value payload, and
  JSON-per-line. Parsed by regex/JSON; lines not parsed are kept raw for the agent's `read_raw_evidence`.
  Note: log lines themselves may contain user emails (`lookup_key=`), which can be PII exposures.
- **PDF**: text layer via `pdfplumber` (all 4 PDFs have one); whole document = one record.
- **PNG**: Windows built-in OCR (`code/ingest/winocr.ps1`, cached by image hash). Confusable letters in id
  suffixes translated; id verified only on exact match with ids from text evidence, else `ocr_unverified_ids`.
  OCR text is given to the agent as-is (no LLM structuring).
- **Activity rows** (`system_activity.csv`) are records too (`file_id` = `activity_id`).
- **Coverage**: per SQL file `{table: [resource_ids], "__scope__": [systems]}` for absence proof.
- Output: `facts` table. Each fact: `fact_id, file_id|activity_id|audit_id, source_kind,
  captured_at, event_time, system_id, resource_id, table/line ref, fields{}, identifiers[], raw_snippet`.
- Cached under `code/.cache/` keyed by file hash.

### 1.2 Identity graph (`code/identity.py`) — code only
- Seed: request email (lowercased). Expand via `user_identities.csv` and `user_audit_log.csv`
  (old/new email, phone, email_hash). Verified: `email_hash == sha256(lower(email))` for 850/850 users.
- **Hard links only**: email (current/historical), email_hash, phone (normalized digits),
  user_id, resource-id links (`LINK_RESOURCE_IDENTITY`, rows that co-locate a hard identifier and a
  resource id). Each link stores its reason + source id.
- **Weak links** (name only, fuzzy) → `flags`, never candidates. (User decision.)
- Unknown email → case flagged; agent still classifies intent; no resources.
- Note: one person can have multiple cases (e.g. case_33 / case_52) → everything keyed by
  `(user_id, audit_at)`.

### 1.3 Ledger + time gate
- Ledger = facts joined to identity links → candidate resources for the case.
- **Time gate in the tool layer**: rows are visible only if `captured_at <= audit_at` (files) or
  `event_time <= audit_at` (activity/audit rows). The agent never sees later data.
- Per resource: full timeline (not only latest) so recreation after deletion is visible.

### 1.4 Agent protocol (`code/agent/`)
Each turn = one model call. The model must return exactly one JSON object:
```json
{"action": "call_tool", "tool": "<name>", "args": {...}}
{"action": "finish", "verdict": {...}}
```
Tools (Python functions, all time-gated, all return compact JSON):
| Tool | Returns |
|---|---|
| `get_request()` | case fields + request_text |
| `get_identity()` | identifiers, link reasons, flags |
| `list_candidate_resources()` | resource_id, system_id, inferred category, link reason |
| `resource_timeline(resource_id)` | ordered observations with evidence ids |
| `flow_status(resource_id)` | sync / connector / restore observations |
| `read_raw_evidence(evidence_id, ref)` | raw snippet(s) |
| `query_snapshot(file_id, sql)` | read-only SELECT on one SQL snapshot |
| `get_rules()` | `deletion_rules.csv` |

Verdict schema (pydantic + JSON schema):
```
intent: DELETION | NOT_DELETION, intent_reason
resources[]: resource_id, system_id, include(bool), data_category, rule_id,
             state_at_audit (PRESENT|ABSENT|REDACTED|ANONYMIZED|EXPOSED),
             required_treatment (DELETE|REDACT|ANONYMIZE|RETAIN|TOMBSTONE_REDACT),
             evidence_id, condition_quote
flows[]: resource_id, system_id, kind (DATA_FLOW|RESTORE), state (ACTIVE|DISABLED|BLOCKED), evidence_id
flags[]: free text
```
Guardrails: max 8 turns (configurable), malformed JSON → retry with error message (max 2),
evidence wrapped in `<evidence>` tags and declared data-not-instructions in the system prompt.

### 1.5 Skeptic
Separate prompt, same gateway. Input: verdict + raw snippets for each cited evidence. Output schema:
`challenges[]: {resource_id, claim, problem, suggested_check}`. Challenges are returned to the
investigator for one revision round; investigator keeps final say. Runs on every case in the
first version; may be limited to risky cases if cost requires (measured, then decided).

### 1.6 Validator (`code/validator.py`) — checks, then a flagged last-resort `finalize`
- `validate`: per resource — linked to the subject (candidate), category allowed for the resource type,
  treatment = policy for the category, rule_id matches, system_id, evidence exists / is a captured file /
  `<= audit_at` / mentions the resource, **state contradiction** (compliant=true while the latest state evidence
  shows it unremediated), and **category vs workflow operation** (treatment disagrees with the DELETE/REDACT/
  ANONYMIZE the deletion workflow attempted). Case level: exactly one primary account; NOT_DELETION questioned
  when the request has unnegated deletion wording (`deletion_wording`, several languages). Absence is read only
  from structured state fields, never free text. Problems go back to the agent (max 2 rounds).
- State evidence (`_state_records`): the resource's own SQL row / JSON record / pipe handover line / key=value
  log line (not lookup-query lines) / PDF or screenshot showing it next to the subject's identifiers.
- `finalize`: repairs citation and system_id; SAFETY OVERRIDE (compliant → false) on a contradiction; drops other
  bad resources. `UNSAFE` flag (case `ok=false`) when a dropped resource is identity-linked or still shown
  unremediated, or when an identity-linked candidate is missing from the verdict while evidence shows it
  unremediated. `unresolved:` case problems also set `ok=false`. Every change is flagged.

### 1.7 Compiler — deterministic
- NOT_DELETION → `NOT_APPLICABLE`, `[]`.
- COMPLETE → verification steps only, each citing evidence.
- INCOMPLETE → findings (with evidence) + remediation (empty evidence) + verification for every
  in-scope resource and flow (empty evidence), including already-compliant ones (observed in samples).
- Order: phase, then operation order of the problem-statement table, then `system_id`, then
  `resource_id` (32/32 samples follow this). Steps numbered from 1.

### 1.8 Model gateway (`code/llm/`)
- `ClaudeCodeGateway`: subprocess `claude -p --bare --disable-slash-commands --strict-mcp-config
  --tools "" --model gpt-6.1-sol --output-format json --max-turns 1`, env
  `ANTHROPIC_BASE_URL=https://agentrouter.org`, `ANTHROPIC_AUTH_TOKEN` from `AGENTROUTER_API_KEY`,
  `MAX_THINKING_TOKENS=0`. Prompt = system + serialized history.
- `OllamaGateway`: `http://localhost:11434/api/chat` with `format` = JSON schema.
- Common: response cache keyed by sha256(model + prompt), retry/backoff on 5xx/timeout, per-call
  usage record (model, input/output tokens, duration, cost estimate) → `code/.cache/usage.jsonl`.
- Config via env: `AUDIT_MODEL_BACKEND`, `AUDIT_MODEL`, `AUDIT_PARALLEL`, `AUDIT_MAX_TURNS`.

### 1.9 Runner
`python code/main.py [--cases ...] [--limit N] [--parallel K] [--resume]` → per-case result JSON in
`code/.cache/cases/`, then assembles root `output.csv` (518 rows, sorted, valid JSON).

### 1.10 Evaluation (`code/evaluation/`)
`python code/evaluation/main.py` → scores vs `sample_output.csv` (status, step, field, sequence
accuracy), split 20 tune / 12 holdout (fixed list), plus custom cases in
`code/evaluation/custom_cases/`. Writes `evaluation/evaluation_report.md` and
`evaluation/usage_report.md`.

## 2. Messy-input behaviour
| Situation | Behaviour |
|---|---|
| Email matches nobody | Intent still classified; no resources; flag. |
| Weak identity link only | Flagged, excluded. |
| Evidence after `audit_at` | Invisible to the agent. |
| Activity claims SUCCESS, evidence shows record | Evidence wins; agent sees both. |
| Unreadable image/PDF or unsnappable id | Fact dropped, flag recorded. |
| Model returns invalid JSON | Retry ≤2, then deterministic minimal fallback + flag. |
| Model unreachable | Retry with backoff; case left pending for `--resume`. |
| Prompt injection text in evidence/request | Treated as data per system prompt; custom test case covers it. |

## 3. Open questions (resolved during build, recorded in decisions.md)
- Exact citation tie-break (Phase 4, against samples).
- OCR library choice (Phase 1).
- Data-category mapping per table/system (Phase 1–2, from evidence content).
- Real AgentRouter cost per case (Phase 0/6 measurement).
