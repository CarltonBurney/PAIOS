# Architecture

The architecture directory contains the system design, decision records, diagrams, and workflow documentation for the PAIOS framework.

## Contents

- **[DESIGN.md](DESIGN.md)** — the system design specification: component decomposition, request lifecycle, policy evaluation semantics, data model, interface contracts, and failure modes
- **decisions/** — architecture decision records capturing the structural choices behind the design and what each one costs
- **diagrams/** — visual representations of system components, governance flows, and integration topology
- **workflows/** — process references describing how requests, approvals, and operational activity move through the control model

The documentation in `docs/` describes what the framework governs. `DESIGN.md` describes how the control plane is structured to enforce it.

## Components

The architecture reflects the following structural layers:

- Control plane responsible for classifying and routing AI requests
- Governance enforcement layer applying policy and approval requirements
- Knowledge management layer for documentation capture and reuse
- Integration layer connecting Microsoft 365 services, workflows, and AI systems

## Data and Governance Flow

Requests enter the control plane, are classified by type and risk, routed through applicable governance controls, executed against appropriate AI or workflow systems, and logged for auditability and knowledge capture.

Human approval gates are applied based on classification results before execution proceeds.

## Design Properties

The design is organized around a small number of properties that the component boundaries exist to preserve:

- **Decisions are deterministic.** Classification may use a model; the gate that permits or denies execution may not.
- **Policy is versioned data.** Rules are published as immutable bundles and pinned per request, so a past decision remains reconstructible.
- **Degradation increases review.** A component that cannot complete its work escalates to a human rather than allowing execution to proceed ungoverned.
- **The record precedes the action.** Audit is written before execution and cannot be modified afterward.

Each is developed in `DESIGN.md` and recorded with its trade-offs in `decisions/`.
