using System.Diagnostics;
using System.Net.Http.Headers;
using System.Text.Json;

namespace Paios.CommandCenter.Providers;

/// <summary>
/// Talks to any endpoint exposing the OpenAI <c>/v1/models</c> shape — LM Studio,
/// LocalAI, vLLM, text-generation-webui. No key is sent unless one is configured,
/// because local servers generally reject unexpected auth headers.
/// </summary>
public sealed class OpenAiCompatibleAdapter(HttpClient httpClient, Func<string, string?> apiKeyResolver)
    : IModelProviderAdapter
{
    public ProviderType ProviderType => ProviderType.OpenaiCompatible;

    public Task<HealthResult> CheckHealthAsync(string endpoint, CancellationToken cancellationToken = default)
        => CheckHealthAsync(endpoint, apiKeyEnvVar: null, cancellationToken);

    public async Task<HealthResult> CheckHealthAsync(string endpoint, string? apiKeyEnvVar, CancellationToken cancellationToken = default)
    {
        var stopwatch = Stopwatch.StartNew();
        try
        {
            using var request = BuildRequest(endpoint, apiKeyEnvVar);
            using var response = await httpClient.SendAsync(request, cancellationToken);
            stopwatch.Stop();

            if (!response.IsSuccessStatusCode)
            {
                return new HealthResult(HealthState.Degraded, stopwatch.ElapsedMilliseconds,
                    $"HTTP {(int)response.StatusCode} {response.ReasonPhrase}");
            }

            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            if (!TryParseModels(body, out _, out var parseError))
            {
                return new HealthResult(HealthState.Degraded, stopwatch.ElapsedMilliseconds, parseError);
            }

            return new HealthResult(HealthState.Healthy, stopwatch.ElapsedMilliseconds, null);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or OperationCanceledException)
        {
            stopwatch.Stop();
            return new HealthResult(HealthState.Unavailable, null, Describe(ex));
        }
    }

    public Task<ModelListResult> ListModelsAsync(string providerId, string endpoint, CancellationToken cancellationToken = default)
        => ListModelsAsync(providerId, endpoint, apiKeyEnvVar: null, cancellationToken);

    public async Task<ModelListResult> ListModelsAsync(string providerId, string endpoint, string? apiKeyEnvVar, CancellationToken cancellationToken = default)
    {
        try
        {
            using var request = BuildRequest(endpoint, apiKeyEnvVar);
            using var response = await httpClient.SendAsync(request, cancellationToken);

            if (!response.IsSuccessStatusCode)
            {
                return new ModelListResult(HealthState.Degraded, Array.Empty<ModelRecord>(),
                    $"HTTP {(int)response.StatusCode} {response.ReasonPhrase}");
            }

            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            if (!TryParseModels(body, out var ids, out var parseError))
            {
                return new ModelListResult(HealthState.Degraded, Array.Empty<ModelRecord>(), parseError);
            }

            var seenAt = DateTimeOffset.UtcNow.ToString("O");
            var records = ids.Select(id => new ModelRecord
            {
                ProviderId = providerId,
                ModelId = id,
                ModelName = id,
                Capabilities = new[] { "chat" },
                Availability = HealthState.Healthy,
                LastSeenAt = seenAt
            }).ToList();

            return new ModelListResult(HealthState.Healthy, records, null);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or OperationCanceledException)
        {
            return new ModelListResult(HealthState.Unavailable, Array.Empty<ModelRecord>(), Describe(ex));
        }
    }

    private HttpRequestMessage BuildRequest(string endpoint, string? apiKeyEnvVar)
    {
        var request = new HttpRequestMessage(HttpMethod.Get, ModelsUri(endpoint));

        // Only attach auth when the config named an env var AND that var is set.
        // The key itself is never stored in config — only the variable's name.
        if (!string.IsNullOrWhiteSpace(apiKeyEnvVar))
        {
            var key = apiKeyResolver(apiKeyEnvVar);
            if (!string.IsNullOrWhiteSpace(key))
            {
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", key);
            }
        }

        return request;
    }

    /// <summary>
    /// Endpoints are configured either with or without the <c>/v1</c> suffix;
    /// both are accepted so a copied LM Studio URL works as-is.
    /// </summary>
    private static Uri ModelsUri(string endpoint)
    {
        var trimmed = endpoint.TrimEnd('/');
        return trimmed.EndsWith("/v1", StringComparison.OrdinalIgnoreCase)
            ? new Uri(trimmed + "/models")
            : new Uri(trimmed + "/v1/models");
    }

    private static bool TryParseModels(string body, out List<string> ids, out string? error)
    {
        ids = new List<string>();
        error = null;
        try
        {
            using var document = JsonDocument.Parse(body);
            if (!document.RootElement.TryGetProperty("data", out var dataElement)
                || dataElement.ValueKind != JsonValueKind.Array)
            {
                error = "Response did not contain a 'data' array.";
                return false;
            }

            foreach (var element in dataElement.EnumerateArray())
            {
                if (!element.TryGetProperty("id", out var idElement)
                    || idElement.ValueKind != JsonValueKind.String)
                {
                    error = "A model entry was missing a string 'id'.";
                    return false;
                }

                ids.Add(idElement.GetString()!);
            }

            return true;
        }
        catch (JsonException ex)
        {
            error = $"Malformed JSON: {ex.Message}";
            return false;
        }
    }

    private static string Describe(Exception ex) =>
        ex is TaskCanceledException or OperationCanceledException
            ? "Request timed out."
            : ex.InnerException?.Message ?? ex.Message;
}
