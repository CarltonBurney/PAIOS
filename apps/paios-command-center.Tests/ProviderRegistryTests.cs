using System.Net;
using Microsoft.Extensions.Logging.Abstractions;
using Paios.CommandCenter.Configuration;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Tests;

[TestClass]
public sealed class ProviderRegistryTests
{
    private string _configPath = null!;

    [TestInitialize]
    public void CreateTempConfig()
        => _configPath = Path.Combine(Path.GetTempPath(), $"paios-providers-{Guid.NewGuid():N}.json");

    [TestCleanup]
    public void RemoveTempConfig()
    {
        if (File.Exists(_configPath))
        {
            File.Delete(_configPath);
        }
    }

    private ProviderRegistry BuildRegistry(StubHttpMessageHandler handler)
    {
        var store = new ProviderConfigurationStore(_configPath, NullLogger<ProviderConfigurationStore>.Instance);
        var client = new HttpClient(handler);
        return new ProviderRegistry(
            store,
            new OllamaAdapter(client),
            new OpenAiCompatibleAdapter(client, _ => null),
            NullLogger<ProviderRegistry>.Instance);
    }

    [TestMethod]
    public async Task Missing_config_file_does_not_fail_and_still_auto_detects_ollama()
    {
        var registry = BuildRegistry(StubHttpMessageHandler.Throwing(new HttpRequestException("Connection refused")));

        var providers = await registry.GetProvidersAsync();

        Assert.AreEqual(1, providers.Count);
        Assert.AreEqual(ProviderRegistry.AutoDetectedOllamaId, providers[0].ProviderId);
        Assert.IsTrue(providers[0].AutoDetected);
    }

    [TestMethod]
    public async Task Unavailable_provider_is_listed_with_its_error_never_dropped()
    {
        var registry = BuildRegistry(StubHttpMessageHandler.Throwing(new HttpRequestException("Connection refused")));

        var providers = await registry.GetProvidersAsync();

        Assert.AreEqual(HealthState.Unavailable, providers[0].Status);
        Assert.AreEqual("Connection refused", providers[0].ErrorMessage);
    }

    [TestMethod]
    public async Task Registered_provider_persists_across_a_new_registry_instance()
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"object\":\"list\",\"data\":[]}");
        var registry = BuildRegistry(handler);

        var result = await registry.RegisterAsync(new ProviderRegistry.RegistrationRequest(
            "lmstudio-local", "openai_compatible", "http://localhost:1234/v1", null));

        Assert.IsTrue(result.Success);

        // A separate instance reads the same file — this is what proves the
        // registration survives a restart rather than living in memory.
        var reloaded = await BuildRegistry(handler).GetProvidersAsync();
        Assert.IsTrue(reloaded.Any(p => p.ProviderId == "lmstudio-local"));
    }

    [TestMethod]
    public async Task Duplicate_provider_id_is_rejected()
    {
        var registry = BuildRegistry(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"data\":[]}"));
        await registry.RegisterAsync(new ProviderRegistry.RegistrationRequest("dupe", "openai_compatible", "http://localhost:1234/v1", null));

        var second = await registry.RegisterAsync(new ProviderRegistry.RegistrationRequest("dupe", "openai_compatible", "http://localhost:9999/v1", null));

        Assert.IsFalse(second.Success);
        StringAssert.Contains(second.Error, "already exists");
    }

    [TestMethod]
    public async Task Reserved_auto_detected_id_cannot_be_claimed()
    {
        var registry = BuildRegistry(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"models\":[]}"));

        var result = await registry.RegisterAsync(new ProviderRegistry.RegistrationRequest(
            ProviderRegistry.AutoDetectedOllamaId, "ollama", "http://localhost:11434", null));

        Assert.IsFalse(result.Success);
    }

    [DataTestMethod]
    [DataRow("", "openai_compatible", "http://localhost:1234", "providerId is required.")]
    [DataRow("x", "nonsense", "http://localhost:1234", "providerType must be")]
    [DataRow("x", "openai_compatible", "not-a-uri", "endpoint must be an absolute URI.")]
    public async Task Invalid_registrations_are_rejected_with_a_reason(string id, string type, string endpoint, string expected)
    {
        var registry = BuildRegistry(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"data\":[]}"));

        var result = await registry.RegisterAsync(new ProviderRegistry.RegistrationRequest(id, type, endpoint, null));

        Assert.IsFalse(result.Success);
        StringAssert.Contains(result.Error, expected);
    }

    [TestMethod]
    public async Task Removing_a_configured_provider_succeeds_and_removing_an_absent_one_does_not()
    {
        var registry = BuildRegistry(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"data\":[]}"));
        await registry.RegisterAsync(new ProviderRegistry.RegistrationRequest("temp", "openai_compatible", "http://localhost:1234/v1", null));

        Assert.IsTrue(await registry.RemoveAsync("temp"));
        Assert.IsFalse(await registry.RemoveAsync("temp"));
    }

    [TestMethod]
    public async Task Auto_detected_provider_cannot_be_removed_via_the_registry()
    {
        var registry = BuildRegistry(StubHttpMessageHandler.Throwing(new HttpRequestException("refused")));

        Assert.IsFalse(await registry.RemoveAsync(ProviderRegistry.AutoDetectedOllamaId));
    }

    [TestMethod]
    public async Task Unknown_provider_id_yields_null_rather_than_throwing()
    {
        var registry = BuildRegistry(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"data\":[]}"));

        Assert.IsNull(await registry.GetProviderAsync("does-not-exist"));
        Assert.IsNull(await registry.GetModelsAsync("does-not-exist"));
    }

    [TestMethod]
    public async Task Malformed_config_file_falls_back_to_defaults_instead_of_failing_startup()
    {
        await File.WriteAllTextAsync(_configPath, "{ this is not json");
        var registry = BuildRegistry(StubHttpMessageHandler.Throwing(new HttpRequestException("refused")));

        var providers = await registry.GetProvidersAsync();

        Assert.AreEqual(1, providers.Count);
        Assert.AreEqual(ProviderRegistry.AutoDetectedOllamaId, providers[0].ProviderId);
    }

    [TestMethod]
    public async Task Api_key_is_never_written_to_the_config_file()
    {
        var registry = BuildRegistry(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"data\":[]}"));

        await registry.RegisterAsync(new ProviderRegistry.RegistrationRequest(
            "keyed", "openai_compatible", "http://localhost:1234/v1", "MY_KEY_ENV_VAR"));

        var contents = await File.ReadAllTextAsync(_configPath);
        StringAssert.Contains(contents, "MY_KEY_ENV_VAR");   // the variable name is stored
        Assert.IsFalse(contents.Contains("secret", StringComparison.OrdinalIgnoreCase));
    }
}
