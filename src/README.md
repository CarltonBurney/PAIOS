# PAIOS — Reference Slice

> **Pre-specification code.** This package predates the PAIOS Master Build
> Specification and is expected to be superseded by it. See
> [`docs/PLANNING_ENGINE_BRIEFING.md`](../docs/PLANNING_ENGINE_BRIEFING.md) §3
> for what it is, what it is not, and which parts may be worth carrying forward.

A working vertical slice through the governance core: a request enters, is
classified, risk-assessed, policy-checked, routed, gated on human approval when
required, executed, and audited. It runs with no cloud dependency, which is what
makes the governance logic testable in CI.

## Run it

```bash
pip install -e '.[dev]'
pytest
```

144 tests, no Azure account required — the default provider is a deterministic
mock.

## Try it

```python
from paios import ControlPlane, Identity, Request, InMemoryAuditSink

sink = InMemoryAuditSink()
cp = ControlPlane(audit_sink=sink)

request = Request(
    content="summarize yesterday's deployment notes",
    identity=Identity(subject="alice@contoso.com", authenticated=True),
)
outcome = cp.handle(request)

print(outcome.classification.request_type)   # RequestType.TECHNICAL
print(outcome.risk.to_dict())                # {'risk_level': 'L1', 'risk_domains': []}
print(outcome.routing.disposition)           # Disposition.AUTO_EXECUTE
print(len(sink.events))                      # full audit trail
```

Ask it to do something sensitive and it stops:

```python
request = Request(
    content="grant admin permissions to the contractor account",
    identity=Identity(subject="alice@contoso.com", authenticated=True),
)
outcome = cp.handle(request)

print(outcome.risk.to_dict())
# {'risk_level': 'L4', 'risk_domains': ['security']}
print(outcome.routing.disposition)    # Disposition.ADMIN_ESCALATION
print(outcome.policy.matched)         # ('PAIOS-SEC-001', ...) in prod
print(outcome.delivered)              # False — no approval handler configured
```

## Connecting to Azure AI Foundry

Copy `.env.example` to `.env` and set:

```bash
PAIOS_PROVIDER=foundry
AZURE_AI_FOUNDRY_ENDPOINT=https://<your-project>.openai.azure.com
AZURE_AI_FOUNDRY_DEPLOYMENT=<your-deployment-name>
```

Then `az login`, and:

```bash
pip install -e '.[azure]'
```

Authentication uses `DefaultAzureCredential` — your `az login` session locally,
a managed identity in Azure. No key is read from source or from `.env`.

## Design properties worth keeping

- **Risk has two axes.** `risk_level` (L0–L4) is impact; `risk_domains` are
  kinds of concern. A request can be L3 in both security and compliance.
- **Risk escalates, never de-escalates.** The highest detector wins and domains
  accumulate; levels do not average out.
- **Policy narrows, never widens.** Allow-lists intersect and denials union, so
  adding a policy can only reduce what a caller may do.
- **Governance classification is one-way.** A model can refine an ambiguous
  request but can never reclassify a `governance_change` into something
  routine.
- **Deny by default.** No approval handler means high-risk requests time out
  rather than execute.
- **The model is replaceable.** Provider choice is configuration; nothing in
  the pipeline knows which model answered.
- **Registry activation is not authorization.** An `active` tool is available;
  whether this principal may call it is a separate decision.
- **Versions are immutable.** Changing governance-relevant configuration creates
  a new version, so audit can resolve exactly what ran.
- **The gateway is the boundary.** `permits_tool()` is a decision;
  `ExecutionGateway.execute()` is enforcement, and it re-validates independently.
- **Registry changes are governed, not just audited.** Lifecycle mutations go
  through the policy engine; production promotion and ownership transfer require
  approval, and a requester cannot approve their own change.
