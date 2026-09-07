using System.Net;
using Paios.CommandCenter.Operations;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Tests;

[TestClass]
public sealed class InfrastructureHealthCheckTests
{
    private static InfrastructureHealthCheck Check(
        OperationsConfiguration configuration,
        StubHttpMessageHandler? handler = null,
        TimeSpan? tcpTimeout = null)
        => new(
            () => Task.FromResult(configuration),
            new HttpClient(handler ?? StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{}")),
            tcpTimeout ?? TimeSpan.FromMilliseconds(500));

    private static OperationsConfiguration Config(params MonitoredService[] services)
        => new() { Monitored = services.ToList() };

    [TestMethod]
    public async Task Nothing_declared_yields_no_services_rather_than_invented_ones()
    {
        var result = await Check(Config()).CheckAsync();

        Assert.AreEqual(0, result.Count);
    }

    [TestMethod]
    public async Task Reachable_http_service_is_healthy()
    {
        var check = Check(
            Config(new MonitoredService { ServiceId = "n8n", Probe = "http", Url = "http://localhost:5678/healthz" }),
            StubHttpMessageHandler.Returning(HttpStatusCode.OK, "ok"));

        var result = await check.CheckAsync();

        Assert.AreEqual(HealthState.Healthy, result.Single().Status);
        Assert.IsNotNull(result.Single().LatencyMs);
    }

    [TestMethod]
    public async Task Http_service_returning_an_error_status_is_degraded_not_down()
    {
        var check = Check(
            Config(new MonitoredService { ServiceId = "n8n", Probe = "http", Url = "http://localhost:5678/healthz" }),
            StubHttpMessageHandler.Returning(HttpStatusCode.InternalServerError, "boom"));

        var result = await check.CheckAsync();

        Assert.AreEqual(HealthState.Degraded, result.Single().Status);
        StringAssert.Contains(result.Single().ErrorMessage, "500");
    }

    [TestMethod]
    public async Task Unreachable_http_service_is_unavailable()
    {
        var check = Check(
            Config(new MonitoredService { ServiceId = "n8n", Probe = "http", Url = "http://localhost:5678/healthz" }),
            StubHttpMessageHandler.Throwing(new HttpRequestException("Connection refused")));

        var result = await check.CheckAsync();

        Assert.AreEqual(HealthState.Unavailable, result.Single().Status);
        Assert.AreEqual("Connection refused", result.Single().ErrorMessage);
    }

    [TestMethod]
    public async Task Http_timeout_is_unavailable_with_a_timeout_message()
    {
        var check = Check(
            Config(new MonitoredService { ServiceId = "slow", Probe = "http", Url = "http://localhost:5678/healthz" }),
            StubHttpMessageHandler.Throwing(new TaskCanceledException()));

        var result = await check.CheckAsync();

        Assert.AreEqual(HealthState.Unavailable, result.Single().Status);
        Assert.AreEqual("Request timed out.", result.Single().ErrorMessage);
    }

    [TestMethod]
    public async Task Refused_tcp_port_is_unavailable()
    {
        // Port 1 on loopback: reliably refused, and this is a real socket call.
        var check = Check(Config(new MonitoredService
        {
            ServiceId = "postgres", Probe = "tcp", Host = "127.0.0.1", Port = 1
        }));

        var result = await check.CheckAsync();

        Assert.AreEqual(HealthState.Unavailable, result.Single().Status);
        Assert.IsNotNull(result.Single().ErrorMessage);
    }

    [TestMethod]
    public async Task Unimplemented_probe_kind_is_unknown_and_marked_uncheckable()
    {
        var check = Check(Config(new MonitoredService
        {
            ServiceId = "qdrant", DisplayName = "Qdrant", Probe = "grpc", Host = "127.0.0.1", Port = 6334
        }));

        var service = (await check.CheckAsync()).Single();

        Assert.AreEqual(HealthState.Unknown, service.Status);
        Assert.IsFalse(service.Checkable);
        StringAssert.Contains(service.ErrorMessage, "No probe implemented");
    }

    [DataTestMethod]
    [DataRow(null, 5432)]
    [DataRow("127.0.0.1", 0)]
    [DataRow("127.0.0.1", 70000)]
    public async Task Invalid_tcp_declarations_are_unknown_rather_than_probed(string? host, int port)
    {
        var check = Check(Config(new MonitoredService
        {
            ServiceId = "bad", Probe = "tcp", Host = host, Port = port
        }));

        var service = (await check.CheckAsync()).Single();

        Assert.AreEqual(HealthState.Unknown, service.Status);
        Assert.IsFalse(service.Checkable);
    }

    [TestMethod]
    public async Task Invalid_http_url_is_unknown_rather_than_probed()
    {
        var check = Check(Config(new MonitoredService { ServiceId = "bad", Probe = "http", Url = "not-a-url" }));

        var service = (await check.CheckAsync()).Single();

        Assert.AreEqual(HealthState.Unknown, service.Status);
        Assert.IsFalse(service.Checkable);
    }

    [TestMethod]
    public async Task Service_id_is_used_when_no_display_name_is_given()
    {
        var check = Check(Config(new MonitoredService { ServiceId = "bare", Probe = "unknown-kind" }));

        Assert.AreEqual("bare", (await check.CheckAsync()).Single().DisplayName);
    }
}
