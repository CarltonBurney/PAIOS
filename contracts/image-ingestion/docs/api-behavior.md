# API and error behavior

`api/openapi.json` is the machine-readable HTTP contract. v1 uses version-pinned connector references. Direct multipart/local upload transport is deferred; a future adapter may produce Source.connector=upload internally after durable OneDrive staging, but there is no v1 upload route. Clients must not supply arbitrary URLs, local paths or credentials.

POST /images/ingest requires a bearer identity and Idempotency-Key. Derive tenant from authenticated identity; authorize workspace and configured source root before fetching. Unknown foreign asset/job IDs return 404 without revealing existence. Normalize and validate fields before reservation. Same tenant/workspace/key plus equal request digest returns 202 with the same IDs and latest committed status; a different digest returns 409. Keep mappings at least as long as retained ingestion records; do not use a short expiry that can cause duplicate ingestion after delayed retries. A response lost after commit must replay cleanly. Never recycle IDs.

202 means the accepted triple exists durably, not that OCR succeeded. Location points to /ingestions/{id}; Retry-After gives the polling interval. Polling returns 200 for terminal failures with a structured error in the Job. GET /images/{id} returns the latest committed Registry, including accepted/failed assets. Requests rejected before durable acceptance create no asset; security/request telemetry may log the rejection without pretending it was ingested. Admitted source-not-found/version-changed/unsupported/corrupt files produce a failed asset triple.

Search `/images?workspace_id=...&q=...&tag=...&status=...` is workspace-scoped, q performs case-insensitive literal substring matching on title/search_text, tag is exact, status exact. Multiple predicates are AND; missing q matches all authorized rows. Default limit 25, maximum 100. Sort by updated_at descending then asset_id ascending; opaque cursor pins a snapshot and query/scope. Expired/tampered/cross-query cursor gives 400 INVALID_REQUEST. indexed_at is the UTC high-watermark of the snapshot, not a claim that every just-committed asset is searchable. The operational target is configurable maximum search lag, proposed 60 seconds; exceeding it is observable and triggers recovery. A consumer needing read-after-write uses direct GET. Reindexing uses committed index rows and applies only newer registry versions.

| Error code | HTTP before acceptance | Retry rule |
|---|---|---|
| INVALID_REQUEST | 400 or 422 for schema violations | Fix request |
| UNAUTHORIZED / FORBIDDEN / NOT_FOUND | 401 / 403 / 404 | Correct identity/scope/reference |
| IDEMPOTENCY_CONFLICT / VERSION_CONFLICT | 409 | Inspect conflicting operation; never blind overwrite |
| LIMIT_EXCEEDED | 413; 429 for admission rate limit | Smaller input/configuration change; rate limit uses Retry-After |
| SOURCE_CHANGED | Normally asynchronous | Resubmit with explicit new source version |
| SOURCE_UNAVAILABLE / OCR_UNAVAILABLE / STORAGE_UNAVAILABLE / PERSISTENCE_UNAVAILABLE | 503 if encountered before acceptance | Bounded retry with same IDs/key; backoff with jitter |
| UNSUPPORTED_MEDIA | 415 if known before admission; otherwise asynchronous | New decoder/revised input |
| DECODE_FAILED / ENCRYPTED_MEDIA / OCR_FAILED | Normally asynchronous | Deterministic failures are not retried blindly |
| INTEGRITY_FAILED / CONTRACT_MISMATCH | 503 or asynchronous failure | Stop affected processing, repair integrity/release |
| INTERNAL_ERROR | 500 | Sanitized message; retry only if explicitly marked retryable |

All errors use Error schema, safe message, trace ID and field-level reasons; no stack traces, secret URLs or extracted document text. Retryable means the same operation can be retried safely, not that it must succeed. Runtime retry policy must bound attempts, delays and deadlines. Once terminal state is durably committed, automatic retries stop. An outage while committing the terminal result stays pending recovery rather than fabricating a terminal Job.
