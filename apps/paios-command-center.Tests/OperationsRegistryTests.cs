using Microsoft.Extensions.Logging.Abstractions;
using Paios.CommandCenter.Operations;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Tests;

[TestClass]
public sealed class OperationsRegistryTests
{
    private sealed class FakeCheck(string serviceId, params ServiceHealth[] results) : IServiceHealthCheck
    {
        public string ServiceId => serviceId;
        public Task<IReadOnlyList<ServiceHealth>> CheckAsync(CancellationToken cancellationToken = default)
            => Task.FromResult<IReadOnlyList<ServiceHealth>>(results);
    }

    private sealed class ThrowingCheck(string serviceId) : IServiceHealthCheck
    {
        public string ServiceId => serviceId;
        public Task<IReadOnlyList<ServiceHealth>> CheckAsync(CancellationToken cancellationToken = default)
            => throw new InvalidOperationException("probe exploded");
    }

    private static ServiceHealth Service(string id, HealthState status, ServiceCategory category = ServiceCategory.Infrastructure)
        => new()
        {
            ServiceId = id,
            DisplayName = id,
            Category = category,
            Status = status,
            LastCheckedAt = DateTimeOffset.UtcNow.ToString("O")
        };

    private static OperationsRegistry Registry(params IServiceHealthCheck[] checks)
        => new(checks, NullLogger<OperationsRegistry>.Instance);

    [TestMethod]
    public async Task A_throwing_check_becomes_a_degraded_card_and_does_not_break_the_snapshot()
    {
        var registry = Registry(
            new FakeCheck("ok", Service("healthy-one", HealthState.Healthy)),
            new ThrowingCheck("broken"));

        var snapshot = await registry.GetSnapshotAsync();

        // The healthy service still renders — this is the criterion that a
        // health-check failure must not crash the workspace.
        Assert.AreEqual(2, snapshot.Services.Count);
        Assert.IsTrue(snapshot.Services.Any(s => s.ServiceId == "healthy-one" && s.Status == HealthState.Healthy));

        var broken = snapshot.Services.Single(s => s.ServiceId == "broken");
        Assert.AreEqual(HealthState.Degraded, broken.Status);
        StringAssert.Contains(broken.ErrorMessage, "probe exploded");
    }

    [TestMethod]
    public async Task All_four_states_are_represented_and_counted()
    {
        var registry = Registry(new FakeCheck("all",
            Service("a", HealthState.Healthy),
            Service("b", HealthState.Degraded),
            Service("c", HealthState.Unavailable),
            Service("d", HealthState.Unknown)));

        var snapshot = await registry.GetSnapshotAsync();

        Assert.AreEqual(1, snapshot.Counts["healthy"]);
        Assert.AreEqual(1, snapshot.Counts["degraded"]);
        Assert.AreEqual(1, snapshot.Counts["unavailable"]);
        Assert.AreEqual(1, snapshot.Counts["unknown"]);
    }

    [DataTestMethod]
    [DataRow(HealthState.Healthy, HealthState.Healthy, HealthState.Healthy)]
    [DataRow(HealthState.Healthy, HealthState.Degraded, HealthState.Degraded)]
    [DataRow(HealthState.Healthy, HealthState.Unavailable, HealthState.Unavailable)]
    [DataRow(HealthState.Degraded, HealthState.Unavailable, HealthState.Unavailable)]
    [DataRow(HealthState.Healthy, HealthState.Unknown, HealthState.Unknown)]
    public async Task Rollup_takes_the_worst_observed_state(HealthState first, HealthState second, HealthState expected)
    {
        var registry = Registry(new FakeCheck("x", Service("a", first), Service("b", second)));

        var snapshot = await registry.GetSnapshotAsync();

        Assert.AreEqual(expected, snapshot.Overall);
    }

    [TestMethod]
    public async Task An_empty_system_is_unknown_never_healthy()
    {
        var snapshot = await Registry().GetSnapshotAsync();

        Assert.AreEqual(HealthState.Unknown, snapshot.Overall);
        Assert.AreEqual(0, snapshot.Services.Count);
    }

    [TestMethod]
    public async Task Every_service_carries_last_check_information()
    {
        var registry = Registry(new FakeCheck("x", Service("a", HealthState.Healthy)));

        var snapshot = await registry.GetSnapshotAsync();

        Assert.IsTrue(DateTimeOffset.TryParse(snapshot.Services[0].LastCheckedAt, out _));
        Assert.IsTrue(DateTimeOffset.TryParse(snapshot.GeneratedAt, out _));
    }

    [TestMethod]
    public async Task Named_service_lookup_returns_it_and_unknown_ids_return_null()
    {
        var registry = Registry(new FakeCheck("x", Service("postgres", HealthState.Unavailable)));

        Assert.IsNotNull(await registry.GetServiceAsync("postgres"));
        Assert.IsNull(await registry.GetServiceAsync("absent"));
    }
}
