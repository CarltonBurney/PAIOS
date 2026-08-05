# Policies

This directory contains the governance policy model: the schemas that define its structure, and a sample bundle that demonstrates it.

## Contents

**schemas/**

| Schema | Describes |
|--------|-----------|
| `policy-bundle.schema.json` | A versioned, immutable set of classification and decision rules |
| `agent-policy.schema.json` | Ownership, boundaries, and lifecycle for a single governed agent |
| `request-record.schema.json` | The composite state of a governed request through its lifecycle |
| `audit-record.schema.json` | A single append-only lifecycle transition entry |

**sample-governance-policies.json**

A bundle conforming to `policy-bundle.schema.json`. Illustrative content — it demonstrates the structure, not a tenant's rules.

## How the model works

A policy bundle is data. The control plane reads it; it does not embed the rules it enforces. Changing what is governed means publishing a new bundle version, not modifying the control plane.

Bundles are immutable once published. A request pins a bundle version when its context is resolved and evaluates against that version for its entire lifetime, which is what allows a past decision to be reconstructed later from the rules that actually produced it.

Decision rules are evaluated as an unordered set. All matching rules are collected and resolved as follows:

1. Any matched `deny` → the decision is `deny`
2. Otherwise any matched `require_approval` → the decision is `require_approval`
3. Otherwise a matched `allow` → the decision is `allow`
4. No rule matched → the decision is `require_approval`

Obligations merge across matched rules: permissions intersect, restrictions union. A rule can narrow what another rule permits; it can never widen it.

Full evaluation semantics, risk scoring, and enforcement points are specified in [`../architecture/DESIGN.md`](../architecture/DESIGN.md).

## Validating a bundle

The schemas are standard JSON Schema (draft 2020-12) and validate with any conforming tool:

```bash
npx ajv-cli validate \
  -s policies/schemas/policy-bundle.schema.json \
  -r policies/schemas/agent-policy.schema.json \
  -d policies/sample-governance-policies.json
```
