using System.Diagnostics;
using System.Text.Json;

namespace Paios.CommandCenter.Providers;

/// <summary>
/// Talks to Ollama's native API. Health and model listing share one endpoint
/// (<c>/api/tags</c>), so a healthy check already proves the listing shape.
/// </summary>
public sealed class OllamaAdapter(HttpClient httpClient) : IModelProviderAdapter
{
    public ProviderType ProviderType => ProviderType.Ollama;

    public async Task<HealthResult> CheckHealthAsync(string endpoint, CancellationToken cancellationToken = default)
    {
        var stopwatch = Stopwatch.StartNew();
        try
        {
            using var response = await httpClient.GetAsync(TagsUri(endpoint), cancellationToken);
            stopwatch.Stop();

            if (!response.IsSuccessStatusCode)
            {
                return new HealthResult(HealthState.Degraded, stopwatch.ElapsedMilliseconds,
                    $"HTTP {(int)response.StatusCode} {response.ReasonPhrase}");
            }

            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            if (!TryParseTags(body, out _, out var parseError))
            {
                return new HealthResult(HealthState.Degraded, stopwatch.ElapsedMilliseconds, parseError);
            }

            return new HealthResult(HealthState.Healthy, stopwatch.ElapsedMilliseconds, null);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or OperationCanceledException)
        {
            stopwatch.Stop();
            // Connection refused, DNS failure, or timeout: the service is not
            // there. This is an expected state, never an exception to surface.
            return new HealthResult(HealthState.Unavailable, null, Describe(ex));
        }
    }

    public async Task<ModelListResult> ListModelsAsync(string providerId, string endpoint, CancellationToken cancellationToken = default)
    {
        try
        {
            using var response = await httpClient.GetAsync(TagsUri(endpoint), cancellationToken);

            if (!response.IsSuccessStatusCode)
            {
                return new ModelListResult(HealthState.Degraded, Array.Empty<ModelRecord>(),
                    $"HTTP {(int)response.StatusCode} {response.ReasonPhrase}");
            }

            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            if (!TryParseTags(body, out var models, out var parseError))
            {
                return new ModelListResult(HealthState.Degraded, Array.Empty<ModelRecord>(), parseError);
            }

            var seenAt = DateTimeOffset.UtcNow.ToString("O");
            var records = models.Select(model => new ModelRecord
            {
                ProviderId = providerId,
                ModelId = model.Name,
                ModelName = model.Name,
                Capabilities = new[] { "chat" },
                Availability = HealthState.Healthy,
                ContextWindow = null,
                SizeBytes = model.Size,
                LastSeenAt = seenAt
            }).ToList();

            // Zero models on a reachable provider is a valid healthy state.
            return new ModelListResult(HealthState.Healthy, records, null);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or OperationCanceledException)
        {
            return new ModelListResult(HealthState.Unavailable, Array.Empty<ModelRecord>(), Describe(ex));
        }
    }

    private static Uri TagsUri(string endpoint) => new(new Uri(endpoint.TrimEnd('/') + "/"), "api/tags");

    private readonly record struct OllamaModel(string Name, long? Size);

    private static bool TryParseTags(string body, out List<OllamaModel> models, out string? error)
    {
        models = new List<OllamaModel>();
        error = null;
        try
        {
            using var document = JsonDocument.Parse(body);
            if (!document.RootElement.TryGetProperty("models", out var modelsElement)
                || modelsElement.ValueKind != JsonValueKind.Array)
            {
                error = "Response did not contain a 'models' array.";
                return false;
            }

            foreach (var element in modelsElement.EnumerateArray())
            {
                if (!element.TryGetProperty("name", out var nameElement)
                    || nameElement.ValueKind != JsonValueKind.String)
                {
                    error = "A model entry was missing a string 'name'.";
                    return false;
                }

                long? size = element.TryGetProperty("size", out var sizeElement)
                    && sizeElement.ValueKind == JsonValueKind.Number
                    && sizeElement.TryGetInt64(out var parsedSize)
                        ? parsedSize
                        : null;

                models.Add(new OllamaModel(nameElement.GetString()!, size));
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
