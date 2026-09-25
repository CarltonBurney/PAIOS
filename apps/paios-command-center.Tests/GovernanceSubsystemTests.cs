using System.Net;
using Microsoft.Extensions.Logging.Abstractions;
using Paios.CommandCenter.Configuration;
using Paios.CommandCenter.Dashboard;
using Paios.CommandCenter.Governance;
using Paios.CommandCenter.Operations;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Tests;

/// <summary>
/// How the control plane appears on the dashboard and in Operations.
///
/// The property under test throughout is the availability-versus-health
/// separation: governance is now *implemented*, so an unreachable kernel must read
/// as unavailable, never as not-implemented, and must never report metrics it did
/// not measure.
/// </summary>
[TestClass]
public sealed class GovernanceSubsystemTests
{
    private string _providerConfigPath = null!;

    [TestInitialize]
    public void Setup()
        => _providerConfigPath = Path.Combine(Path.GetTempPath(), $"paios-gov-{Guid.NewGuid():N}.json");

    [TestCleanup]
    public void Cleanup()
    {
        if (File.Exists(_providerConfigPath)) File.Delete(_providerConfigPath);
    }

    private DashboardAggregator Dashboard(GovernanceClient governance)
    {
        var store = new ProviderConfigurationStore(_providerConfigPath, NullLogger<ProviderConfigurationStore>.Instance);
        var providerClient = new HttpClient(
            StubHttpMessageHandler.Returning(HttpStatusCode.OK, "{\"models\":[]}"));
        var providers = new ProviderRegistry(store,
            new OllamaAdapter(providerClient),
            new OpenAiCompatibleAdapter(providerClient, _ => null),
            NullLogger<ProviderRegistry>.Instance);
        var operations = new OperationsRegistry(
            [new GovernanceHealthCheck(governance)], NullLogger<OperationsRegistry>.Instance);

        return new DashboardAggregator(providers, operations, governance,
            NullLogger<DashboardAggregator>.Instance);
    }

    private async Task<SubsystemSummary> Card(GovernanceClient governance)
        => (await Dashboard(governance).GetSnapshotAsync())
            .Subsystems.Single(s => s.SubsystemId == "governance");

    // -- the availability / health separation --------------------------------

    [TestMethod]
    public async Task A_healthy_kernel_reports_implemented_and_healthy_with_real_counts()
    {
        var card = await Card(GovernanceStub.Healthy());

        Assert.AreEqual(SubsystemAvailability.Implemented, card.Availability);
        Assert.AreEqual(HealthState.Healthy, card.Status);
        Assert.AreEqual("7/7 enabled", card.Metrics.Single(m => m.Label == "policies").Value);
        Assert.AreEqual("5/6 enabled", card.Metrics.Single(m => m.Label == "tools").Value);
        Assert.AreEqual("5", card.Metrics.Single(m => m.Label == "risk levels").Value);
        Assert.IsNull(card.Detail);
    }

    [TestMethod]
    public async Task An_unreachable_kernel_is_unavailable_and_still_implemented()
    {
        // The whole point of the separation: built-but-down is not not-built.
        var card = await Card(GovernanceStub.Unreachable());

        Assert.AreEqual(SubsystemAvailability.Implemented, card.Availability);
        Assert.AreEqual(HealthState.Unavailable, card.Status);
        StringAssert.Contains(card.Detail, "unreachable");
    }

    [TestMethod]
    public async Task An_unreachable_kernel_reports_no_metrics_rather_than_zeros()
    {
        var card = await Card(GovernanceStub.Unreachable());

        Assert.AreEqual(0, card.Metrics.Count,
            "an unmeasured subsystem must not contribute fabricated numbers");
    }

    [TestMethod]
    public async Task A_disabled_kernel_is_not_implemented_rather_than_unavailable()
    {
        // Switched off on purpose. Nothing is broken, so nothing should look broken.
        var card = await Card(GovernanceStub.Disabled());

        Assert.AreEqual(SubsystemAvailability.NotImplemented, card.Availability);
        Assert.AreEqual(HealthState.Unknown, card.Status);
        Assert.AreEqual(0, card.Metrics.Count);
        StringAssert.Contains(card.Detail, "disabled");
    }

    [TestMethod]
    public async Task A_degraded_kernel_is_degraded_not_unavailable()
    {
        var card = await Card(GovernanceStub.Degraded());

        Assert.AreEqual(HealthState.Degraded, card.Status);
        Assert.IsNotNull(card.Detail);
    }

    [TestMethod]
    public async Task A_rejected_credential_degrades_the_card_even_though_health_is_fine()
    {
        // Health passes (it needs no credential) but the authenticated read fails.
        // Taking the better of the two would report a healthy governance layer the
        // Command Center cannot actually read.
        var card = await Card(GovernanceStub.RejectingCredential());

        Assert.AreEqual(HealthState.Degraded, card.Status);
        StringAssert.Contains(card.Detail, "credential");
    }

    [TestMethod]
    public async Task A_null_tool_count_is_omitted_rather_than_rendered_as_zero()
    {
        var card = await Card(GovernanceStub.Healthy(GovernanceStub.StatusWithoutToolsBody));

        Assert.IsFalse(card.Metrics.Any(m => m.Label == "tools"),
            "a kernel with no registry attached must not appear to have zero tools");
        // The counts it did report are still present.
        Assert.AreEqual("7/7 enabled", card.Metrics.Single(m => m.Label == "policies").Value);
    }

    // -- alerts --------------------------------------------------------------

    [TestMethod]
    public async Task An_unreachable_kernel_raises_a_traceable_critical_alert()
    {
        var alerts = (await Dashboard(GovernanceStub.Unreachable()).GetSnapshotAsync())
            .Alerts.Where(a => a.SubsystemId == "governance").ToList();

        var alert = alerts.First(a => a.SourceId == GovernanceHealthCheck.Id);
        Assert.AreEqual(AlertSeverity.Critical, alert.Severity);
        StringAssert.Contains(alert.Message, "Connection refused");
    }

    [TestMethod]
    public async Task A_degraded_kernel_raises_a_warning_not_a_critical()
    {
        var alerts = (await Dashboard(GovernanceStub.Degraded()).GetSnapshotAsync())
            .Alerts.Where(a => a.SubsystemId == "governance").ToList();

        Assert.IsTrue(alerts.Any(a => a.Severity == AlertSeverity.Warning));
        Assert.IsFalse(alerts.Any(a => a.Severity == AlertSeverity.Critical));
    }

    [TestMethod]
    public async Task A_disabled_kernel_raises_no_alert_at_all()
    {
        var alerts = (await Dashboard(GovernanceStub.Disabled()).GetSnapshotAsync())
            .Alerts.Where(a => a.SubsystemId == "governance").ToList();

        Assert.AreEqual(0, alerts.Count, "a deliberate configuration is not an incident");
    }

    // -- roll-up -------------------------------------------------------------

    [TestMethod]
    public async Task An_unavailable_kernel_drags_the_dashboard_rollup()
    {
        // Governance is implemented, so unlike Agent Lab it participates.
        var snapshot = await Dashboard(GovernanceStub.Unreachable()).GetSnapshotAsync();

        Assert.AreEqual(HealthState.Unavailable, snapshot.Overall);
    }

    [TestMethod]
    public async Task A_disabled_kernel_does_not_drag_the_rollup()
    {
        var snapshot = await Dashboard(GovernanceStub.Disabled()).GetSnapshotAsync();

        Assert.AreNotEqual(HealthState.Unavailable, snapshot.Overall);
    }

    [TestMethod]
    public async Task The_governance_card_drills_into_the_governance_workspace()
    {
        var card = await Card(GovernanceStub.Healthy());

        Assert.AreEqual("governance", card.Workspace);
        Assert.IsFalse(string.IsNullOrWhiteSpace(card.Source));
    }

    // -- operations records --------------------------------------------------

    [TestMethod]
    public async Task Operations_lists_the_kernel_and_one_record_per_component()
    {
        var records = await new GovernanceHealthCheck(GovernanceStub.Healthy()).CheckAsync();

        Assert.AreEqual(5, records.Count, "the kernel plus its four components");
        Assert.IsTrue(records.All(r => r.Category == ServiceCategory.Governance));
        Assert.IsTrue(records.All(r => r.Status == HealthState.Healthy));
        Assert.IsTrue(records.Any(r => r.ServiceId == GovernanceHealthCheck.Id));
        Assert.IsTrue(records.Any(r => r.ServiceId == $"{GovernanceHealthCheck.Id}:policy"));
    }

    [TestMethod]
    public async Task A_partial_component_failure_is_visible_per_component()
    {
        // The value of per-component records: "policy is unreadable while the
        // provider is fine" survives instead of collapsing into one amber light.
        var records = await new GovernanceHealthCheck(GovernanceStub.Degraded()).CheckAsync();

        var policy = records.Single(r => r.ServiceId.EndsWith(":policy"));
        var provider = records.Single(r => r.ServiceId.EndsWith(":provider"));

        Assert.AreEqual(HealthState.Unavailable, policy.Status);
        StringAssert.Contains(policy.ErrorMessage, "unreadable");
        Assert.AreEqual(HealthState.Healthy, provider.Status);
        Assert.IsNull(provider.ErrorMessage);
    }

    [TestMethod]
    public async Task An_unreachable_kernel_yields_one_record_not_fabricated_components()
    {
        var records = await new GovernanceHealthCheck(GovernanceStub.Unreachable()).CheckAsync();

        Assert.AreEqual(1, records.Count,
            "components the kernel never reported must not be invented");
        Assert.AreEqual(HealthState.Unavailable, records.Single().Status);
        Assert.AreEqual(GovernanceStub.Endpoint, records.Single().Target);
    }

    [TestMethod]
    public async Task A_disabled_kernel_is_marked_uncheckable_rather_than_unavailable()
    {
        var record = (await new GovernanceHealthCheck(GovernanceStub.Disabled()).CheckAsync()).Single();

        Assert.IsFalse(record.Checkable);
        Assert.AreEqual(HealthState.Unknown, record.Status);
        StringAssert.Contains(record.ErrorMessage, "Disabled");
    }

    [TestMethod]
    public async Task A_rejected_credential_degrades_the_operations_record_too()
    {
        // The kernel's /health is open, so it answers healthy even with a bad
        // token. Operations must still report the link as degraded — otherwise it
        // reads green while the dashboard reads amber for the same fault.
        var records = await new GovernanceHealthCheck(GovernanceStub.RejectingCredential()).CheckAsync();

        var kernel = records.Single(r => r.ServiceId == GovernanceHealthCheck.Id);
        Assert.AreEqual(HealthState.Degraded, kernel.Status);
        StringAssert.Contains(kernel.ErrorMessage, "credential");

        // The components themselves really are healthy, and still say so.
        Assert.IsTrue(records.Where(r => r.ServiceId != GovernanceHealthCheck.Id)
            .All(r => r.Status == HealthState.Healthy));
    }

    [TestMethod]
    public async Task An_unreachable_kernel_is_not_probed_a_second_time()
    {
        // No point authenticating against something that is not there, and doing so
        // would double the wait before the card renders.
        var handler = StubHttpMessageHandler.Throwing(new HttpRequestException("refused"));
        await new GovernanceHealthCheck(GovernanceStub.Build(handler)).CheckAsync();

        Assert.AreEqual(1, handler.Requests.Count);
    }

    [TestMethod]
    public async Task Health_records_carry_a_timestamp_latency_and_target()
    {
        var record = (await new GovernanceHealthCheck(GovernanceStub.Healthy()).CheckAsync())
            .Single(r => r.ServiceId == GovernanceHealthCheck.Id);

        Assert.IsTrue(DateTimeOffset.TryParse(record.LastCheckedAt, out _));
        Assert.IsNotNull(record.LatencyMs);
        Assert.AreEqual(GovernanceStub.Endpoint, record.Target);
    }

    [TestMethod]
    public async Task An_unrecognised_component_status_is_unknown_not_healthy()
    {
        var handler = new StubHttpMessageHandler(_ => new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(
                """{"status":"healthy","checks":{"policy":{"status":"who knows","detail":"?"}}}""",
                System.Text.Encoding.UTF8, "application/json")
        });

        var records = await new GovernanceHealthCheck(GovernanceStub.Build(handler)).CheckAsync();

        Assert.AreEqual(HealthState.Unknown,
            records.Single(r => r.ServiceId.EndsWith(":policy")).Status);
    }

    // -- propagation ---------------------------------------------------------

    [TestMethod]
    public async Task Governance_state_propagates_without_a_refresh_mechanism()
    {
        // Same assertion the Phase 4 work made for providers: the aggregator holds
        // no governance state, so two reads against different truth differ.
        var up = await Card(GovernanceStub.Healthy());
        var down = await Card(GovernanceStub.Unreachable());

        Assert.AreEqual(HealthState.Healthy, up.Status);
        Assert.AreEqual(HealthState.Unavailable, down.Status);
    }

    [TestMethod]
    public async Task The_agent_card_no_longer_claims_governance_is_unresolved()
    {
        // Phase 2's location question is answered; Agent Lab's own absence is not.
        var agents = (await Dashboard(GovernanceStub.Healthy()).GetSnapshotAsync())
            .Subsystems.Single(s => s.SubsystemId == "agents");

        Assert.AreEqual(SubsystemAvailability.NotImplemented, agents.Availability);
        Assert.AreEqual(0, agents.Metrics.Count);
        Assert.IsFalse(agents.Detail!.Contains("blocked"),
            "the governance location blocker is resolved and must not be restated");
    }
}
