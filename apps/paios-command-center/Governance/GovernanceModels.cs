using System.Text.Json.Serialization;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Governance;

/// <summary>
/// Normalized result of reading the control plane. Mirrors the Phase 1 provider
/// contract: every network and protocol failure arrives here as a
/// <see cref="HealthState"/> with a message, never as an exception, so one
/// unreachable kernel cannot blank a workspace.
/// </summary>
/// <typeparam name="T">The payload, absent whenever the read did not succeed.</typeparam>
public sealed record GovernanceResult<T>(HealthState Status, T? Value, string? ErrorMessage)
    where T : class
{
    public static GovernanceResult<T> Ok(T value) => new(HealthState.Healthy, value, null);

    public static GovernanceResult<T> Unavailable(string message)
        => new(HealthState.Unavailable, null, message);

    /// <summary>
    /// The kernel answered but something is wrong — a component failed to load,
    /// the credential was rejected, or the body did not parse. Distinguished from
    /// unavailable because "reachable but refusing" and "not there at all" need
    /// different responses from an operator.
    /// </summary>
    public static GovernanceResult<T> Degraded(string message, T? value = null)
        => new(HealthState.Degraded, value, message);
}

/// <summary>One component of the kernel's own self-check.</summary>
public sealed record GovernanceComponent
{
    [JsonPropertyName("status")]
    public string Status { get; init; } = "unknown";

    [JsonPropertyName("detail")]
    public string? Detail { get; init; }
}

/// <summary>Response shape of the kernel's <c>/health</c>.</summary>
public sealed record GovernanceHealthPayload
{
    [JsonPropertyName("status")]
    public string Status { get; init; } = "unknown";

    [JsonPropertyName("environment")]
    public string? Environment { get; init; }

    [JsonPropertyName("checks")]
    public Dictionary<string, GovernanceComponent> Checks { get; init; } = new();
}

public sealed record GovernanceProviderInfo
{
    [JsonPropertyName("name")]
    public string? Name { get; init; }

    [JsonPropertyName("model")]
    public string? Model { get; init; }
}

public sealed record GovernancePolicyInfo
{
    [JsonPropertyName("name")]
    public string? Name { get; init; }

    [JsonPropertyName("total")]
    public int Total { get; init; }

    [JsonPropertyName("enabled")]
    public int Enabled { get; init; }
}

public sealed record GovernanceRiskModelInfo
{
    [JsonPropertyName("name")]
    public string? Name { get; init; }

    [JsonPropertyName("default_level")]
    public string? DefaultLevel { get; init; }

    [JsonPropertyName("levels")]
    public int Levels { get; init; }

    [JsonPropertyName("detectors")]
    public int Detectors { get; init; }
}

/// <summary>
/// Tool counts. Nullable on purpose: an absent registry is <c>null</c>, which is
/// not the same as a registry containing nothing. Rendering a null as 0 would be
/// the fabricated-zero problem the dashboard exists to avoid.
/// </summary>
public sealed record GovernanceToolCounts
{
    [JsonPropertyName("registered")]
    public int? Registered { get; init; }

    [JsonPropertyName("enabled")]
    public int? Enabled { get; init; }
}

/// <summary>Response shape of <c>/api/governance/status</c>.</summary>
public sealed record GovernanceStatus
{
    [JsonPropertyName("environment")]
    public string? Environment { get; init; }

    [JsonPropertyName("provider")]
    public GovernanceProviderInfo? Provider { get; init; }

    [JsonPropertyName("policies")]
    public GovernancePolicyInfo? Policies { get; init; }

    [JsonPropertyName("risk_model")]
    public GovernanceRiskModelInfo? RiskModel { get; init; }

    [JsonPropertyName("tools")]
    public GovernanceToolCounts? Tools { get; init; }

    /// <summary>
    /// Which approval handler the kernel is running. Worth surfacing: with the
    /// default, anything requiring a human is refused, and an operator should be
    /// able to see that without reading the kernel's source.
    /// </summary>
    [JsonPropertyName("approval_handler")]
    public string? ApprovalHandler { get; init; }

    /// <summary><c>token</c> or <c>none</c>. Never the token itself.</summary>
    [JsonPropertyName("authentication")]
    public string? Authentication { get; init; }
}

public sealed record GovernanceViolation
{
    [JsonPropertyName("policy_id")]
    public string? PolicyId { get; init; }

    [JsonPropertyName("control")]
    public string? Control { get; init; }

    [JsonPropertyName("detail")]
    public string? Detail { get; init; }
}

public sealed record GovernanceRouting
{
    [JsonPropertyName("disposition")]
    public string? Disposition { get; init; }

    [JsonPropertyName("agent")]
    public string? Agent { get; init; }

    [JsonPropertyName("reason")]
    public string? Reason { get; init; }

    [JsonPropertyName("requires_human")]
    public bool RequiresHuman { get; init; }
}

public sealed record GovernanceRisk
{
    [JsonPropertyName("level")]
    public string? Level { get; init; }

    [JsonPropertyName("domains")]
    public List<string> Domains { get; init; } = new();
}

/// <summary>
/// One request's decision. A refusal is a successful read of governance, so this
/// is what comes back for a blocked request too — the caller reads
/// <see cref="Disposition"/> rather than inferring from a status code.
/// </summary>
public sealed record GovernanceDecision
{
    [JsonPropertyName("disposition")]
    public string? Disposition { get; init; }

    [JsonPropertyName("delivered")]
    public bool Delivered { get; init; }

    [JsonPropertyName("blocked")]
    public bool Blocked { get; init; }

    [JsonPropertyName("risk")]
    public GovernanceRisk? Risk { get; init; }

    [JsonPropertyName("routing")]
    public GovernanceRouting? Routing { get; init; }

    [JsonPropertyName("violations")]
    public List<GovernanceViolation> Violations { get; init; } = new();

    [JsonPropertyName("audit_ids")]
    public List<string> AuditIds { get; init; } = new();
}

/// <summary>A registered tool as the kernel reports it.</summary>
public sealed record GovernanceTool
{
    [JsonPropertyName("tool_id")]
    public string? ToolId { get; init; }

    [JsonPropertyName("risk_level")]
    public string? RiskLevel { get; init; }

    [JsonPropertyName("operation_type")]
    public string? OperationType { get; init; }

    [JsonPropertyName("availability")]
    public string? Availability { get; init; }

    [JsonPropertyName("status")]
    public string? Status { get; init; }
}

public sealed record GovernanceToolList
{
    [JsonPropertyName("tools")]
    public List<GovernanceTool> Tools { get; init; } = new();

    [JsonPropertyName("detail")]
    public string? Detail { get; init; }
}
