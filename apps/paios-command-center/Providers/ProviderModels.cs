using System.Text.Json;
using System.Text.Json.Serialization;

namespace Paios.CommandCenter.Providers;

/// <summary>
/// Provider kinds the normalized layer understands. Serialized as the lowercase
/// snake_case strings the shared schema uses ("ollama", "openai_compatible").
/// </summary>
[JsonConverter(typeof(ProviderTypeJsonConverter))]
public enum ProviderType
{
    Ollama,
    OpenaiCompatible,
    Other
}

/// <summary>Serializes <see cref="ProviderType"/> as "ollama" / "openai_compatible" / "other".</summary>
public sealed class ProviderTypeJsonConverter() : JsonStringEnumConverter<ProviderType>(JsonNamingPolicy.SnakeCaseLower);

/// <summary>
/// The four states every provider and model must be able to report. "Unknown"
/// is the honest default before a check has run — it is never a stand-in for
/// healthy.
/// </summary>
[JsonConverter(typeof(HealthStateJsonConverter))]
public enum HealthState
{
    Healthy,
    Degraded,
    Unavailable,
    Unknown
}

/// <summary>Serializes <see cref="HealthState"/> as its lowercase name.</summary>
public sealed class HealthStateJsonConverter() : JsonStringEnumConverter<HealthState>(JsonNamingPolicy.SnakeCaseLower);

/// <summary>Outcome of a single health probe, before it is merged onto a provider.</summary>
public sealed record HealthResult(HealthState Status, long? LatencyMs, string? ErrorMessage);

public sealed record ModelProvider
{
    public required string ProviderId { get; init; }
    public required ProviderType ProviderType { get; init; }
    public required string Endpoint { get; init; }
    public HealthState Status { get; init; } = HealthState.Unknown;
    public string? LastCheckedAt { get; init; }
    public long? LatencyMs { get; init; }
    public string? ErrorMessage { get; init; }

    /// <summary>
    /// True when the provider was discovered by startup auto-detection rather
    /// than configured explicitly. Auto-detected providers are not persisted to
    /// the config file, so a restart re-detects rather than accumulating stale
    /// entries.
    /// </summary>
    public bool AutoDetected { get; init; }
}

public sealed record ModelRecord
{
    public required string ProviderId { get; init; }
    public required string ModelId { get; init; }
    public required string ModelName { get; init; }
    public IReadOnlyList<string> Capabilities { get; init; } = Array.Empty<string>();
    public HealthState Availability { get; init; } = HealthState.Unknown;
    public int? ContextWindow { get; init; }
    public long? SizeBytes { get; init; }
    public string? LastSeenAt { get; init; }
}

/// <summary>
/// Result of listing models. Carries the health of the listing attempt itself so
/// callers can distinguish "healthy provider, zero models" (valid) from "could
/// not read models" (degraded/unavailable) — the two collapse into an empty
/// array otherwise.
/// </summary>
public sealed record ModelListResult(HealthState Status, IReadOnlyList<ModelRecord> Models, string? ErrorMessage);
