using System.Text.Json;
using System.Text.Json.Serialization;

namespace Paios.CommandCenter.Configuration;

/// <summary>
/// On-disk shape of <c>config/governance.json</c> — where the control plane is and
/// how to authenticate to it.
///
/// <see cref="TokenEnvVar"/> holds the *name* of an environment variable, never a
/// token. This is the same rule the provider configuration follows for API keys,
/// and for the same reason: a config file lands in version control, a backup, and
/// a support bundle, and a credential in any of those is a credential leaked.
/// </summary>
public sealed class GovernanceConfiguration
{
    /// <summary>
    /// Whether to read the control plane at all. Disabled means the subsystem
    /// reports <c>not_implemented</c> rather than <c>unavailable</c> — the operator
    /// turned it off, which is not the same as it being broken.
    /// </summary>
    [JsonPropertyName("enabled")]
    public bool Enabled { get; set; } = true;

    /// <summary>Loopback by default, matching the kernel's own default bind.</summary>
    [JsonPropertyName("endpoint")]
    public string Endpoint { get; set; } = "http://127.0.0.1:8081";

    /// <summary>
    /// Name of the environment variable holding the bearer token — never the token.
    /// </summary>
    [JsonPropertyName("tokenEnvVar")]
    public string? TokenEnvVar { get; set; } = "PAIOS_HTTP_TOKEN";
}

/// <summary>
/// Reads <c>config/governance.json</c>. Read per request so an endpoint change
/// applies without a restart, and a malformed file degrades to defaults rather
/// than preventing boot — the same contract as the provider store.
/// </summary>
public sealed class GovernanceConfigurationStore(string configPath, ILogger<GovernanceConfigurationStore> logger)
{
    public string ConfigPath { get; } = configPath;

    public async Task<GovernanceConfiguration> LoadAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            if (!File.Exists(ConfigPath))
            {
                return new GovernanceConfiguration();
            }

            var json = await File.ReadAllTextAsync(ConfigPath, cancellationToken);
            return JsonSerializer.Deserialize<GovernanceConfiguration>(json) ?? new GovernanceConfiguration();
        }
        catch (Exception ex) when (ex is JsonException or IOException or UnauthorizedAccessException)
        {
            logger.LogError(ex, "Could not read governance config at {Path}; falling back to defaults.", ConfigPath);
            return new GovernanceConfiguration();
        }
    }
}
