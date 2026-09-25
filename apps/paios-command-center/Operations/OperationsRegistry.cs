using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Operations;

/// <summary>Roll-up of the whole system, plus the individual records behind it.</summary>
public sealed record OperationsSnapshot(
    HealthState Overall,
    string GeneratedAt,
    IReadOnlyDictionary<string, int> Counts,
    IReadOnlyList<ServiceHealth> Services);

/// <summary>
/// Runs every registered check and merges the results. A check that throws is
/// converted into a degraded record naming the failure — one broken probe can
/// never prevent the rest of the workspace from rendering.
/// </summary>
public sealed class OperationsRegistry(
    IEnumerable<IServiceHealthCheck> checks,
    ILogger<OperationsRegistry> logger)
{
    public async Task<OperationsSnapshot> GetSnapshotAsync(CancellationToken cancellationToken = default)
    {
        var results = await Task.WhenAll(checks.Select(check => RunSafelyAsync(check, cancellationToken)));
        var services = results.SelectMany(r => r).OrderBy(s => s.Category).ThenBy(s => s.DisplayName).ToList();

        var counts = new Dictionary<string, int>
        {
            ["healthy"] = services.Count(s => s.Status == HealthState.Healthy),
            ["degraded"] = services.Count(s => s.Status == HealthState.Degraded),
            ["unavailable"] = services.Count(s => s.Status == HealthState.Unavailable),
            ["unknown"] = services.Count(s => s.Status == HealthState.Unknown)
        };

        return new OperationsSnapshot(Rollup(services), DateTimeOffset.UtcNow.ToString("O"), counts, services);
    }

    public async Task<ServiceHealth?> GetServiceAsync(string serviceId, CancellationToken cancellationToken = default)
    {
        var snapshot = await GetSnapshotAsync(cancellationToken);
        return snapshot.Services.FirstOrDefault(
            s => string.Equals(s.ServiceId, serviceId, StringComparison.OrdinalIgnoreCase));
    }

    /// <summary>
    /// Worst observed state wins, with unknown ranking below unavailable: a
    /// system whose state cannot be determined must not read as merely degraded.
    /// An empty system is unknown, never healthy.
    /// </summary>
    private static HealthState Rollup(IReadOnlyList<ServiceHealth> services)
    {
        if (services.Count == 0)
        {
            return HealthState.Unknown;
        }

        if (services.Any(s => s.Status == HealthState.Unavailable)) return HealthState.Unavailable;
        if (services.Any(s => s.Status == HealthState.Degraded)) return HealthState.Degraded;
        if (services.All(s => s.Status == HealthState.Healthy)) return HealthState.Healthy;
        return HealthState.Unknown;
    }

    private async Task<IReadOnlyList<ServiceHealth>> RunSafelyAsync(
        IServiceHealthCheck check, CancellationToken cancellationToken)
    {
        try
        {
            return await check.CheckAsync(cancellationToken);
        }
        catch (Exception ex)
        {
            // Deliberately broad: an unforeseen probe bug must degrade one card,
            // not blank the Operations workspace.
            logger.LogError(ex, "Health check '{ServiceId}' threw; reporting it as degraded.", check.ServiceId);

            return
            [
                new ServiceHealth
                {
                    ServiceId = check.ServiceId,
                    DisplayName = check.ServiceId,
                    Category = ServiceCategory.Infrastructure,
                    Status = HealthState.Degraded,
                    ErrorMessage = $"Health check failed: {ex.Message}",
                    LastCheckedAt = DateTimeOffset.UtcNow.ToString("O")
                }
            ];
        }
    }
}
