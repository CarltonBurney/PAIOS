using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Operations;

/// <summary>
/// Surfaces every model provider as an operations service, reusing the Phase 1
/// registry so provider health has exactly one implementation rather than a
/// second one that could disagree with the Local LLMs workspace.
/// </summary>
public sealed class ProviderHealthCheck(ProviderRegistry registry) : IServiceHealthCheck
{
    public string ServiceId => "model-providers";

    public async Task<IReadOnlyList<ServiceHealth>> CheckAsync(CancellationToken cancellationToken = default)
    {
        var providers = await registry.GetProvidersAsync(cancellationToken);

        return providers.Select(provider => new ServiceHealth
        {
            ServiceId = $"provider:{provider.ProviderId}",
            DisplayName = provider.ProviderId,
            Category = ServiceCategory.Provider,
            Status = provider.Status,
            Target = provider.Endpoint,
            LatencyMs = provider.LatencyMs,
            ErrorMessage = provider.ErrorMessage,
            LastCheckedAt = provider.LastCheckedAt ?? DateTimeOffset.UtcNow.ToString("O")
        }).ToList();
    }
}
