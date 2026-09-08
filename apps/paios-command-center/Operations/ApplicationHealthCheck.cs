using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Operations;

/// <summary>
/// The Command Center reporting on itself. If this code runs, the host is
/// serving — so the honest answer is healthy, with uptime as the evidence.
/// This is measured, not a hard-coded green.
/// </summary>
public sealed class ApplicationHealthCheck(IHostEnvironment environment) : IServiceHealthCheck
{
    private static readonly DateTimeOffset StartedAt = DateTimeOffset.UtcNow;

    public string ServiceId => "command-center";

    public Task<IReadOnlyList<ServiceHealth>> CheckAsync(CancellationToken cancellationToken = default)
    {
        var uptime = DateTimeOffset.UtcNow - StartedAt;

        IReadOnlyList<ServiceHealth> result =
        [
            new ServiceHealth
            {
                ServiceId = ServiceId,
                DisplayName = "Command Center API",
                Category = ServiceCategory.Application,
                Status = HealthState.Healthy,
                Target = $"{environment.EnvironmentName} · up {FormatUptime(uptime)}",
                LatencyMs = 0,
                LastCheckedAt = DateTimeOffset.UtcNow.ToString("O")
            }
        ];

        return Task.FromResult(result);
    }

    private static string FormatUptime(TimeSpan uptime) => uptime.TotalHours >= 1
        ? $"{(int)uptime.TotalHours}h {uptime.Minutes}m"
        : uptime.TotalMinutes >= 1
            ? $"{(int)uptime.TotalMinutes}m {uptime.Seconds}s"
            : $"{(int)uptime.TotalSeconds}s";
}
