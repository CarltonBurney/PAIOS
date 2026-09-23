# Claude implementation handoff

Build to package version 1.0.0. Read README and all normative docs before implementation. Do not implement alternative field names, local-only persistence, best-effort audit logging or independent canonical document edits. This package is the repository handoff; no separate message or PR comment has been sent to Claude.

Start with [Packet 1 issued contract](PACKET-1-ISSUED-CONTRACT.md). The plan in PR #6 was reviewed at commit 7cfce260660618449e6403bbdf94c4aa5cc6a19f. Preserve its numbering: 1 decode, 2 persistence, 3 OCR, 4 API. Packet 1 has no persistence/audit writes or final routing implementation. Packet 2 can use synthetic fixtures before OCR is available. Each packet is a bounded review unit. Initial coding can use mocks for unconfigured cloud roots/provider; label them mocks and do not call that end-to-end production acceptance.

## Packet 1 — decode, identity, metadata

Inputs: normalized media and metadata definitions, LocalPage/decoder/hash protocols, normalization semantics. Implement signature detector, deterministic registry, Pillow, HEIF and PDF adapters, SHA-256, advisory perceptual hash, original metadata extraction, limits and cache cleanup. Return local normalized pages to orchestrator; it persists StorageRefs. Required evidence: A09–A12, A22, fixture hashes, exact page geometry/orientation, malformed/oversized/encrypted behavior. OCR calls and API modules are not this packet.

## Packet 2 — persistence, storage and recovery

Inputs: CommitBundle/Registry/Audit/Index schemas, storage-and-consistency, persistence-model, repository/storage protocols. Implement version-pinned connector fetch, verified OneDrive writes, canonical DGE commit adapter, durable key reservation, writer fencing, three-record validation, read head, job projection, audit read and recovery/reindex. Propose coordinator/database technology and publication/reservation format before relying on cloud atomicity. Required evidence: acceptance A01–A08, A17–A21; crash injection at every commit boundary. Deliver adapters, migrations/config templates, test results and documented durability guarantees. Never add standalone mutable Audit update APIs.

## Packet 3 — OCR provider

Inputs: OCRResult, Error, OCRProvider, OCR status/geometry/reuse semantics. Select a configured provider profile and document model/version, supported languages, timeout, data handling and confidence mapping. Implement page success/failure normalization and deterministic full_text construction. Required evidence: A13–A16, provider failure fixtures and normalized results. Provider changes must not alter the shared schema. No invented confidence/languages/text on failures.

## Packet 4 — orchestration, API and search

Inputs: OpenAPI, all approved adapters, API behavior and acceptance matrix. Implement durable acceptance before 202, state transitions, source pinning, duplicate lookup/reuse, worker retries, terminal commit, polling, GET and scoped search. Required evidence: full acceptance matrix including cross-tenant checks, request replay after timeout and deletion of the local cache. Supply implementation commit, dependency versions, tests, cloud/mock distinction, outstanding risks and operations recovery instructions for ChatGPT review.

## Return packet format

Include: packet number; contract release; implementation commit; changed files/modules; configured vs mocked adapters; acceptance IDs and results; known limitations; requested contract changes; exact reproduction commands; canonical/working-storage receipts with secrets removed. ChatGPT reviews consistency and integration; Claude fixes implementation defects. Production approval requires cloud-backed recovery evidence, not only passing unit tests.

Claude must not edit approved Obsidian/DGE specifications directly. The authorized runtime service may append operational records to a separately configured DGE records root. Use distinct specification and record permissions. If runtime write authority is not configured, use mocks and report the deployment gate.
