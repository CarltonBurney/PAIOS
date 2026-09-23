# PAIOS OCR ingestion — architecture/control package

Release **1.0.0**, prepared 2026-09-22. Status: **implementation contract candidate; not yet promoted to canonical storage**.

This is ChatGPT's portion of the shared build. It contains machine-readable contracts, interface stubs, architectural decisions, acceptance scenarios, and Claude implementation packets. It contains no decoder, OCR adapter, API server, or persistence implementation. Runtime integration has not been tested.

Claude can begin Packet 1 with [the issued eight contract inputs](handoff/PACKET-1-ISSUED-CONTRACT.md). The full PR #6 plan was reviewed at head 7cfce260660618449e6403bbdf94c4aa5cc6a19f; see [plan reconciliation](handoff/PLAN-RECONCILIATION.md).

Start with [architecture](docs/architecture.md), then [storage and consistency](docs/storage-and-consistency.md), [contract semantics](docs/contract-semantics.md), [API behavior](docs/api-behavior.md), and [Claude's handoff](handoff/START-HERE.md).

| Deliverable | Location |
|---|---|
| Canonical data definitions, including Registry, Audit, Index, normalized media, OCR, errors | `schemas/contracts.schema.json` and per-type wrappers |
| HTTP contract | `api/openapi.json` |
| Python interface-only protocols | `interfaces/contracts.py` |
| Relational constraints and repository semantics | `docs/persistence-model.md` |
| Acceptance and fault-injection plan | `acceptance/acceptance.md` |
| Valid fixtures and contract checks | `examples/`, `acceptance/validate_contracts.py` |
| Sequenced implementation packets | `handoff/` |
| Canonical promotion and configuration gates | `docs/promotion.md`, `config/deployment.template.json` |
| File checksums and validation evidence | `MANIFEST.json`, `validation-report.json` |

Obsidian vault + DGE Google Drive remain canonical truth. OneDrive is primary working media/project storage. Local files are a delivery copy and temporary execution/cache, not a new permanent authority. No remote location has been configured or modified by this package.

All definitions are strict except named extension/EXIF maps. Schema shape validation is necessary but insufficient: the cross-record invariants and acceptance criteria are also normative. The bundle's schema file is authoritative for field names; prose defines semantics. Inconsistencies must be resolved by ChatGPT before implementation diverges.

Run `python -m pip install -r acceptance/requirements.txt`, then `python acceptance/validate_contracts.py` from this package. The validator requires Python 3.10+ and jsonschema 4.x. Validation evidence covers contracts, not production behavior.
