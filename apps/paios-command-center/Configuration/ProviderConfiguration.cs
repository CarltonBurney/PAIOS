using System.Text.Json;
using System.Text.Json.Serialization;

namespace Paios.CommandCenter.Configuration;

/// <summary>
/// On-disk shape of <c>config/providers.json</c>. This file is the persistence
/// layer for configured providers — POST/DELETE write back to it so registrations
/// survive a restart.
/// </summary>
public sealed class ProviderConfiguration
{
    [JsonPropertyName("autoDetect")]
    public AutoDetectConfiguration AutoDetect { get; set; } = new();

    [JsonPropertyName("configured")]
    public List<ConfiguredProvider> Configured { get; set; } = new();
}

public sealed class AutoDetectConfiguration
{
    [JsonPropertyName("ollama")]
    public AutoDetectEntry Ollama { get; set; } = new()
    {
        Enabled = true,
        Endpoint = "http://localhost:11434"
    };
}

public sealed class AutoDetectEntry
{
    [JsonPropertyName("enabled")]
    public bool Enabled { get; set; }

    [JsonPropertyName("endpoint")]
    public string Endpoint { get; set; } = string.Empty;
}

public sealed class ConfiguredProvider
{
    [JsonPropertyName("providerId")]
    public string ProviderId { get; set; } = string.Empty;

    [JsonPropertyName("providerType")]
    public string ProviderType { get; set; } = string.Empty;

    [JsonPropertyName("endpoint")]
    public string Endpoint { get; set; } = string.Empty;

    /// <summary>
    /// Name of the environment variable holding the key — never the key itself.
    /// </summary>
    [JsonPropertyName("apiKeyEnvVar")]
    public string? ApiKeyEnvVar { get; set; }
}

/// <summary>
/// Reads and writes <c>config/providers.json</c>. A missing or unreadable file is
/// not fatal: the app starts with defaults so a bad config never prevents boot.
/// </summary>
public sealed class ProviderConfigurationStore(string configPath, ILogger<ProviderConfigurationStore> logger)
{
    private static readonly JsonSerializerOptions WriteOptions = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    private readonly SemaphoreSlim _gate = new(1, 1);

    public string ConfigPath { get; } = configPath;

    public async Task<ProviderConfiguration> LoadAsync(CancellationToken cancellationToken = default)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            if (!File.Exists(ConfigPath))
            {
                logger.LogInformation("No provider config at {Path}; using defaults.", ConfigPath);
                return new ProviderConfiguration();
            }

            var json = await File.ReadAllTextAsync(ConfigPath, cancellationToken);
            return JsonSerializer.Deserialize<ProviderConfiguration>(json) ?? new ProviderConfiguration();
        }
        catch (Exception ex) when (ex is JsonException or IOException or UnauthorizedAccessException)
        {
            // A malformed config must not stop the app from starting — it degrades
            // to defaults and says so, which is recoverable without a redeploy.
            logger.LogError(ex, "Could not read provider config at {Path}; falling back to defaults.", ConfigPath);
            return new ProviderConfiguration();
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task SaveAsync(ProviderConfiguration configuration, CancellationToken cancellationToken = default)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            var directory = Path.GetDirectoryName(ConfigPath);
            if (!string.IsNullOrEmpty(directory))
            {
                Directory.CreateDirectory(directory);
            }

            var json = JsonSerializer.Serialize(configuration, WriteOptions);
            await File.WriteAllTextAsync(ConfigPath, json, cancellationToken);
        }
        finally
        {
            _gate.Release();
        }
    }
}
