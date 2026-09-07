using System.Text.Json;
using System.Text.Json.Serialization;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Dashboard;

/// <summary>
/// Whether a subsystem is built at all. This is deliberately separate from
/// <see cref="HealthState"/>: a subsystem that does not exist is not "unhealthy",
/// and conflating the two would let an unbuilt feature read as a broken one.
/// </summary>
[JsonConverter(typeof(SubsystemAvailabilityJsonConverter))]
public enum SubsystemAvailability
{
    Implemented,
    NotImplemented
}

public sealed class SubsystemAvailabilityJsonConverter()
    : JsonStringEnumConverter<SubsystemAvailability>(JsonNamingPolicy.SnakeCaseLower);

[JsonConverter(typeof(AlertSeverityJsonConverter))]
public enum AlertSeverity
{
    Critical,
    Warning,
    Info
}

public sealed class AlertSeverityJsonConverter()
    : JsonStringEnumConverter<AlertSeverity>(JsonNamingPolicy.SnakeCaseLower);

/// <summary>
/// One headline number on a subsystem card. Carries its own label so the UI
/// renders whatever the subsystem reports rather than hard-coding captions.
/// </summary>
public sealed record SubsystemMetric(string Label, string Value);

/// <summary>
/// An alert derived from real underlying state. <see cref="SourceId"/> names the
/// exact record it came from, so every alert is traceable rather than asserted.
/// </summary>
public sealed record DashboardAlert(
    AlertSeverity Severity,
    string SubsystemId,
    string SourceId,
    string Message);

public sealed record SubsystemSummary
{
    public required string SubsystemId { get; init; }
    public required string DisplayName { get; init; }

    /// <summary>Workspace id the card drills into — matches the nav's data-workspace.</summary>
    public required string Workspace { get; init; }

    /// <summary>
    /// The API this card's numbers came from. Required by the phase criterion
    /// that dashboard counts be traceable to an underlying source.
    /// </summary>
    public required string Source { get; init; }

    public SubsystemAvailability Availability { get; init; } = SubsystemAvailability.Implemented;

    /// <summary>Unknown until a real read succeeds — never optimistically healthy.</summary>
    public HealthState Status { get; init; } = HealthState.Unknown;

    public IReadOnlyList<SubsystemMetric> Metrics { get; init; } = Array.Empty<SubsystemMetric>();

    /// <summary>Why this subsystem is not healthy, or why it cannot be read.</summary>
    public string? Detail { get; init; }
}

public sealed record DashboardSnapshot(
    HealthState Overall,
    string GeneratedAt,
    IReadOnlyList<SubsystemSummary> Subsystems,
    IReadOnlyList<DashboardAlert> Alerts);
