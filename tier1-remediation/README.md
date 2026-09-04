# Tier-1 IT Support Remediation Framework

A reference implementation for automating high-volume Tier-1 service desk scenarios across Microsoft 365 and the Power Platform, using Microsoft Teams (mobile) as the intake surface, Azure Logic Apps / Power Automate as the orchestration layer, and Dataverse as the centralized audit and telemetry store.

This framework is the operational counterpart to the governance model described in [`docs/MICROSOFT_365_INTEGRATION_STRATEGY.md`](../docs/MICROSOFT_365_INTEGRATION_STRATEGY.md). Where that document defines *what* must be governed, this directory demonstrates *how* a governed automation actually executes against live M365 services.

> **Status: reference implementation.** The workflow definition, architecture, and data model in this directory are complete and internally consistent, but they are written against placeholder tenant identifiers (`cr123_` table prefix, zeroed subscription and team GUIDs). They are intended to be deployed into your own environment after the substitutions described in [Deployment](#deployment). No performance figures in this document are measurements from a production tenant — the targets in [Operational Model](#operational-model) are design goals, not observed results.

---

## Table of Contents

- [Problem Statement](#problem-statement)
- [Architecture Overview](#architecture-overview)
- [Scenario 1 — Teams Mobile Password Reset](#scenario-1--teams-mobile-password-reset)
- [Scenario 2 — Automated Printer Diagnostics](#scenario-2--automated-printer-diagnostics)
- [Event-Driven Failure Logging](#event-driven-failure-logging)
- [Data Model](#data-model)
- [Operational Model](#operational-model)
- [Prerequisites](#prerequisites)
- [Deployment](#deployment)
- [Security Considerations](#security-considerations)
- [Repository Layout](#repository-layout)

---

## Problem Statement

Tier-1 service desk volume in most enterprises is dominated by a small number of repetitive, low-judgement request types. Two of them recur in nearly every M365 tenant:

1. **Password resets.** Highly repetitive, and frequently raised from a mobile device *precisely because* the user cannot sign in to a workstation to reach the self-service portal. The conventional SSPR web flow assumes access the user does not have at the moment of failure.
2. **Printer faults.** Diagnostically shallow but operationally noisy. Most tickets resolve to one of a handful of causes — a hung spooler, a saturated queue, or a driver mismatch — each of which has a deterministic remediation that does not require human reasoning.

Both scenarios share a structural property that makes them good automation candidates: **the diagnosis is mechanical, the remediation is deterministic, and the failure modes are enumerable.** What they lack in a typical environment is not intelligence but plumbing — an intake surface the user can reach, an authorization gate, and an audit trail that satisfies the people who own the identity system.

The design constraint this framework works under is that automation touching identity is only acceptable if it is *more* accountable than the human process it replaces, not less. Every branch in this workflow terminates in a durable log record, including the branches that refuse to act.

---

## Architecture Overview

The full diagram lives at [`diagrams/architecture.mmd`](diagrams/architecture.mmd) and renders in GitHub, [mermaid.live](https://mermaid.live), or VS Code with the Markdown Preview Mermaid extension.

The system decomposes into six layers:

| Layer | Component | Responsibility |
|---|---|---|
| 1 — Intake | Teams Mobile + Power Virtual Agent | Capture a structured request via Adaptive Card; no free-text triage |
| 2 — Identity | Microsoft Graph + Entra ID | Resolve and verify the caller; enforce eligibility before any action |
| 3 — Routing | Logic Apps `Switch` | Dispatch to a scenario handler on `requestType`; unknown types escalate |
| 4 — Remediation | Graph API / Azure Automation / Intune | Execute the deterministic fix for the classified fault |
| 5 — Exception | `Scope` with `runAfter` | Catch terminal and transient failures; classify and escalate |
| 6 — Telemetry | Dataverse → Power BI / App Insights | Durable audit record and operational reporting |

### Control flow

Intake is deliberately **structured rather than conversational**. The Adaptive Card emits a typed JSON payload against a fixed schema, which means the orchestration layer never performs natural-language interpretation of a support request. This removes an entire class of failure — misrouted automation acting on a misread request — and is what makes the `Switch` a safe dispatch mechanism rather than a guess.

The orchestration uses a **Try/Catch scope pattern**, which is the Logic Apps idiom for structured exception handling:

- `Try_Remediation` — a `Scope` containing identity resolution, the eligibility gate, and the scenario `Switch`.
- `Catch_Log_Exception` — a second `Scope` whose `runAfter` is configured for `["Failed", "TimedOut"]` on the first. It executes *only* when the try scope did not succeed.
- `Log_Successful_Remediation` — the success path, gated on `["Succeeded"]`.
- `Respond_To_Intake_Bot` — a `Response` action gated on `["Succeeded", "Failed", "Skipped"]` for **both** preceding branches, so exactly one HTTP response is returned regardless of which path ran.

That last detail matters. A `Response` action gated only on the success path leaves the caller hanging on every failure; gating it on `Skipped` as well is what makes the failure path observable to the user rather than silently swallowed.

---

## Scenario 1 — Teams Mobile Password Reset

### Flow

1. User opens the support bot in the Teams mobile client and selects **Reset my password**.
2. The bot issues a step-up authentication challenge. The result is recorded on the payload as `requester.mfaSatisfied`.
3. The workflow resolves the requester against Graph (`GET /users/{id}`) and confirms `accountEnabled` is `true`.
4. If — and only if — `mfaSatisfied` is `true`, the workflow calls:

   ```http
   POST https://graph.microsoft.com/v1.0/users/{id}/authentication/passwordMethods/28c10230-6103-485e-b985-444c60001490/resetPassword
   Content-Type: application/json

   { "requireChangeOnNextSignIn": true }
   ```

   `28c10230-6103-485e-b985-444c60001490` is the well-known identifier for a user's password authentication method.

5. The endpoint is **long-running**: it returns `202 Accepted` with a `Location` header rather than a completed result. The workflow polls that URL in an `Until` loop (5-second interval, 12-iteration / 5-minute ceiling) until `status` is no longer `running`.
6. A terminal status other than `succeeded` raises `PasswordResetNotConfirmed` and diverts to the catch scope.

### Design notes

**The MFA gate is a hard `Terminate`, not a warning.** If step-up authentication was not satisfied, the workflow fails with `StepUpAuthenticationRequired` and never reaches the Graph call. Absence of proof is treated as failure, not as something to log and proceed past — the negative branch is the one that carries the security weight.

**Polling is not optional.** The `202` response means the reset has been *accepted*, not *performed*. Treating the `202` as success is the most common way this integration is implemented incorrectly; the workflow would report a completed reset for an operation that later failed server-side.

**Credential material is marked non-loggable.** Both the reset request and the poll responses set `runtimeConfiguration.secureData` over `inputs` and `outputs`, which suppresses them from Logic Apps run history. Without this, temporary credentials are readable by anyone with reader access to the run history — a far wider audience than the identity administrators who are supposed to hold them. See [Security Considerations](#security-considerations).

---

## Scenario 2 — Automated Printer Diagnostics

### Flow

1. The workflow reads current print queue state via Graph (`GET /print/printers/{id}/jobs`).
2. `Classify_Printer_Fault` (a `Compose` action) derives a `faultClass` from queue depth and the reported symptom:

   | `faultClass` | Classification signal | Remediation |
   |---|---|---|
   | `queue_backlog` | Queue depth > 25 jobs | Purge stalled jobs |
   | `driver_mismatch` | Symptom text contains `driver` | Redeploy driver package via Intune |
   | `hardware_offline` | Symptom text contains `offline` | **Not auto-remediable** — escalate |
   | `spooler_hung` | Default | Restart the print spooler service |

3. A nested `Switch` on `faultClass` isolates `hardware_offline` — which terminates immediately with `HardwareFaultNotAutoRemediable` — from everything else, which routes to the Azure Automation runbook webhook.
4. **Post-remediation verification is mandatory.** The workflow re-reads printer state and requires `status.state == "idle"`. Anything else raises `PrinterVerificationFailed`.

### Design notes

**A completed runbook is not a fixed printer.** The verification step exists because runbook success only proves the script ran, not that the fault cleared. Reporting resolution on runbook exit code is how automation acquires a reputation for closing tickets that are still broken — the user reopens it, and trust in the automation degrades faster than the ticket deflection was ever worth.

**Hardware faults are explicitly modelled as un-automatable.** Encoding "this class of problem requires a human" as a first-class branch is what keeps the automation honest about its own boundaries. The alternative — attempting remediation and reporting a generic failure — loses the diagnostic information that the fault was *physical*, which is the single most useful thing to hand to the technician who picks it up.

**Classification is heuristic and deliberately simple.** Substring matching on reported symptoms is adequate for a fixed Adaptive Card vocabulary and has the significant advantage of being auditable by inspection. If the intake surface is ever opened to free text, this classifier is the first component that must be replaced.

---

## Event-Driven Failure Logging

Every failure path converges on `Catch_Log_Exception`, which performs four steps:

1. **`Collect_Failed_Action_Results`** — a `Query` action filtering `@result('Try_Remediation')` to entries where `status == 'Failed'`. The `result()` function returns the full execution record of every action inside the scope, which is what makes granular post-hoc diagnosis possible without instrumenting each action individually.
2. **`Set_Failure_Reason`** — extracts the first failed action's `error.message`, falling back to `'Unclassified remediation failure.'` via `coalesce`.
3. **`Log_Exception_To_Dataverse`** — writes the full failure record, including the serialized failed-action array.
4. **`Notify_Tier2_Support_Channel`** — posts to the Tier-2 Teams channel with ticket, scenario, requester, reason, and workflow run ID.

The Teams notification is gated on `["Succeeded", "Failed"]` of the Dataverse write. This is intentional: **a logging outage must not also suppress the human escalation.** If Dataverse is unavailable, the notification still fires, and the run ID in the message is sufficient to recover the detail from Logic Apps run history.

### Error taxonomy

| Code | Origin | Auto-retry | Terminal disposition |
|---|---|---|---|
| `AccountIneligible` | Eligibility gate | No | Escalate — account disabled |
| `StepUpAuthenticationRequired` | Password reset branch | No | Deny — re-challenge required |
| `PasswordResetNotConfirmed` | Graph poll | No | Escalate to identity team |
| `UnsupportedRequestType` | Switch default | No | Escalate — no automation registered |
| `HardwareFaultNotAutoRemediable` | Printer classification | No | Dispatch field technician |
| `PrinterVerificationFailed` | Post-remediation check | No | Escalate to Tier 2 |

Transient transport failures are handled *beneath* this taxonomy by per-action exponential retry policies (Graph reads: 3–4 attempts; runbook start: 3 attempts) and never surface as escalations unless the retry budget is exhausted. The password reset call itself is configured `"retryPolicy": { "type": "none" }` — retrying a partially-applied credential change is more dangerous than failing it cleanly.

---

## Data Model

Dataverse table `cr123_tier1remediationlogs`:

| Column | Type | Notes |
|---|---|---|
| `cr123_correlationid` | Text | Ticket ID, falling back to workflow run name |
| `cr123_ticketid` | Text | Originating service desk record |
| `cr123_requesttype` | Choice | `password_reset` \| `printer_diagnostics` |
| `cr123_requesterupn` | Text | Requester UPN |
| `cr123_intakechannel` | Text | Defaults to `teams_mobile` |
| `cr123_outcome` | Choice | `pending` \| `remediated` \| `escalated` |
| `cr123_failurereason` | Multiline text | Empty on success |
| `cr123_failedactions` | Multiline text | Serialized failed-action array (failure path only) |
| `cr123_workflowrunid` | Text | Joins to Logic Apps run history |
| `cr123_starttimeutc` | DateTime | Captured at workflow entry |
| `cr123_completedtimeutc` | DateTime | Captured at write time |

Both success and failure paths write the same core columns, so mean-time-to-remediation and deflection rate are derivable from a single table without joining across sources. `cr123_workflowrunid` is the join key back to full run history for any record requiring deeper forensics.

---

## Operational Model

The following are **design targets used to size the solution**, not measured production results:

| Metric | Target | Rationale |
|---|---|---|
| Password reset, end to end | < 90 s | Dominated by the Graph long-running poll |
| Printer remediation, end to end | < 4 min | Dominated by runbook execution and re-verification |
| Auto-remediation rate, password reset | 85–95% | Residual is disabled accounts and failed step-up |
| Auto-remediation rate, printer | 60–75% | Residual is hardware faults, which are out of scope by design |
| Unlogged runs | 0 | Every terminal path writes a Dataverse record |

The last row is the one that should be monitored as a correctness invariant. A run that terminates without a log record indicates a gap in the `runAfter` graph, not merely a missing report.

---

## Prerequisites

### Licensing and platform

- Microsoft 365 tenant with Entra ID P1 or higher (required for SSPR and Conditional Access)
- Power Platform environment with a provisioned Dataverse database
- Azure subscription for the Logic App and Azure Automation account
- Microsoft Teams deployed to mobile clients
- Universal Print subscription **or** a reachable on-premises print server with an Automation Hybrid Worker

### Graph API permissions

Granted to the Logic App's system-assigned managed identity as **application** permissions, each requiring tenant administrator consent:

| Permission | Purpose |
|---|---|
| `User.Read.All` | Resolve requester and evaluate eligibility |
| `UserAuthenticationMethod.ReadWrite.All` | Execute the password reset |
| `Printer.Read.All` | Read printer state and queue depth |
| `PrintJob.ReadWriteBasic.All` | Inspect and purge print jobs |

`UserAuthenticationMethod.ReadWrite.All` is a highly privileged permission. Scope it deliberately — see [Security Considerations](#security-considerations).

### Connections

- Dataverse (Common Data Service — current environment)
- Microsoft Teams
- Azure Automation account with the printer remediation runbook published and a webhook generated

---

## Deployment

### 1. Provision the Dataverse table

Create `cr123_tier1remediationlogs` with the columns in [Data Model](#data-model). If your publisher prefix differs from `cr123_`, update it consistently in `workflows/tier1-remediation.json` — the prefix appears in both the success and failure write bodies.

### 2. Deploy the Logic App

```bash
az group create --name rg-tier1-remediation --location eastus

az deployment group create \
  --resource-group rg-tier1-remediation \
  --template-file infra/logicapp.bicep \
  --parameters workflowDefinition=@workflows/tier1-remediation.json
```

> `infra/logicapp.bicep` is not included in this reference implementation. Either author it against the [`Microsoft.Logic/workflows`](https://learn.microsoft.com/azure/templates/microsoft.logic/workflows) resource schema, or import `workflows/tier1-remediation.json` directly through the Logic Apps designer's **Code view**.

### 3. Enable the managed identity and grant Graph permissions

```bash
az logic workflow identity assign \
  --resource-group rg-tier1-remediation \
  --name logic-tier1-remediation
```

Grant each application permission from [Prerequisites](#prerequisites) to the resulting service principal object ID, then complete tenant admin consent.

### 4. Substitute environment parameters

Replace the placeholder defaults in the `parameters` block of the workflow definition:

| Parameter | Replace with |
|---|---|
| `dataverseEnvironmentUrl` | Your Dataverse hostname, e.g. `contoso.crm.dynamics.com` |
| `remediationLogTable` | Your table's logical name |
| `tier2TeamId` / `tier2ChannelId` | Target Teams team and channel identifiers |
| `printerRunbookWebhookUri` | **Key Vault reference — do not commit a literal value** |
| `$connections` | Real connection resource IDs from your subscription |

`printerRunbookWebhookUri` is typed `SecureString` and ships with an empty default. An Azure Automation webhook URI is a bearer credential: anyone holding it can start the runbook. Supply it from Key Vault at deployment time.

### 5. Configure Teams intake

Publish the Power Virtual Agent bot to the Teams mobile client, configure the Adaptive Card to emit the trigger's JSON schema, and point its HTTP action at the Logic App's callback URL.

### 6. Validate

Exercise all six error codes from the [error taxonomy](#error-taxonomy) against a non-production tenant before enabling the intake bot for general users. Confirm in particular that:

- A run with `mfaSatisfied: false` terminates *without* reaching the Graph reset call.
- Every terminal path — including the denials — produces a Dataverse record.
- Password material does not appear anywhere in Logic Apps run history.

---

## Security Considerations

**The managed identity is the highest-value asset in this design.** It holds `UserAuthenticationMethod.ReadWrite.All`, which is effectively the ability to take over any account in scope. Compromise of the Logic App is compromise of the tenant's identity plane. Mitigate accordingly:

- Restrict the identity's reach using [Entra ID administrative units](https://learn.microsoft.com/entra/identity/role-based-access-control/administrative-units) so it cannot act on privileged or executive accounts.
- Treat the Logic App resource as a Tier-0 asset for RBAC purposes. Contributor access to it is equivalent to the permissions it holds.
- Alert on any change to the workflow definition.

**Run history is a disclosure channel.** `secureData` is applied to the reset request and poll responses, but it is per-action configuration — any action added later that touches credential material needs the same treatment, and nothing in the platform enforces this. Audit it on every change.

**The HTTP trigger's callback URL contains a SAS signature.** Anyone holding the full URL can invoke the workflow. It is not a secret that can be rotated casually — rotating it requires reconfiguring every caller. Restrict trigger access by IP range to the Power Platform service tags, and never log the callback URL.

**Eligibility is evaluated on `accountEnabled` alone.** This is intentionally minimal for a reference implementation. Production deployments should extend `Check_Account_Is_Eligible` to exclude privileged role holders, service accounts, and accounts flagged by Identity Protection — the check is a single `If` expression and is the correct place to add those conditions.

---

## Repository Layout

```
tier1-remediation/
├── README.md                          # This document
├── diagrams/
│   └── architecture.mmd               # Mermaid source — full system architecture
├── scripts/
│   └── validate_workflow.py           # Structural validation for the workflow definition
└── workflows/
    └── tier1-remediation.json         # Logic Apps / Power Automate workflow definition
```

## Validation

The workflow definition is checked in CI on every push and pull request that touches this directory (`.github/workflows/validate-tier1-remediation.yml`). Run the same checks locally:

```bash
python3 tier1-remediation/scripts/validate_workflow.py
```

Fourteen checks run, covering three categories:

**Structural integrity** — the definition parses, declares the Logic Apps schema, has exactly one HTTP request trigger, action names are unique across the whole tree, every `runAfter` target resolves to a real sibling, and every `body()` / `outputs()` reference names an action that exists. Logic Apps resolves actions by bare name, so a duplicate silently makes those references ambiguous; that class of bug is invisible on inspection and fatal at runtime.

**Declaration hygiene** — every variable referenced through `variables()` is initialized, and every parameter referenced through `parameters()` is declared.

**Secret and tenant-data leakage** — no GUID outside the known placeholder set appears in the file, no Azure Automation webhook URI is committed, `printerRunbookWebhookUri` remains a `SecureString` with an empty default, and the password reset action still marks its inputs and outputs as `secureData`.

That last group is the one with ongoing value. The structural checks mostly confirm what review would catch; the leakage checks catch the mistake that happens months later, when someone pastes a live subscription ID or a working webhook URI into the sample while debugging and commits it without noticing.

---

## License

MIT — see [LICENSE](../LICENSE) at the repository root.
