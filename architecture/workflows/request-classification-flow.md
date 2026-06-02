# Request Classification Flow

The request classification flow describes how incoming requests are evaluated, categorized, and routed through the PAIOS governance model.

## Classification Steps

```mermaid
flowchart LR
    A([Incoming Request]) --> B{Identity Check}
    B -->|Authorized| C[Context Extraction]
    B -->|Unauthorized| Z[Block + Log]

    C --> D[Request Type Classification]
    D --> E{Risk Level Assignment}

    E -->|Low| F[Auto-Route to Agent]
    E -->|Standard| F
    E -->|Sensitive| G[Human Review Required]
    E -->|Compliance| G
    E -->|Security| H[Escalate to Admin]

    G --> I{Approval Decision}
    I -->|Approved| F
    I -->|Rejected| Z
    I -->|Timeout| H

    F --> J[Agent Execution]
    J --> K[Output Review]
    K --> L[(Audit Log)]
    L --> M([Response Delivered])

    Z --> L
    H --> L

    style G fill:#ff8800,color:#fff
    style H fill:#ff4444,color:#fff
    style Z fill:#cc0000,color:#fff
    style M fill:#00aa44,color:#fff
```

## Classification Categories

| Request Type | Description | Default Routing |
|-------------|-------------|-----------------|
| `project` | Project planning, tracking, coordination | Project Agent |
| `technical` | Technical support, troubleshooting, implementation | Technical Agent |
| `logical` | Analysis, reasoning, research | Logical Agent |
| `core` | General operations, documentation, communication | Core Agent |
| `governance_change` | Any modification to governance rules or policy | Human Approval Gate |

## Risk Levels

| Level | Trigger Condition | Action |
|-------|------------------|--------|
| Low | Routine request, no sensitive data | Auto-execute |
| Standard | Normal operational request | Auto-execute with logging |
| Sensitive | Personal data, client information involved | Human review |
| Compliance | Regulatory or legal implications | Human review + documentation |
| Security | System changes, access modifications | Admin escalation |
