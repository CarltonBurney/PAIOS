using System.Net;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Tests;

[TestClass]
public sealed class OpenAiCompatibleAdapterTests
{
    private const string ModelsBody = """
    {"object":"list","data":[{"id":"llama-3.1-8b-instruct","object":"model"},{"id":"qwen2.5-coder-7b","object":"model"}]}
    """;

    private static OpenAiCompatibleAdapter Adapter(
        StubHttpMessageHandler handler,
        Func<string, string?>? keyResolver = null)
        => new(new HttpClient(handler), keyResolver ?? (_ => null));

    [TestMethod]
    public async Task Reachable_endpoint_reports_healthy()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.OK, ModelsBody));

        var health = await adapter.CheckHealthAsync("http://localhost:1234/v1");

        Assert.AreEqual(HealthState.Healthy, health.Status);
        Assert.IsNull(health.ErrorMessage);
    }

    [TestMethod]
    public async Task Reachable_endpoint_enumerates_models_from_the_provider()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.OK, ModelsBody));

        var result = await adapter.ListModelsAsync("lmstudio-local", "http://localhost:1234/v1");

        Assert.AreEqual(HealthState.Healthy, result.Status);
        CollectionAssert.AreEqual(
            new[] { "llama-3.1-8b-instruct", "qwen2.5-coder-7b" },
            result.Models.Select(m => m.ModelId).ToArray());
    }

    [TestMethod]
    public async Task Unreachable_endpoint_reports_unavailable()
    {
        var adapter = Adapter(StubHttpMessageHandler.Throwing(new HttpRequestException("Connection refused")));

        var health = await adapter.CheckHealthAsync("http://localhost:1234/v1");

        Assert.AreEqual(HealthState.Unavailable, health.Status);
        Assert.AreEqual("Connection refused", health.ErrorMessage);
    }

    [TestMethod]
    public async Task Malformed_json_reports_degraded()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"data\":[ BROKEN"));

        var result = await adapter.ListModelsAsync("lmstudio-local", "http://localhost:1234/v1");

        Assert.AreEqual(HealthState.Degraded, result.Status);
        StringAssert.Contains(result.ErrorMessage, "Malformed JSON");
        Assert.AreEqual(0, result.Models.Count);
    }

    [TestMethod]
    public async Task Healthy_provider_with_zero_models_is_valid()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"object\":\"list\",\"data\":[]}"));

        var result = await adapter.ListModelsAsync("lmstudio-local", "http://localhost:1234/v1");

        Assert.AreEqual(HealthState.Healthy, result.Status);
        Assert.AreEqual(0, result.Models.Count);
        Assert.IsNull(result.ErrorMessage);
    }

    [TestMethod]
    public async Task No_auth_header_is_sent_when_no_key_is_configured()
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, ModelsBody);
        var adapter = Adapter(handler);

        await adapter.CheckHealthAsync("http://localhost:1234/v1", apiKeyEnvVar: null);

        Assert.IsNull(handler.Requests.Single().Headers.Authorization);
    }

    [TestMethod]
    public async Task Auth_header_is_sent_only_when_the_named_env_var_resolves()
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, ModelsBody);
        var adapter = Adapter(handler, name => name == "MY_KEY" ? "secret-value" : null);

        await adapter.CheckHealthAsync("http://localhost:1234/v1", apiKeyEnvVar: "MY_KEY");

        var auth = handler.Requests.Single().Headers.Authorization;
        Assert.IsNotNull(auth);
        Assert.AreEqual("Bearer", auth.Scheme);
        Assert.AreEqual("secret-value", auth.Parameter);
    }

    [TestMethod]
    public async Task Missing_env_var_sends_no_auth_rather_than_an_empty_bearer()
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, ModelsBody);
        var adapter = Adapter(handler, _ => null);

        await adapter.CheckHealthAsync("http://localhost:1234/v1", apiKeyEnvVar: "UNSET_VAR");

        Assert.IsNull(handler.Requests.Single().Headers.Authorization);
    }

    [DataTestMethod]
    [DataRow("http://localhost:1234", "http://localhost:1234/v1/models")]
    [DataRow("http://localhost:1234/", "http://localhost:1234/v1/models")]
    [DataRow("http://localhost:1234/v1", "http://localhost:1234/v1/models")]
    [DataRow("http://localhost:1234/v1/", "http://localhost:1234/v1/models")]
    public async Task Endpoint_is_normalized_with_or_without_the_v1_suffix(string configured, string expected)
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, ModelsBody);
        var adapter = Adapter(handler);

        await adapter.CheckHealthAsync(configured);

        Assert.AreEqual(expected, handler.Requests.Single().RequestUri!.ToString());
    }

    [TestMethod]
    public async Task Response_missing_data_array_reports_degraded()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"unexpected\":true}"));

        var result = await adapter.ListModelsAsync("lmstudio-local", "http://localhost:1234/v1");

        Assert.AreEqual(HealthState.Degraded, result.Status);
        StringAssert.Contains(result.ErrorMessage, "'data' array");
    }
}
