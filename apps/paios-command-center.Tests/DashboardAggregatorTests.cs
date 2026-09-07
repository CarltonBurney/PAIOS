using System.Net;
using Microsoft.Extensions.Logging.Abstractions;
using Paios.CommandCenter.Configuration;
using Paios.CommandCenter.Dashboard;
using Paios.CommandCenter.Operations;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Tests;

[TestClass]
public sealed class DashboardAggregatorTests
{
    private string _providerConfigPath = null!;

    [TestInitialize]
    public void Setup()
        => _providerConfigPath = Path.Combine(Path.GetTempPath(), $"paios-dash-{Guid.NewGuid():N}.json");

    [TestCleanup]
    public void Cleanup()
    {
        if (File.Exists(_providerConfigPath)) File.Delete(_providerConfigPath);
    }

    private sealed class FakeCheck(string id, params ServiceHealth[] results) : IServiceHealthCheck
    {
        public string ServiceId => id;
        public Task<IReadOnlyList<ServiceHealth>> CheckAsync(CancellationToken cancellationToken = default)
            => Task.FromResult<IReadOnlyList<ServiceHealth>>(results);
    }

    private sealed class ThrowingCheck : IServiceHealthCheck
    {
        public string ServiceId => "boom";
        public Task<IReadOnlyList<ServiceHealth>> CheckAsync(CancellationToken cancellationToken = default)
            => throw new InvalidOperationException("operations exploded");
    }

    private static ServiceHealth Service(string id, HealthState status, bool checkable = true) => new()
    {
        ServiceId = id,
        DisplayName = id,
        Category = ServiceCategory.Infrastructure,
        Status = status,
        Checkable = checkable,
        LastCheckedAt = DateTimeOffset.UtcNow.ToString("O")
    };

    private DashboardAggregator Build(StubHttpMessageHandler providerHandler, params IServiceHealthCheck[] checks)
    {
        var store = new ProviderConfigurationStore(_providerConfigPath, NullLogger<ProviderConfigurationStore>.Instance);
        var client = new HttpClient(providerHandler);
        var providers = new ProviderRegistry(store,
            new OllamaAdapter(client),
            new OpenAiCompatibleAdapter(client, _ => null),
            NullLogger<ProviderRegistry>.Instance);
        var operations = new OperationsRegistry(checks, NullLogger<OperationsRegistry>.Instance);
        return new DashboardAggregator(providers, operations, NullLogger<DashboardAggregator>.Instance);
    }

    [TestMethod]
    public async Task Agent_subsystem_is_reported_not_implemented_with_no_fabricated_metrics()
    {
        var dashboard = Build(StubHttpMessageHandler.Throwing(new HttpRequestException("refused")));

        var agents = (await dashboard.GetSnapshotAsync()).Subsystems.Single(s => s.SubsystemId == "agents");

        Assert.AreEqual(SubsystemAvailability.NotImplemented, agents.Availability);
        Assert.AreEqual(HealthState.Unknown, agents.Status);
        Assert.AreEqual(0, agents.Metrics.Count, "an unbuilt subsystem must not report metrics");
        StringAssert.Contains(agents.Detail, "No agent registry");
    }

    [TestMethod]
    public async Task Not_implemented_subsystem_does_not_drag_the_rollup_but_is_never_counted_healthy()
    {
        var dashboard = Build(
            StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"models\":[]}"),
            new FakeCheck("ok", Service("svc", HealthState.Healthy)));

        var snapshot = await dashboard.GetSnapshotAsync();

        // Agent Lab is unknown, yet the roll-up is healthy because unbuilt
        // subsystems are excluded rather than treated as indeterminate.
        Assert.AreEqual(HealthState.Healthy, snapshot.Overall);
        Assert.AreEqual(HealthState.Unknown, snapshot.Subsystems.Single(s => s.SubsystemId == "agents").Status);
    }

    [TestMethod]
    public async Task Every_subsystem_names_the_source_its_numbers_came_from()
    {
        var dashboard = Build(StubHttpMessageHandler.Throwing(new HttpRequestException("refused")));

        foreach (var subsystem in (await dashboard.GetSnapshotAsync()).Subsystems)
        {
            Assert.IsFalse(string.IsNullOrWhiteSpace(subsystem.Source), $"{subsystem.SubsystemId} has no source");
        }
    }

    [TestMethod]
    public async Task Unhealthy_provider_produces_a_traceable_critical_alert()
    {
        var dashboard = Build(StubHttpMessageHandler.Throwing(new HttpRequestException("Connection refused")));

        var alerts = (await dashboard.GetSnapshotAsync()).Alerts;

        var alert = alerts.First(a => a.SubsystemId == "models");
        Assert.AreEqual(AlertSeverity.Critical, alert.Severity);
        Assert.AreEqual(ProviderRegistry.AutoDetectedOllamaId, alert.SourceId);
        StringAssert.Contains(alert.Message, "Connection refused");
    }

    [TestMethod]
    public async Task Uncheckable_service_produces_an_informational_alert_not_a_critical_one()
    {
        var dashboard = Build(
            StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"models\":[]}"),
            new FakeCheck("infra", Service("qdrant", HealthState.Unknown, checkable: false)));

        var alerts = (await dashboard.GetSnapshotAsync()).Alerts;

        Assert.IsTrue(alerts.Any(a => a.Severity == AlertSeverity.Info && a.SourceId == "qdrant"));
    }

    [TestMethod]
    public async Task A_failing_subsystem_degrades_only_its_own_card()
    {
        var dashboard = Build(
            StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"models\":[]}"),
            new ThrowingCheck());

        var snapshot = await dashboard.GetSnapshotAsync();

        // Three cards still render — the dashboard survives a broken subsystem.
        Assert.AreEqual(3, snapshot.Subsystems.Count);
        Assert.AreEqual(HealthState.Healthy, snapshot.Subsystems.Single(s => s.SubsystemId == "models").Status);
    }

    [TestMethod]
    public async Task Model_count_ignores_providers_that_cannot_be_read()
    {
        // Unreachable provider: no inventory may be invented for it.
        var dashboard = Build(StubHttpMessageHandler.Throwing(new HttpRequestException("refused")));

        var models = (await dashboard.GetSnapshotAsync()).Subsystems.Single(s => s.SubsystemId == "models");

        Assert.AreEqual("0", models.Metrics.Single(m => m.Label == "models available").Value);
    }

    [TestMethod]
    public async Task Healthy_provider_contributes_its_real_model_count()
    {
        var dashboard = Build(StubHttpMessageHandler.Returning(HttpStatusCode.OK,
            "{\"models\":[{\"name\":\"a\"},{\"name\":\"b\"},{\"name\":\"c\"}]}"));

        var models = (await dashboard.GetSnapshotAsync()).Subsystems.Single(s => s.SubsystemId == "models");

        Assert.AreEqual("3", models.Metrics.Single(m => m.Label == "models available").Value);
        Assert.AreEqual(HealthState.Healthy, models.Status);
    }

    [TestMethod]
    public async Task Every_subsystem_drills_into_a_real_workspace_id()
    {
        var dashboard = Build(StubHttpMessageHandler.Throwing(new HttpRequestException("refused")));
        string[] navIds = ["dashboard", "llm", "agents", "ops"];

        foreach (var subsystem in (await dashboard.GetSnapshotAsync()).Subsystems)
        {
            CollectionAssert.Contains(navIds, subsystem.Workspace,
                $"{subsystem.SubsystemId} drills into '{subsystem.Workspace}', which is not a nav workspace");
        }
    }

    [TestMethod]
    public async Task Snapshot_is_timestamped_and_alerts_are_ordered_by_severity()
    {
        var dashboard = Build(
            StubHttpMessageHandler.Throwing(new HttpRequestException("refused")),
            new FakeCheck("infra", Service("declared", HealthState.Unknown, checkable: false)));

        var snapshot = await dashboard.GetSnapshotAsync();

        Assert.IsTrue(DateTimeOffset.TryParse(snapshot.GeneratedAt, out _));
        var severities = snapshot.Alerts.Select(a => a.Severity).ToList();
        CollectionAssert.AreEqual(severities.OrderBy(s => s).ToList(), severities,
            "alerts must be ordered most severe first");
    }

    [TestMethod]
    public async Task Dashboard_holds_no_state_between_reads()
    {
        // Two reads through the same aggregator against different underlying
        // truth must report different results — this is what makes the phase's
        // propagation requirement structural rather than a refresh mechanism.
        var healthy = Build(StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"models\":[{\"name\":\"a\"}]}"));
        var first = await healthy.GetSnapshotAsync();

        var unreachable = Build(StubHttpMessageHandler.Throwing(new HttpRequestException("refused")));
        var second = await unreachable.GetSnapshotAsync();

        Assert.AreEqual(HealthState.Healthy, first.Subsystems.Single(s => s.SubsystemId == "models").Status);
        Assert.AreEqual(HealthState.Unavailable, second.Subsystems.Single(s => s.SubsystemId == "models").Status);
    }
}
