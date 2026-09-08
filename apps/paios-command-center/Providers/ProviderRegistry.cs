using Paios.CommandCenter.Configuration;

namespace Paios.CommandCenter.Providers;

/// <summary>
/// The single source of provider truth for the API. Merges auto-detected local
/// defaults with explicitly configured providers, runs health checks through the
/// adapters, and persists registrations back to the config file.
/// </summary>
public sealed class ProviderRegistry(
    ProviderConfigurationStore store,
    OllamaAdapter ollamaAdapter,
    OpenAiCompatibleAdapter openAiAdapter,
    ILogger<ProviderRegistry> logger)
{
    public const string AutoDetectedOllamaId = "ollama-local";

    /// <summary>
    /// Every known provider with a freshly checked health state. A provider that
    /// fails its check is still returned, carrying the failure — never dropped.
    /// </summary>
    public async Task<IReadOnlyList<ModelProvider>> GetProvidersAsync(CancellationToken cancellationToken = default)
    {
        var descriptors = await GetDescriptorsAsync(cancellationToken);

        var checks = descriptors.Select(async descriptor =>
        {
            var health = await CheckAsync(descriptor, cancellationToken);
            return ToProvider(descriptor, health);
        });

        return await Task.WhenAll(checks);
    }

    public async Task<ModelProvider?> GetProviderAsync(string providerId, CancellationToken cancellationToken = default)
    {
        var descriptor = (await GetDescriptorsAsync(cancellationToken))
            .FirstOrDefault(d => string.Equals(d.ProviderId, providerId, StringComparison.OrdinalIgnoreCase));

        if (descriptor is null)
        {
            return null;
        }

        var health = await CheckAsync(descriptor, cancellationToken);
        return ToProvider(descriptor, health);
    }

    public async Task<ModelListResult?> GetModelsAsync(string providerId, CancellationToken cancellationToken = default)
    {
        var descriptor = (await GetDescriptorsAsync(cancellationToken))
            .FirstOrDefault(d => string.Equals(d.ProviderId, providerId, StringComparison.OrdinalIgnoreCase));

        if (descriptor is null)
        {
            return null;
        }

        return descriptor.ProviderType switch
        {
            ProviderType.Ollama => await ollamaAdapter.ListModelsAsync(descriptor.ProviderId, descriptor.Endpoint, cancellationToken),
            ProviderType.OpenaiCompatible => await openAiAdapter.ListModelsAsync(descriptor.ProviderId, descriptor.Endpoint, descriptor.ApiKeyEnvVar, cancellationToken),
            _ => new ModelListResult(HealthState.Unknown, Array.Empty<ModelRecord>(), "No adapter for this provider type.")
        };
    }

    public sealed record RegistrationRequest(string ProviderId, string ProviderType, string Endpoint, string? ApiKeyEnvVar);

    public sealed record RegistrationResult(bool Success, string? Error, ModelProvider? Provider);

    public async Task<RegistrationResult> RegisterAsync(RegistrationRequest request, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(request.ProviderId))
        {
            return new RegistrationResult(false, "providerId is required.", null);
        }

        if (!TryParseProviderType(request.ProviderType, out var providerType))
        {
            return new RegistrationResult(false, "providerType must be 'ollama', 'openai_compatible', or 'other'.", null);
        }

        if (!Uri.TryCreate(request.Endpoint, UriKind.Absolute, out _))
        {
            return new RegistrationResult(false, "endpoint must be an absolute URI.", null);
        }

        var configuration = await store.LoadAsync(cancellationToken);

        if (configuration.Configured.Any(p => string.Equals(p.ProviderId, request.ProviderId, StringComparison.OrdinalIgnoreCase))
            || string.Equals(request.ProviderId, AutoDetectedOllamaId, StringComparison.OrdinalIgnoreCase))
        {
            return new RegistrationResult(false, $"A provider with id '{request.ProviderId}' already exists.", null);
        }

        configuration.Configured.Add(new ConfiguredProvider
        {
            ProviderId = request.ProviderId,
            ProviderType = request.ProviderType,
            Endpoint = request.Endpoint,
            ApiKeyEnvVar = request.ApiKeyEnvVar
        });

        await store.SaveAsync(configuration, cancellationToken);
        logger.LogInformation("Registered provider {ProviderId} ({ProviderType}).", request.ProviderId, request.ProviderType);

        var descriptor = new ProviderDescriptor(request.ProviderId, providerType, request.Endpoint, request.ApiKeyEnvVar, AutoDetected: false);
        var health = await CheckAsync(descriptor, cancellationToken);
        return new RegistrationResult(true, null, ToProvider(descriptor, health));
    }

    public async Task<bool> RemoveAsync(string providerId, CancellationToken cancellationToken = default)
    {
        var configuration = await store.LoadAsync(cancellationToken);
        var removed = configuration.Configured.RemoveAll(
            p => string.Equals(p.ProviderId, providerId, StringComparison.OrdinalIgnoreCase));

        if (removed == 0)
        {
            return false;
        }

        await store.SaveAsync(configuration, cancellationToken);
        logger.LogInformation("Removed provider {ProviderId}.", providerId);
        return true;
    }

    internal sealed record ProviderDescriptor(
        string ProviderId,
        ProviderType ProviderType,
        string Endpoint,
        string? ApiKeyEnvVar,
        bool AutoDetected);

    private async Task<List<ProviderDescriptor>> GetDescriptorsAsync(CancellationToken cancellationToken)
    {
        var configuration = await store.LoadAsync(cancellationToken);
        var descriptors = new List<ProviderDescriptor>();

        // Auto-detected local Ollama comes first, and is only skipped when an
        // explicit provider already claims that id.
        if (configuration.AutoDetect.Ollama.Enabled
            && !string.IsNullOrWhiteSpace(configuration.AutoDetect.Ollama.Endpoint)
            && !configuration.Configured.Any(p => string.Equals(p.ProviderId, AutoDetectedOllamaId, StringComparison.OrdinalIgnoreCase)))
        {
            descriptors.Add(new ProviderDescriptor(
                AutoDetectedOllamaId,
                ProviderType.Ollama,
                configuration.AutoDetect.Ollama.Endpoint,
                ApiKeyEnvVar: null,
                AutoDetected: true));
        }

        foreach (var configured in configuration.Configured)
        {
            if (!TryParseProviderType(configured.ProviderType, out var providerType))
            {
                logger.LogWarning("Provider {ProviderId} has unrecognized type '{Type}'; treating as 'other'.",
                    configured.ProviderId, configured.ProviderType);
                providerType = ProviderType.Other;
            }

            descriptors.Add(new ProviderDescriptor(
                configured.ProviderId,
                providerType,
                configured.Endpoint,
                configured.ApiKeyEnvVar,
                AutoDetected: false));
        }

        return descriptors;
    }

    private async Task<HealthResult> CheckAsync(ProviderDescriptor descriptor, CancellationToken cancellationToken)
        => descriptor.ProviderType switch
        {
            ProviderType.Ollama => await ollamaAdapter.CheckHealthAsync(descriptor.Endpoint, cancellationToken),
            ProviderType.OpenaiCompatible => await openAiAdapter.CheckHealthAsync(descriptor.Endpoint, descriptor.ApiKeyEnvVar, cancellationToken),
            _ => new HealthResult(HealthState.Unknown, null, "No adapter for this provider type.")
        };

    private static ModelProvider ToProvider(ProviderDescriptor descriptor, HealthResult health) => new()
    {
        ProviderId = descriptor.ProviderId,
        ProviderType = descriptor.ProviderType,
        Endpoint = descriptor.Endpoint,
        Status = health.Status,
        LatencyMs = health.LatencyMs,
        ErrorMessage = health.ErrorMessage,
        LastCheckedAt = DateTimeOffset.UtcNow.ToString("O"),
        AutoDetected = descriptor.AutoDetected
    };

    private static bool TryParseProviderType(string value, out ProviderType providerType)
    {
        switch (value?.Trim().ToLowerInvariant())
        {
            case "ollama":
                providerType = ProviderType.Ollama;
                return true;
            case "openai_compatible":
                providerType = ProviderType.OpenaiCompatible;
                return true;
            case "other":
                providerType = ProviderType.Other;
                return true;
            default:
                providerType = ProviderType.Other;
                return false;
        }
    }
}
