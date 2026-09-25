using Paios.CommandCenter.Operations;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Governance;

/// <summary>
/// Reports the control plane through the Phase 3 <see cref="IServiceHealthCheck"/>
/// contract, so it appears in Operations alongside every other component and needs
/// no special case in the roll-up.
///
/// The kernel's <c>/health</c> already reports per-component detail, so this check
/// emits one record for the kernel itself plus one per component it names. That
/// makes a partial failure — say the policy document is unreadable while the
/// provider is fine — visible in Operations rather than flattened into a single
/// amber light.
/// </summary>
public sealed class GovernanceHealthCheck(GovernanceClient client) : IServiceHealthCheck
{
    public const string Id = "governance-control-plane";

    public string ServiceId => Id;

    public async Task<IReadOnlyList<ServiceHealth>> CheckAsync(CancellationToken cancellationToken = default)
    {
        var now = DateTimeOffset.UtcNow.ToString("O");
        var endpoint = await client.GetEndpointAsync(cancellationToken);

        if (endpoint is null)
        {
            // Disabled by configuration. Not checkable, and explicitly not
            // "unavailable" — nobody turned it off by accident and calling it
            // broken would send an operator looking for a fault that is not there.
            return
            [
                new ServiceHealth
                {
                    ServiceId = Id,
                    DisplayName = "Governance control plane",
                    Category = ServiceCategory.Governance,
                    Status = HealthState.Unknown,
                    Target = null,
                    Checkable = false,
                    ErrorMessage = "Disabled in config/governance.json.",
                    LastCheckedAt = now
                }
            ];
        }

        var (result, latencyMs) = await GovernanceClient.TimedHealthAsync(client, cancellationToken);

        // The kernel's /health needs no credential, so a healthy answer there says
        // nothing about whether *we* can read it. Probing an authenticated route
        // too means a rejected credential shows up in Operations as a fault in the
        // link, instead of Operations reading green while the dashboard reads amber.
        var reachable = result.Status != HealthState.Unavailable;
        var authenticated = reachable
            ? await client.GetStatusAsync(cancellationToken)
            : null;

        var linkStatus = authenticated is null
            ? result.Status
            : Worst(result.Status, authenticated.Status);

        var records = new List<ServiceHealth>
        {
            new()
            {
                ServiceId = Id,
                DisplayName = "Governance control plane",
                Category = ServiceCategory.Governance,
                Status = linkStatus,
                Target = endpoint,
                LatencyMs = latencyMs,
                ErrorMessage = result.ErrorMessage
                    ?? (authenticated?.Status != HealthState.Healthy ? authenticated?.ErrorMessage : null)
                    ?? (linkStatus == HealthState.Healthy ? null : DescribePayload(result.Value)),
                LastCheckedAt = now
            }
        };

        // Component records only exist when the kernel actually named them. An
        // unreachable kernel contributes one record, not a fabricated set of
        // component failures it never reported.
        foreach (var (name, component) in result.Value?.Checks ?? new Dictionary<string, GovernanceComponent>())
        {
            records.Add(new ServiceHealth
            {
                ServiceId = $"{Id}:{name}",
                DisplayName = $"Control plane — {Humanize(name)}",
                Category = ServiceCategory.Governance,
                Status = MapComponentStatus(component.Status),
                Target = endpoint,
                ErrorMessage = MapComponentStatus(component.Status) == HealthState.Healthy
                    ? null
                    : component.Detail,
                LastCheckedAt = now
            });
        }

        return records;
    }

    /// <summary>
    /// The kernel speaks the same vocabulary the Command Center does, but an
    /// unrecognized word maps to <c>unknown</c> rather than being assumed healthy.
    /// </summary>
    /// <summary>Worse of two states, using the Operations roll-up ordering.</summary>
    private static HealthState Worst(HealthState a, HealthState b)
    {
        static int Rank(HealthState s) => s switch
        {
            HealthState.Healthy => 0,
            HealthState.Unknown => 1,
            HealthState.Degraded => 2,
            HealthState.Unavailable => 3,
            _ => 1
        };

        return Rank(a) >= Rank(b) ? a : b;
    }

    private static HealthState MapComponentStatus(string status) => status switch
    {
        "healthy" => HealthState.Healthy,
        "degraded" => HealthState.Degraded,
        "unavailable" => HealthState.Unavailable,
        _ => HealthState.Unknown
    };

    private static string? DescribePayload(GovernanceHealthPayload? payload)
    {
        if (payload is null)
        {
            return null;
        }

        var failing = payload.Checks
            .Where(c => MapComponentStatus(c.Value.Status) != HealthState.Healthy)
            .Select(c => Humanize(c.Key))
            .ToList();

        return failing.Count == 0 ? null : $"Failing: {string.Join(", ", failing)}.";
    }

    private static string Humanize(string name) => name.Replace('_', ' ');
}
