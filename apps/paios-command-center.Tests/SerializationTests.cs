using System.Text.Json;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Tests;

/// <summary>
/// The wire format is a contract with the shared specification and with the
/// workspace JavaScript, which keys CSS classes off these exact strings.
/// </summary>
[TestClass]
public sealed class SerializationTests
{
    [DataTestMethod]
    [DataRow(ProviderType.Ollama, "\"ollama\"")]
    [DataRow(ProviderType.OpenaiCompatible, "\"openai_compatible\"")]
    [DataRow(ProviderType.Other, "\"other\"")]
    public void Provider_type_serializes_to_the_specified_string(ProviderType value, string expected)
        => Assert.AreEqual(expected, JsonSerializer.Serialize(value));

    [DataTestMethod]
    [DataRow(HealthState.Healthy, "\"healthy\"")]
    [DataRow(HealthState.Degraded, "\"degraded\"")]
    [DataRow(HealthState.Unavailable, "\"unavailable\"")]
    [DataRow(HealthState.Unknown, "\"unknown\"")]
    public void Health_state_serializes_to_the_specified_string(HealthState value, string expected)
        => Assert.AreEqual(expected, JsonSerializer.Serialize(value));

    [TestMethod]
    public void Health_state_round_trips()
    {
        var json = JsonSerializer.Serialize(HealthState.Degraded);
        Assert.AreEqual(HealthState.Degraded, JsonSerializer.Deserialize<HealthState>(json));
    }

    [TestMethod]
    public void Unknown_is_the_default_health_state_not_healthy()
    {
        var provider = new ModelProvider
        {
            ProviderId = "x",
            ProviderType = ProviderType.Ollama,
            Endpoint = "http://localhost:11434"
        };

        Assert.AreEqual(HealthState.Unknown, provider.Status);
    }
}
