using System.Diagnostics;
using System.Net.Sockets;
using System.Text.Json.Serialization;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Operations;

/// <summary>
/// A declared external dependency — Postgres, n8n, a vector store. Probe kind
/// decides how it is checked; an unrecognized kind reports <c>unknown</c> rather
/// than being guessed at.
/// </summary>
public sealed class MonitoredService
{
    [JsonPropertyName("serviceId")] public string ServiceId { get; set; } = string.Empty;
    [JsonPropertyName("displayName")] public string DisplayName { get; set; } = string.Empty;

    /// <summary>"tcp" or "http". Anything else yields an unknown, uncheckable service.</summary>
    [JsonPropertyName("probe")] public string Probe { get; set; } = string.Empty;

    [JsonPropertyName("host")] public string? Host { get; set; }
    [JsonPropertyName("port")] public int? Port { get; set; }
    [JsonPropertyName("url")] public string? Url { get; set; }
}

public sealed class OperationsConfiguration
{
    /// <summary>
    /// Empty by default. Nothing is monitored unless it is declared, so the
    /// workspace never invents a service that does not exist.
    /// </summary>
    [JsonPropertyName("monitored")] public List<MonitoredService> Monitored { get; set; } = new();
}

/// <summary>
/// Probes declared infrastructure. Timeouts, refused connections, and bad
/// responses each map to a distinct state with the detail attached.
/// </summary>
public sealed class InfrastructureHealthCheck(
    Func<Task<OperationsConfiguration>> configurationLoader,
    HttpClient httpClient,
    TimeSpan tcpTimeout) : IServiceHealthCheck
{
    public string ServiceId => "infrastructure";

    public async Task<IReadOnlyList<ServiceHealth>> CheckAsync(CancellationToken cancellationToken = default)
    {
        var configuration = await configurationLoader();
        if (configuration.Monitored.Count == 0)
        {
            return Array.Empty<ServiceHealth>();
        }

        var checks = configuration.Monitored.Select(service => ProbeAsync(service, cancellationToken));
        return await Task.WhenAll(checks);
    }

    private async Task<ServiceHealth> ProbeAsync(MonitoredService service, CancellationToken cancellationToken)
    {
        var now = DateTimeOffset.UtcNow.ToString("O");

        return service.Probe?.Trim().ToLowerInvariant() switch
        {
            "tcp" => await ProbeTcpAsync(service, cancellationToken),
            "http" => await ProbeHttpAsync(service, cancellationToken),
            _ => new ServiceHealth
            {
                ServiceId = service.ServiceId,
                DisplayName = string.IsNullOrWhiteSpace(service.DisplayName) ? service.ServiceId : service.DisplayName,
                Category = ServiceCategory.Infrastructure,
                Status = HealthState.Unknown,
                Target = null,
                ErrorMessage = $"No probe implemented for kind '{service.Probe}'. Declared but not checkable.",
                LastCheckedAt = now,
                Checkable = false
            }
        };
    }

    private async Task<ServiceHealth> ProbeTcpAsync(MonitoredService service, CancellationToken cancellationToken)
    {
        var target = $"{service.Host}:{service.Port}";
        var stopwatch = Stopwatch.StartNew();

        if (string.IsNullOrWhiteSpace(service.Host) || service.Port is null or <= 0 or > 65535)
        {
            return Failure(service, target, "tcp probe requires a host and a port between 1 and 65535.", HealthState.Unknown, checkable: false);
        }

        try
        {
            using var client = new TcpClient();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(tcpTimeout);

            await client.ConnectAsync(service.Host, service.Port.Value, timeout.Token);
            stopwatch.Stop();

            return new ServiceHealth
            {
                ServiceId = service.ServiceId,
                DisplayName = DisplayNameFor(service),
                Category = ServiceCategory.Infrastructure,
                Status = HealthState.Healthy,
                Target = target,
                LatencyMs = stopwatch.ElapsedMilliseconds,
                LastCheckedAt = DateTimeOffset.UtcNow.ToString("O")
            };
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return Failure(service, target, $"Connection timed out after {tcpTimeout.TotalSeconds:0.#}s.", HealthState.Unavailable);
        }
        catch (Exception ex) when (ex is SocketException or IOException or OperationCanceledException)
        {
            return Failure(service, target, ex is SocketException socket ? socket.Message : ex.Message, HealthState.Unavailable);
        }
    }

    private async Task<ServiceHealth> ProbeHttpAsync(MonitoredService service, CancellationToken cancellationToken)
    {
        var stopwatch = Stopwatch.StartNew();

        if (string.IsNullOrWhiteSpace(service.Url) || !Uri.TryCreate(service.Url, UriKind.Absolute, out _))
        {
            return Failure(service, service.Url, "http probe requires an absolute url.", HealthState.Unknown, checkable: false);
        }

        try
        {
            using var response = await httpClient.GetAsync(service.Url, cancellationToken);
            stopwatch.Stop();

            // Reachable but erroring is degraded, not down — the distinction
            // matters when deciding whether to restart or investigate.
            return response.IsSuccessStatusCode
                ? new ServiceHealth
                {
                    ServiceId = service.ServiceId,
                    DisplayName = DisplayNameFor(service),
                    Category = ServiceCategory.Infrastructure,
                    Status = HealthState.Healthy,
                    Target = service.Url,
                    LatencyMs = stopwatch.ElapsedMilliseconds,
                    LastCheckedAt = DateTimeOffset.UtcNow.ToString("O")
                }
                : Failure(service, service.Url, $"HTTP {(int)response.StatusCode} {response.ReasonPhrase}", HealthState.Degraded, stopwatch.ElapsedMilliseconds);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or OperationCanceledException)
        {
            var message = ex is TaskCanceledException or OperationCanceledException
                ? "Request timed out."
                : ex.InnerException?.Message ?? ex.Message;
            return Failure(service, service.Url, message, HealthState.Unavailable);
        }
    }

    private static string DisplayNameFor(MonitoredService service)
        => string.IsNullOrWhiteSpace(service.DisplayName) ? service.ServiceId : service.DisplayName;

    private static ServiceHealth Failure(
        MonitoredService service, string? target, string error, HealthState status,
        long? latencyMs = null, bool checkable = true) => new()
    {
        ServiceId = service.ServiceId,
        DisplayName = DisplayNameFor(service),
        Category = ServiceCategory.Infrastructure,
        Status = status,
        Target = target,
        LatencyMs = latencyMs,
        ErrorMessage = error,
        LastCheckedAt = DateTimeOffset.UtcNow.ToString("O"),
        Checkable = checkable
    };
}
