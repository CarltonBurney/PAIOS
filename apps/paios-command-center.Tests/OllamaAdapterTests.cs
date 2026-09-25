using System.Net;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Tests;

[TestClass]
public sealed class OllamaAdapterTests
{
    private const string TagsBody = """
    {"models":[{"name":"llama3.2:latest","size":2019393189},{"name":"nomic-embed-text","size":274302450}]}
    """;

    private static OllamaAdapter Adapter(StubHttpMessageHandler handler) => new(new HttpClient(handler));

    [TestMethod]
    public async Task Running_with_models_reports_healthy()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.OK, TagsBody));

        var health = await adapter.CheckHealthAsync("http://localhost:11434");

        Assert.AreEqual(HealthState.Healthy, health.Status);
        Assert.IsNull(health.ErrorMessage);
        Assert.IsNotNull(health.LatencyMs);
    }

    [TestMethod]
    public async Task Running_with_models_enumerates_them_from_the_provider()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.OK, TagsBody));

        var result = await adapter.ListModelsAsync("ollama-local", "http://localhost:11434");

        Assert.AreEqual(HealthState.Healthy, result.Status);
        Assert.AreEqual(2, result.Models.Count);
        Assert.AreEqual("llama3.2:latest", result.Models[0].ModelId);
        Assert.AreEqual(2019393189L, result.Models[0].SizeBytes);
        Assert.AreEqual("ollama-local", result.Models[0].ProviderId);
    }

    [TestMethod]
    public async Task Not_running_reports_unavailable_without_throwing()
    {
        var adapter = Adapter(StubHttpMessageHandler.Throwing(
            new HttpRequestException("Connection refused", new IOException("Connection refused"))));

        var health = await adapter.CheckHealthAsync("http://localhost:11434");

        Assert.AreEqual(HealthState.Unavailable, health.Status);
        Assert.AreEqual("Connection refused", health.ErrorMessage);
    }

    [TestMethod]
    public async Task Not_running_yields_empty_model_list_marked_unavailable()
    {
        var adapter = Adapter(StubHttpMessageHandler.Throwing(new HttpRequestException("Connection refused")));

        var result = await adapter.ListModelsAsync("ollama-local", "http://localhost:11434");

        Assert.AreEqual(HealthState.Unavailable, result.Status);
        Assert.AreEqual(0, result.Models.Count);
    }

    [TestMethod]
    public async Task Timeout_reports_unavailable_rather_than_hanging()
    {
        var adapter = Adapter(StubHttpMessageHandler.Throwing(new TaskCanceledException()));

        var health = await adapter.CheckHealthAsync("http://localhost:11434");

        Assert.AreEqual(HealthState.Unavailable, health.Status);
        Assert.AreEqual("Request timed out.", health.ErrorMessage);
    }

    [TestMethod]
    public async Task Malformed_json_reports_degraded_and_captures_the_error()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"models\": [ BROKEN"));

        var health = await adapter.CheckHealthAsync("http://localhost:11434");

        Assert.AreEqual(HealthState.Degraded, health.Status);
        StringAssert.Contains(health.ErrorMessage, "Malformed JSON");
    }

    [TestMethod]
    public async Task Response_missing_models_array_reports_degraded()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"unexpected\":true}"));

        var result = await adapter.ListModelsAsync("ollama-local", "http://localhost:11434");

        Assert.AreEqual(HealthState.Degraded, result.Status);
        StringAssert.Contains(result.ErrorMessage, "'models' array");
    }

    [TestMethod]
    public async Task Healthy_provider_with_zero_models_is_valid_not_an_error()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"models\":[]}"));

        var result = await adapter.ListModelsAsync("ollama-local", "http://localhost:11434");

        Assert.AreEqual(HealthState.Healthy, result.Status);
        Assert.AreEqual(0, result.Models.Count);
        Assert.IsNull(result.ErrorMessage);
    }

    [TestMethod]
    public async Task Http_error_status_reports_degraded()
    {
        var adapter = Adapter(StubHttpMessageHandler.Returning(HttpStatusCode.InternalServerError, "boom"));

        var health = await adapter.CheckHealthAsync("http://localhost:11434");

        Assert.AreEqual(HealthState.Degraded, health.Status);
        StringAssert.Contains(health.ErrorMessage, "500");
    }

    [TestMethod]
    public async Task Endpoint_with_trailing_slash_still_targets_api_tags()
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, TagsBody);
        var adapter = Adapter(handler);

        await adapter.CheckHealthAsync("http://localhost:11434/");

        Assert.AreEqual("http://localhost:11434/api/tags", handler.Requests.Single().RequestUri!.ToString());
    }
}
