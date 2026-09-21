# Local API

`open_tutor.server.create_app(data_dir=..., path=...)` creates the local FastAPI
application. It does not seed a subject. The default database is
`out/open-tutor.sqlite3`; tests should always pass a temporary `data_dir` or
`path`. Run a private deployment with an explicit loopback/Tailscale bind in the
deployment command; the application itself never selects `0.0.0.0`.

All routes are under `/api`, return JSON, and use FastAPI's `{ "detail":
"..." }` error shape. Subject identifiers are lower-case slugs (`[a-z0-9][a-z0-9-]{0,63}`);
thread and job identifiers are 32-character lower-case UUID hex values.

## Read routes

| Route | Result |
| --- | --- |
| `GET /health` | `{ok, model:{configured,base_url,model}, version}`. No secret is returned. |
| `GET /subjects` | `{subjects:[{subject,title,node_count,grounded_count,source_count,status,candidate}]}`. `candidate` is `null` or `{status,fingerprint,grounded_count,node_count,reasons}`; blocked/paused candidate-only subjects are included for review. |
| `GET /subjects/{subject}` | `{spec,report,decision,state,gates,due,candidate}`. Active data is returned in the first fields; when no active bundle exists, those fields describe the candidate so it remains reviewable. `candidate` is `null` or `{spec,report,decision,fingerprint}`. |
| `GET /threads?subject=` | Durable thread summaries. |
| `GET /threads/{id}` | Thread, ordered messages, and an active job if one is queued/running. |
| `GET /jobs/{id}` | Durable `{id,status,stage,error,result}`. `interrupted` and `model-error` are real statuses. |
| `GET /jobs/{id}/events` | Replayable SSE. `stage`, `answer`, `error`, and `done` events are read from SQLite; `Last-Event-ID` resumes after a sequence number. |
| `GET /research?query=` | Lazy source-adapter discovery. Missing adapters and adapter failures are returned in `errors`, never hidden. |
| `GET /subjects/{subject}/assessment?node_id=&kind=quiz|teach-back` | Issues and stores a versioned assessment item. The key is never returned. |
| `GET /settings` | Non-secret provider mode/configuration only. |

An answer SSE event is emitted only after the complete `engine.tutor` result has
passed its deterministic answer gate and the answer/message has been committed.
An unverified result remains explicitly marked by the existing engine contract;
it is never relabelled as grounded.

## Write routes

| Route | Result |
| --- | --- |
| `POST /threads` `{subject,title?}` | Creates a durable thread for an imported active subject. |
| `POST /threads/{id}/ask` `{question,node_id?,followup_context?}` | Persists the user message, queues a worker, and returns `{job_id}`. `node_id` pins resolution to a validated node; `followup_context` is bounded data passed to drafting only. The worker continues independently of an SSE/browser connection. Asking a question does not update mastery. |
| `POST /design` `{topic,level?,depth?,review?,sources?}` | Runs the existing designer/pipeline in a durable worker. A verified non-review result becomes active; a review/failed result remains a separate candidate. Unsupported integrations fail visibly. |
| `POST /subjects/{subject}/review` `{action: approve\|reject, fingerprint?}` | Approves only a current, hash-matching, independently report-validated candidate. Approval cannot override a failed report. Reject removes only the candidate. |
| `PUT /subjects/{subject}/candidate` `{spec}` | Queues deterministic verifier re-evaluation of an edited candidate. A failure never replaces the active curriculum. |
| `POST /subjects/{subject}/assessment` `{item_id,response}` | Grades against the server-issued item and deterministic assessment module. `response` may be a string or `{text,citations:[{source_id,char_start,char_end,text}]}`. Citation spans must exactly match measured cached corpus offsets; client `grounded`, score, mastery, and event fields are ignored. Result is `{grade,state}`. Accepted correct/incorrect/partial outcomes go through `events_from_assessment`; flagged/ambiguous/degraded outcomes do not award or schedule mastery. Repeated submissions for one item return the durable first result. |
| `PUT /settings` `{base_url?,model?,mode?}` | Sets `extractive` or `local-model`. Provider URLs must be loopback/private HTTP(S) IP endpoints (including Tailscale CGNAT); API keys are neither accepted nor persisted. |

When `OPEN_TUTOR_WRITE_TOKEN` is set (or `create_app(..., write_token=...)` is
used), every write requires `Authorization: Bearer ...`. Requests carrying a
cross-origin `Origin` or `Sec-Fetch-Site: cross-site` are rejected. A missing
`Origin` is allowed for local CLI/tests; browser same-origin requests still
need to match their request host.

## Durability and recovery

SQLite uses WAL mode, foreign keys, and immediate write transactions. Threads,
messages, jobs, job events, settings, assessment issuance, and each subject's
learner-state projection are separate durable tables. On process startup,
queued/running jobs from the prior process are marked `interrupted` and receive
a terminal event instead of hanging forever. Job completion commits the result
and assistant message before its SSE `answer`/`done` events.

At startup, filesystem bundles are imported only when the YAML parses, the
compiled receipt has `min_grounding_chars == 2500`, its fingerprint matches,
the report is structurally complete, every node is independently `grounded`,
and the verifier report is internally consistent. An unused `needs-render` or
dead corpus entry remains visible evidence but does not invalidate nodes
grounded by other sources. Candidate files are not treated as active subjects;
candidates are surfaced by the subject routes.

Runtime cache files are isolated at `out/cache/<subject>/<source-id>.txt`; the
engine never falls back to another subject's source-id file.
