using System.Text.Json.Serialization;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Operations;

/// <summary>
/// Where a service sits in the system, so the workspace can group without
/// hard-coding names.
/// </summary>
[JsonConverter(typeof(ServiceCategoryJsonConverter))]
public enum ServiceCategory
{
    Application,
    Provider,
    Infrastructure
}

public sealed class ServiceCategoryJsonConverter()
    : System.Text.Json.Serialization.JsonStringEnumConverter<ServiceCategory>(System.Text.Json.JsonNamingPolicy.SnakeCaseLower);

/// <summary>
/// One normalized health record. Every checkable component in the system reports
/// through this shape, reusing <see cref="HealthState"/> so Operations and the
/// provider layer cannot drift into different vocabularies.
/// </summary>
public sealed record ServiceHealth
{
    public required string ServiceId { get; init; }
    public required string DisplayName { get; init; }
    public required ServiceCategory Category { get; init; }

    /// <summary>Never defaults to healthy — an unrun check is <c>unknown</c>.</summary>
    public HealthState Status { get; init; } = HealthState.Unknown;

    /// <summary>Endpoint or description, so a reader can tell what was probed.</summary>
    public string? Target { get; init; }

    public long? LatencyMs { get; init; }

    /// <summary>Populated for every non-healthy state, so failures are inspectable.</summary>
    public string? ErrorMessage { get; init; }

    /// <summary>ISO-8601. Required by the phase criteria: status includes last-check info.</summary>
    public required string LastCheckedAt { get; init; }

    /// <summary>
    /// False for a component that is declared but has no implemented probe. Such
    /// a service reports <c>unknown</c> and says why, rather than being drawn as
    /// healthy or omitted.
    /// </summary>
    public bool Checkable { get; init; } = true;
}

/// <summary>
/// One contract for every health probe. Implementations should not throw — the
/// aggregator isolates failures anyway, but a probe that reports its own failure
/// produces a better message than a caught exception.
/// </summary>
public interface IServiceHealthCheck
{
    string ServiceId { get; }

    Task<IReadOnlyList<ServiceHealth>> CheckAsync(CancellationToken cancellationToken = default);
}
