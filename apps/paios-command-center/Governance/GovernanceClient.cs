using System.Diagnostics;
using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Paios.CommandCenter.Configuration;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Governance;

/// <summary>
/// Client for the Python control plane's HTTP surface.
///
/// Follows the Phase 1 adapter contract exactly: no method here throws for a
/// network, protocol, or authorization failure. Each returns a
/// <see cref="GovernanceResult{T}"/> carrying a <see cref="HealthState"/>, because
/// "the governance kernel is down" is a state the Command Center must be able to
/// display, not an exception that blanks a page.
///
/// The distinction between unavailable and degraded is deliberate and load-bearing:
///
/// <list type="bullet">
/// <item>nothing listening, DNS failure, timeout — <c>unavailable</c></item>
/// <item>answering but a component failed to load (503) — <c>degraded</c></item>
/// <item>answering but rejecting our credential (401) — <c>degraded</c></item>
/// <item>answering with a body that does not parse — <c>degraded</c></item>
/// </list>
///
/// An operator responds differently to "start the kernel" than to "fix the token",
/// so collapsing both into one state would cost them a diagnosis.
///
/// SECRET HANDLING — the bearer token is resolved from the environment by name at
/// request time and never stored in configuration, never logged, and never
/// returned. Config holds <c>tokenEnvVar</c>, matching the Phase 1 rule that only
/// the name of an environment variable is persisted.
/// </summary>
public sealed class GovernanceClient(
    HttpClient httpClient,
    Func<CancellationToken, Task<GovernanceConfiguration>> configurationReader,
    Func<string, string?> environmentReader,
    ILogger<GovernanceClient> logger)
{
    private static readonly JsonSerializerOptions ReadOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };

    /// <summary>The configured endpoint, for display. Null when disabled.</summary>
    public async Task<string?> GetEndpointAsync(CancellationToken cancellationToken = default)
    {
        var configuration = await ReadConfigurationAsync(cancellationToken);
        return configuration.Enabled ? configuration.Endpoint : null;
    }

    public async Task<bool> IsEnabledAsync(CancellationToken cancellationToken = default)
        => (await ReadConfigurationAsync(cancellationToken)).Enabled;

    /// <summary>
    /// The kernel's self-check. Unauthenticated by design on the kernel side, so
    /// this works even when the token is wrong — which is what lets a misconfigured
    /// credential be reported as degraded rather than as an outage.
    /// </summary>
    public Task<GovernanceResult<GovernanceHealthPayload>> GetHealthAsync(
        CancellationToken cancellationToken = default)
        => GetAsync<GovernanceHealthPayload>("/health", authenticate: false, cancellationToken);

    public Task<GovernanceResult<GovernanceStatus>> GetStatusAsync(
        CancellationToken cancellationToken = default)
        => GetAsync<GovernanceStatus>("/api/governance/status", authenticate: true, cancellationToken);

    public Task<GovernanceResult<GovernanceToolList>> GetToolsAsync(
        CancellationToken cancellationToken = default)
        => GetAsync<GovernanceToolList>("/api/governance/tools", authenticate: true, cancellationToken);

    /// <summary>
    /// Submit a request for a governance decision.
    ///
    /// Only content and metadata are sent. Identity is deliberately not a
    /// parameter: the kernel resolves entitlements from the credential and ignores
    /// anything a body claims, so offering an identity argument here would imply a
    /// control the Command Center does not have.
    /// </summary>
    public async Task<GovernanceResult<GovernanceDecision>> SubmitAsync(
        string content,
        IReadOnlyDictionary<string, object?>? metadata = null,
        string? correlationId = null,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(content))
        {
            return GovernanceResult<GovernanceDecision>.Degraded("content must not be empty");
        }

        var configuration = await ReadConfigurationAsync(cancellationToken);
        if (!configuration.Enabled)
        {
            return GovernanceResult<GovernanceDecision>.Degraded("the control plane is disabled in configuration");
        }

        var body = new Dictionary<string, object?> { ["content"] = content };
        if (metadata is { Count: > 0 })
        {
            body["metadata"] = metadata;
        }
        if (!string.IsNullOrWhiteSpace(correlationId))
        {
            body["correlation_id"] = correlationId;
        }

        try
        {
            using var request = new HttpRequestMessage(
                HttpMethod.Post, Combine(configuration.Endpoint, "/api/governance/requests"))
            {
                Content = new StringContent(
                    JsonSerializer.Serialize(body), Encoding.UTF8, "application/json")
            };
            Authenticate(request, configuration);

            using var response = await httpClient.SendAsync(request, cancellationToken);
            return await InterpretAsync<GovernanceDecision>(response, cancellationToken);
        }
        catch (Exception ex) when (IsTransportFailure(ex))
        {
            return GovernanceResult<GovernanceDecision>.Unavailable(Describe(ex, configuration.Endpoint));
        }
    }

    private async Task<GovernanceResult<T>> GetAsync<T>(
        string path, bool authenticate, CancellationToken cancellationToken)
        where T : class
    {
        var configuration = await ReadConfigurationAsync(cancellationToken);
        if (!configuration.Enabled)
        {
            return GovernanceResult<T>.Degraded("the control plane is disabled in configuration");
        }

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, Combine(configuration.Endpoint, path));
            if (authenticate)
            {
                Authenticate(request, configuration);
            }

            using var response = await httpClient.SendAsync(request, cancellationToken);
            return await InterpretAsync<T>(response, cancellationToken);
        }
        catch (Exception ex) when (IsTransportFailure(ex))
        {
            return GovernanceResult<T>.Unavailable(Describe(ex, configuration.Endpoint));
        }
    }

    /// <summary>
    /// Turn an HTTP response into a state. A 503 still carries a usable body — the
    /// kernel reports which component failed — so the payload is kept and paired
    /// with <c>degraded</c> rather than discarded.
    /// </summary>
    private async Task<GovernanceResult<T>> InterpretAsync<T>(
        HttpResponseMessage response, CancellationToken cancellationToken)
        where T : class
    {
        if (response.StatusCode == HttpStatusCode.Unauthorized)
        {
            return GovernanceResult<T>.Degraded(
                "the control plane rejected the configured credential (401); "
                + "check the environment variable named by tokenEnvVar");
        }

        T? payload = null;
        string? parseError = null;
        try
        {
            payload = await response.Content.ReadFromJsonAsync<T>(ReadOptions, cancellationToken);
        }
        catch (Exception ex) when (ex is JsonException or NotSupportedException)
        {
            parseError = ex.Message;
        }

        if (parseError is not null)
        {
            return GovernanceResult<T>.Degraded(
                $"the control plane answered {(int)response.StatusCode} with a body that did not parse: {parseError}");
        }

        if (payload is null)
        {
            return GovernanceResult<T>.Degraded(
                $"the control plane answered {(int)response.StatusCode} with an empty body");
        }

        if (response.StatusCode == HttpStatusCode.ServiceUnavailable)
        {
            // The kernel's own word for "I am up but not well". Keep the body.
            return GovernanceResult<T>.Degraded(
                "the control plane reports itself degraded (503)", payload);
        }

        if (!response.IsSuccessStatusCode)
        {
            return GovernanceResult<T>.Degraded(
                $"the control plane answered {(int)response.StatusCode}", payload);
        }

        return GovernanceResult<T>.Ok(payload);
    }

    private void Authenticate(HttpRequestMessage request, GovernanceConfiguration configuration)
    {
        if (string.IsNullOrWhiteSpace(configuration.TokenEnvVar))
        {
            return;
        }

        var token = environmentReader(configuration.TokenEnvVar);
        if (string.IsNullOrWhiteSpace(token))
        {
            // Named but unset. Send nothing rather than an empty Bearer header, so
            // the kernel's 401 means "no credential" and not "a malformed one".
            logger.LogWarning(
                "Environment variable '{Name}' is named as the control plane token but is not set.",
                configuration.TokenEnvVar);
            return;
        }

        request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
    }

    private async Task<GovernanceConfiguration> ReadConfigurationAsync(CancellationToken cancellationToken)
    {
        try
        {
            return await configurationReader(cancellationToken);
        }
        catch (Exception ex)
        {
            // Deliberately broad. A config read failure degrades governance to a
            // reported state; it does not take down whatever else is being served.
            logger.LogError(ex, "Could not read governance configuration; treating the control plane as disabled.");
            return new GovernanceConfiguration { Enabled = false };
        }
    }

    private static string Combine(string endpoint, string path)
        => $"{endpoint.TrimEnd('/')}{path}";

    /// <summary>
    /// Failures that mean "could not complete a round trip". Anything outside this
    /// set is a bug in this client and is left to propagate rather than being
    /// laundered into a health state.
    /// </summary>
    private static bool IsTransportFailure(Exception ex)
        => ex is HttpRequestException or TaskCanceledException or OperationCanceledException
            or UriFormatException or InvalidOperationException;

    private static string Describe(Exception ex, string endpoint) => ex switch
    {
        TaskCanceledException or OperationCanceledException =>
            $"the control plane at {endpoint} did not answer before the timeout",
        UriFormatException or InvalidOperationException =>
            $"'{endpoint}' is not a usable control plane endpoint: {ex.Message}",
        _ => $"the control plane at {endpoint} is unreachable: {Innermost(ex).Message}"
    };

    private static Exception Innermost(Exception ex)
    {
        var current = ex;
        while (current.InnerException is not null)
        {
            current = current.InnerException;
        }
        return current;
    }

    /// <summary>Round-trip latency, for the health record.</summary>
    public static async Task<(GovernanceResult<GovernanceHealthPayload> Result, long LatencyMs)> TimedHealthAsync(
        GovernanceClient client, CancellationToken cancellationToken = default)
    {
        var stopwatch = Stopwatch.StartNew();
        var result = await client.GetHealthAsync(cancellationToken);
        stopwatch.Stop();
        return (result, stopwatch.ElapsedMilliseconds);
    }
}
