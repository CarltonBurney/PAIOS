using Paios.CommandCenter.Operations;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Dashboard;

/// <summary>
/// Builds the Command Center view by reading the same registries the individual
/// workspaces read. It holds no state of its own — there is deliberately no
/// dashboard-side copy of provider or service truth to drift out of sync, which
/// is what makes "change a service, the dashboard changes" true by construction
/// rather than by a refresh mechanism.
/// </summary>
public sealed class DashboardAggregator(
    ProviderRegistry providers,
    OperationsRegistry operations,
    ILogger<DashboardAggregator> logger)
{
    public async Task<DashboardSnapshot> GetSnapshotAsync(CancellationToken cancellationToken = default)
    {
        // Subsystems are read concurrently and independently: one failing source
        // must not prevent the others from reporting.
        var modelsTask = BuildModelsAsync(cancellationToken);
        var operationsTask = BuildOperationsAsync(cancellationToken);

        await Task.WhenAll(modelsTask, operationsTask);

        var (models, modelAlerts) = modelsTask.Result;
        var (ops, opsAlerts) = operationsTask.Result;
        var agents = BuildAgents();

        var subsystems = new List<SubsystemSummary> { models, agents, ops };
        var alerts = modelAlerts.Concat(opsAlerts)
            .OrderBy(a => a.Severity)
            .ThenBy(a => a.SubsystemId)
            .ToList();

        return new DashboardSnapshot(
            Rollup(subsystems),
            DateTimeOffset.UtcNow.ToString("O"),
            subsystems,
            alerts);
    }

    private async Task<(SubsystemSummary, List<DashboardAlert>)> BuildModelsAsync(CancellationToken cancellationToken)
    {
        const string subsystemId = "models";
        var alerts = new List<DashboardAlert>();

        try
        {
            var all = await providers.GetProvidersAsync(cancellationToken);

            if (all.Count == 0)
            {
                return (new SubsystemSummary
                {
                    SubsystemId = subsystemId,
                    DisplayName = "Local LLMs",
                    Workspace = "llm",
                    Source = "/api/providers",
                    Status = HealthState.Unknown,
                    Detail = "No providers configured or auto-detected.",
                    Metrics = [new SubsystemMetric("providers", "0")]
                }, alerts);
            }

            var healthy = all.Count(p => p.Status == HealthState.Healthy);

            // Model counts only come from providers that are actually readable;
            // an unreachable provider contributes no invented inventory.
            var modelCount = 0;
            foreach (var provider in all.Where(p => p.Status == HealthState.Healthy))
            {
                var models = await providers.GetModelsAsync(provider.ProviderId, cancellationToken);
                modelCount += models?.Models.Count ?? 0;
            }

            foreach (var provider in all.Where(p => p.Status != HealthState.Healthy))
            {
                alerts.Add(new DashboardAlert(
                    provider.Status == HealthState.Unavailable ? AlertSeverity.Critical : AlertSeverity.Warning,
                    subsystemId,
                    provider.ProviderId,
                    $"Provider '{provider.ProviderId}' is {Describe(provider.Status)}"
                        + (provider.ErrorMessage is null ? "." : $": {provider.ErrorMessage}")));
            }

            return (new SubsystemSummary
            {
                SubsystemId = subsystemId,
                DisplayName = "Local LLMs",
                Workspace = "llm",
                Source = "/api/providers",
                Status = Rollup(all.Select(p => p.Status).ToList()),
                Metrics =
                [
                    new SubsystemMetric("providers", $"{healthy}/{all.Count} healthy"),
                    new SubsystemMetric("models available", modelCount.ToString())
                ],
                Detail = healthy == all.Count ? null : $"{all.Count - healthy} of {all.Count} providers not healthy."
            }, alerts);
        }
        catch (Exception ex)
        {
            // Partial failure: this card reports the error, the dashboard survives.
            logger.LogError(ex, "Model subsystem could not be read.");
            alerts.Add(new DashboardAlert(AlertSeverity.Critical, subsystemId, "aggregator",
                $"Could not read the model subsystem: {ex.Message}"));

            return (Unreadable(subsystemId, "Local LLMs", "llm", "/api/providers", ex), alerts);
        }
    }

    private async Task<(SubsystemSummary, List<DashboardAlert>)> BuildOperationsAsync(CancellationToken cancellationToken)
    {
        const string subsystemId = "operations";
        var alerts = new List<DashboardAlert>();

        try
        {
            var snapshot = await operations.GetSnapshotAsync(cancellationToken);

            foreach (var service in snapshot.Services.Where(s => s.Status is HealthState.Unavailable or HealthState.Degraded))
            {
                alerts.Add(new DashboardAlert(
                    service.Status == HealthState.Unavailable ? AlertSeverity.Critical : AlertSeverity.Warning,
                    subsystemId,
                    service.ServiceId,
                    $"{service.DisplayName} is {Describe(service.Status)}"
                        + (service.ErrorMessage is null ? "." : $": {service.ErrorMessage}")));
            }

            foreach (var service in snapshot.Services.Where(s => !s.Checkable))
            {
                alerts.Add(new DashboardAlert(AlertSeverity.Info, subsystemId, service.ServiceId,
                    $"{service.DisplayName} is declared but has no implemented probe."));
            }

            var healthy = snapshot.Counts.TryGetValue("healthy", out var h) ? h : 0;

            return (new SubsystemSummary
            {
                SubsystemId = subsystemId,
                DisplayName = "Operations",
                Workspace = "ops",
                Source = "/api/operations/services",
                Status = snapshot.Overall,
                Metrics =
                [
                    new SubsystemMetric("services", $"{healthy}/{snapshot.Services.Count} healthy"),
                    new SubsystemMetric("alerts", alerts.Count(a => a.Severity != AlertSeverity.Info).ToString())
                ],
                Detail = snapshot.Services.Count == 0 ? "No checkable services." : null
            }, alerts);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Operations subsystem could not be read.");
            alerts.Add(new DashboardAlert(AlertSeverity.Critical, subsystemId, "aggregator",
                $"Could not read the operations subsystem: {ex.Message}"));

            return (Unreadable(subsystemId, "Operations", "ops", "/api/operations/services", ex), alerts);
        }
    }

    /// <summary>
    /// The agent layer has no implementation to read. It is reported as
    /// explicitly not implemented with <c>unknown</c> health and no metrics —
    /// never a fabricated agent count, and never a health state implying it was
    /// checked and found working.
    /// </summary>
    private static SubsystemSummary BuildAgents() => new()
    {
        SubsystemId = "agents",
        DisplayName = "Agent Lab",
        Workspace = "agents",
        Source = "none — no agent registry exists",
        Availability = SubsystemAvailability.NotImplemented,
        Status = HealthState.Unknown,
        Metrics = Array.Empty<SubsystemMetric>(),
        Detail = "No agent registry or execution path exists in this repository. "
               + "Phase 2 is blocked pending resolution of the governance implementation location."
    };

    private static SubsystemSummary Unreadable(
        string id, string name, string workspace, string source, Exception ex) => new()
    {
        SubsystemId = id,
        DisplayName = name,
        Workspace = workspace,
        Source = source,
        Status = HealthState.Degraded,
        Detail = $"Subsystem could not be read: {ex.Message}"
    };

    /// <summary>
    /// Worst state wins. Subsystems that are not implemented are excluded — an
    /// unbuilt feature must not drag the whole dashboard to unknown, but it also
    /// must not be counted as healthy. If nothing is implemented, the result is
    /// unknown rather than healthy.
    /// </summary>
    private static HealthState Rollup(IReadOnlyList<SubsystemSummary> subsystems)
        => Rollup(subsystems
            .Where(s => s.Availability == SubsystemAvailability.Implemented)
            .Select(s => s.Status)
            .ToList());

    private static HealthState Rollup(IReadOnlyList<HealthState> states)
    {
        if (states.Count == 0) return HealthState.Unknown;
        if (states.Any(s => s == HealthState.Unavailable)) return HealthState.Unavailable;
        if (states.Any(s => s == HealthState.Degraded)) return HealthState.Degraded;
        if (states.All(s => s == HealthState.Healthy)) return HealthState.Healthy;
        return HealthState.Unknown;
    }

    private static string Describe(HealthState state) => state switch
    {
        HealthState.Unavailable => "unavailable",
        HealthState.Degraded => "degraded",
        HealthState.Unknown => "in an unknown state",
        _ => "healthy"
    };
}
