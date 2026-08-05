# Deal Intelligence Architecture

The Deal Intelligence Pipeline applied to a private real estate investment context, showing data sources, the six intelligence stages, governance controls, and the human decision layer.

The design constraint reflected throughout: analysis flows toward people, and decision authority never flows back into the system.

```mermaid
graph TB
    subgraph SOURCES["Data Sources"]
        EXT[External Market Data<br/>Demographics · Employment · Permits]
        CAP[Capital Markets Data<br/>Rates · Cap Rates · Comparables]
        DEAL[Inbound Opportunities<br/>OM · Rent Roll · T-12]
        INT[(Internal Records<br/>Prior Investments · Outcomes)]
    end

    subgraph INTEL["Intelligence Stages"]
        S1[Stage 1 — Market Intelligence<br/>Continuous submarket scoring]
        S2[Stage 2 — Property Intelligence<br/>Extraction and normalization]
        S3[Stage 3 — Internal Knowledge<br/>Precedent and assumption ledger]
        S4[Stage 4 — Risk Scoring<br/>Standalone and portfolio-marginal]
        S5[Stage 5 — Scenario Modeling<br/>Stress testing]
        S6[Stage 6 — Committee Support<br/>Briefing generation]
    end

    subgraph CONTROL["PAIOS Governance Layer"]
        CLASS[Request Classification]
        PROV[Provenance and Data Vintage]
        MODEL[Model Version Registry]
        GATE[Human Approval Gate]
        POLICY[Policy Bundle — JSON]
    end

    subgraph HUMAN["Human Decision Layer"]
        ANALYST[Analyst Review]
        IC[Investment Committee]
        OVERRIDE[Override Capture]
    end

    subgraph AUDIT["Audit and Learning"]
        LOG[(Audit Log)]
        LEDGER[(Assumption Ledger)]
        DRIFT[Drift and Backtest Reports]
    end

    EXT --> S1
    CAP --> S1
    DEAL --> S2
    INT --> S3

    S1 --> S4
    S2 --> S4
    S3 --> S4
    S4 --> S5
    S5 --> S6

    POLICY --> CLASS
    POLICY --> GATE
    CLASS --> S2
    PROV --> S4
    MODEL --> S4
    MODEL --> S5

    S4 --> ANALYST
    S6 --> GATE
    GATE --> IC

    ANALYST --> OVERRIDE
    IC --> OVERRIDE
    OVERRIDE --> LEDGER
    IC --> LEDGER

    S1 --> LOG
    S2 --> LOG
    S4 --> LOG
    S5 --> LOG
    S6 --> LOG
    GATE --> LOG

    LEDGER --> S3
    LOG --> DRIFT
    LEDGER --> DRIFT

    style CONTROL fill:#1a3a5c,color:#fff
    style HUMAN fill:#2d6a2d,color:#fff
    style AUDIT fill:#5c3a1a,color:#fff
```

## Architectural Notes

**Analysis flows one direction.** Intelligence stages produce output that moves toward the human decision layer. No path returns from the decision layer into an execution action. The system prepares; it does not act.

**The learning loop is closed through people.** Outcomes and overrides are captured from analyst and committee activity, written to the assumption ledger, and fed back into Stage 3. The system learns from recorded human judgment rather than from its own prior output.

**Governance is applied to inputs, not appended to outputs.** Classification, provenance, and model version registration constrain the intelligence stages as they run. Governance applied only at the end would produce results that cannot be traced.

**Every stage writes to the audit log.** Reproducing any analysis requires the model version and data vintage in effect when it was produced, both of which are recorded at execution time.

## Related Documentation

- [Deal Intelligence Pipeline](../../docs/DEAL_INTELLIGENCE_PIPELINE.md)
- [Deal Scoring and Model Governance](../../docs/DEAL_SCORING_AND_MODEL_GOVERNANCE.md)
- [Deal Screening Funnel](../workflows/deal-screening-funnel.md)
- [System Architecture](system-architecture.md)
