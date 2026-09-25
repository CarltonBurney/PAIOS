using Paios.CommandCenter.Governance;
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
    GovernanceClient governance,
    ILogger<DashboardAggregator> logger)
{
    public async Task<DashboardSnapshot> GetSnapshotAsync(CancellationToken cancellationToken = default)
    {
        // Subsystems are read concurrently and independently: one failing source
        // must not prevent the others from reporting.
        var modelsTask = BuildModelsAsync(cancellationToken);
        var operationsTask = BuildOperationsAsync(cancellationToken);
        var governanceTask = BuildGovernanceAsync(cancellationToken);

        await Task.WhenAll(modelsTask, operationsTask, governanceTask);

        var (models, modelAlerts) = modelsTask.Result;
        var (ops, opsAlerts) = operationsTask.Result;
        var (gov, govAlerts) = governanceTask.Result;
        var agents = BuildAgents();

        var subsystems = new List<SubsystemSummary> { models, gov, agents, ops };
        var alerts = modelAlerts.Concat(govAlerts).Concat(opsAlerts)
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
    /// The governance control plane, read over HTTP.
    ///
    /// Unlike Agent Lab this subsystem *is* implemented, so an unreachable kernel
    /// is <c>unavailable</c> rather than <c>not_implemented</c>. That is the
    /// availability-versus-health separation doing its job: "built but down" and
    /// "not built" are different facts and stay different here.
    /// </summary>
    private async Task<(SubsystemSummary, List<DashboardAlert>)> BuildGovernanceAsync(
        CancellationToken cancellationToken)
    {
        const string subsystemId = "governance";
        const string source = "/api/governance/status";
        var alerts = new List<DashboardAlert>();

        try
        {
            if (!await governance.IsEnabledAsync(cancellationToken))
            {
                // Switched off deliberately. Not implemented is the honest label:
                // there is nothing to measure, and nothing is broken.
                return (new SubsystemSummary
                {
                    SubsystemId = subsystemId,
                    DisplayName = "Governance",
                    Workspace = "governance",
                    Source = "none — disabled in config/governance.json",
                    Availability = SubsystemAvailability.NotImplemented,
                    Status = HealthState.Unknown,
                    Metrics = Array.Empty<SubsystemMetric>(),
                    Detail = "The control plane is disabled in config/governance.json."
                }, alerts);
            }

            var health = await governance.GetHealthAsync(cancellationToken);
            var endpoint = await governance.GetEndpointAsync(cancellationToken);

            if (health.Status != HealthState.Healthy)
            {
                alerts.Add(new DashboardAlert(
                    health.Status == HealthState.Unavailable ? AlertSeverity.Critical : AlertSeverity.Warning,
                    subsystemId,
                    GovernanceHealthCheck.Id,
                    $"Governance control plane is {Describe(health.Status)}"
                        + (health.ErrorMessage is null ? "." : $": {health.ErrorMessage}")));
            }

            // Status is only read when the kernel is answering. An unreachable
            // kernel yields no metrics at all rather than zeros.
            var status = health.Status == HealthState.Unavailable
                ? GovernanceResult<GovernanceStatus>.Unavailable(health.ErrorMessage ?? "unreachable")
                : await governance.GetStatusAsync(cancellationToken);

            if (status.Status == HealthState.Degraded && status.ErrorMessage is not null
                && health.Status == HealthState.Healthy)
            {
                // Health is fine but the authenticated read is not — almost always
                // a credential problem, and worth its own alert because the fix is
                // different.
                alerts.Add(new DashboardAlert(AlertSeverity.Warning, subsystemId, source, status.ErrorMessage));
            }

            var metrics = new List<SubsystemMetric>();
            if (status.Value is { } s)
            {
                if (s.Policies is { } policies)
                {
                    metrics.Add(new SubsystemMetric("policies", $"{policies.Enabled}/{policies.Total} enabled"));
                }

                // Null means the kernel has no registry attached; it is not zero.
                if (s.Tools is { Registered: { } registered })
                {
                    metrics.Add(new SubsystemMetric(
                        "tools", s.Tools.Enabled is { } enabled
                            ? $"{enabled}/{registered} enabled"
                            : registered.ToString()));
                }

                if (s.RiskModel is { } risk)
                {
                    metrics.Add(new SubsystemMetric("risk levels", risk.Levels.ToString()));
                }
            }

            // The worse of the two reads wins: a healthy self-check plus a refused
            // authenticated read is not a healthy subsystem.
            var overall = Rollup([health.Status, status.Status]);

            return (new SubsystemSummary
            {
                SubsystemId = subsystemId,
                DisplayName = "Governance",
                Workspace = "governance",
                Source = source,
                Availability = SubsystemAvailability.Implemented,
                Status = overall,
                Metrics = metrics,
                Detail = overall == HealthState.Healthy
                    ? null
                    : health.ErrorMessage ?? status.ErrorMessage
                        ?? $"The control plane at {endpoint} is {Describe(overall)}."
            }, alerts);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Governance subsystem could not be read.");
            alerts.Add(new DashboardAlert(AlertSeverity.Critical, subsystemId, "aggregator",
                $"Could not read the governance subsystem: {ex.Message}"));

            return (Unreadable(subsystemId, "Governance", "governance", source, ex), alerts);
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
        Detail = "No agent registry or execution path exists. The governance kernel "
               + "provides the Tool Registry and Execution Gateway an agent layer "
               + "would run under, but no agent registry is built on them yet."
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
