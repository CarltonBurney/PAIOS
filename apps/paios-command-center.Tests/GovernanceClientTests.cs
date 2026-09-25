using System.Net;
using Paios.CommandCenter.Governance;
using Paios.CommandCenter.Providers;

namespace Paios.CommandCenter.Tests;

/// <summary>
/// The client's job is to turn every way the control plane can fail into a
/// <see cref="HealthState"/> the dashboard can render. These tests exist mostly
/// to prove it never throws, and that it distinguishes failures an operator would
/// respond to differently.
/// </summary>
[TestClass]
public sealed class GovernanceClientTests
{
    [TestMethod]
    public async Task A_healthy_control_plane_reads_healthy()
    {
        var result = await GovernanceStub.Healthy().GetHealthAsync();

        Assert.AreEqual(HealthState.Healthy, result.Status);
        Assert.IsNull(result.ErrorMessage);
        Assert.AreEqual("dev", result.Value!.Environment);
        Assert.AreEqual(4, result.Value.Checks.Count);
    }

    [TestMethod]
    public async Task Nothing_listening_is_unavailable_not_an_exception()
    {
        var result = await GovernanceStub.Unreachable().GetHealthAsync();

        Assert.AreEqual(HealthState.Unavailable, result.Status);
        StringAssert.Contains(result.ErrorMessage, "unreachable");
        StringAssert.Contains(result.ErrorMessage, "Connection refused");
        Assert.IsNull(result.Value);
    }

    [TestMethod]
    public async Task A_timeout_is_unavailable_and_says_so()
    {
        var result = await GovernanceStub.TimingOut().GetHealthAsync();

        Assert.AreEqual(HealthState.Unavailable, result.Status);
        StringAssert.Contains(result.ErrorMessage, "did not answer before the timeout");
    }

    [TestMethod]
    public async Task A_kernel_reporting_itself_degraded_is_degraded_and_its_body_is_kept()
    {
        // 503 still carries which component failed; discarding it would throw away
        // the only useful part of the answer.
        var result = await GovernanceStub.Degraded().GetHealthAsync();

        Assert.AreEqual(HealthState.Degraded, result.Status);
        Assert.IsNotNull(result.Value);
        Assert.AreEqual("unavailable", result.Value!.Checks["policy"].Status);
        StringAssert.Contains(result.Value.Checks["policy"].Detail, "unreadable");
    }

    [TestMethod]
    public async Task A_rejected_credential_is_degraded_not_unavailable()
    {
        // The distinction matters: "start the kernel" and "fix the token" are
        // different jobs, and the state is the only hint the operator gets.
        var result = await GovernanceStub.RejectingCredential().GetStatusAsync();

        Assert.AreEqual(HealthState.Degraded, result.Status);
        StringAssert.Contains(result.ErrorMessage, "rejected the configured credential");
        StringAssert.Contains(result.ErrorMessage, "tokenEnvVar");
    }

    [TestMethod]
    public async Task An_unparseable_body_is_degraded_with_the_parse_error()
    {
        var result = await GovernanceStub.ReturningUnparseableBody().GetHealthAsync();

        Assert.AreEqual(HealthState.Degraded, result.Status);
        StringAssert.Contains(result.ErrorMessage, "did not parse");
    }

    [TestMethod]
    public async Task A_disabled_control_plane_is_degraded_and_makes_no_request()
    {
        // The stub throws if it is ever called, so this also proves no request.
        var result = await GovernanceStub.Disabled().GetHealthAsync();

        Assert.AreEqual(HealthState.Degraded, result.Status);
        StringAssert.Contains(result.ErrorMessage, "disabled in configuration");
    }

    [TestMethod]
    public async Task An_unreadable_configuration_is_treated_as_disabled()
    {
        var result = await GovernanceStub.WithBrokenConfiguration().GetHealthAsync();

        Assert.AreEqual(HealthState.Degraded, result.Status);
        StringAssert.Contains(result.ErrorMessage, "disabled in configuration");
    }

    // -- credential handling -------------------------------------------------

    [TestMethod]
    public async Task The_token_is_sent_as_a_bearer_header_on_authenticated_reads()
    {
        var (client, handler) = GovernanceStub.Recording();

        await client.GetStatusAsync();

        var request = handler.Requests.Single();
        Assert.AreEqual("Bearer", request.Headers.Authorization!.Scheme);
        Assert.AreEqual(GovernanceStub.Token, request.Headers.Authorization.Parameter);
    }

    [TestMethod]
    public async Task Health_is_probed_without_a_credential()
    {
        // The kernel leaves /health open, and probing it unauthenticated is what
        // lets a bad token be diagnosed as degraded rather than as an outage.
        var (client, handler) = GovernanceStub.Recording();

        await client.GetHealthAsync();

        Assert.IsNull(handler.Requests.Single().Headers.Authorization);
    }

    [TestMethod]
    public async Task An_unset_token_variable_sends_no_header_rather_than_an_empty_one()
    {
        var (client, handler) = GovernanceStub.Recording(token: null);

        await client.GetStatusAsync();

        Assert.IsNull(handler.Requests.Single().Headers.Authorization,
            "an empty Bearer header would make the kernel's 401 ambiguous");
    }

    [TestMethod]
    public async Task No_token_variable_configured_sends_no_header()
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, GovernanceStub.StatusBody);
        var client = GovernanceStub.Build(handler, tokenEnvVar: null);

        await client.GetStatusAsync();

        Assert.IsNull(handler.Requests.Single().Headers.Authorization);
    }

    // -- submission ----------------------------------------------------------

    [TestMethod]
    public async Task A_decision_is_returned_for_an_executed_request()
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, """
            {
              "disposition": "auto_execute",
              "delivered": true,
              "blocked": false,
              "risk": {"level": "L0", "domains": []},
              "routing": {"disposition": "auto_execute", "agent": "project_agent",
                          "reason": "risk L0", "requires_human": false},
              "violations": [],
              "audit_ids": ["aud-1", "aud-2"]
            }
            """);

        var result = await GovernanceStub.Build(handler).SubmitAsync("summarise the roadmap");

        Assert.AreEqual(HealthState.Healthy, result.Status);
        Assert.AreEqual("auto_execute", result.Value!.Disposition);
        Assert.IsTrue(result.Value.Delivered);
        Assert.AreEqual(2, result.Value.AuditIds.Count);
    }

    [TestMethod]
    public async Task A_blocked_request_is_a_successful_read_not_an_error()
    {
        // "You may not do this" is governance working. Reporting it as a failed
        // read would make a refusal indistinguishable from an outage.
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, """
            {
              "disposition": "blocked",
              "delivered": false,
              "blocked": true,
              "risk": {"level": "L3", "domains": ["governance"]},
              "routing": {"disposition": "blocked", "agent": null,
                          "reason": "policy denied", "requires_human": false},
              "violations": [{"policy_id": "AUTHZ-001", "control": "auth", "detail": "unauthenticated"}],
              "audit_ids": ["aud-1"]
            }
            """);

        var result = await GovernanceStub.Build(handler).SubmitAsync("change the policy");

        Assert.AreEqual(HealthState.Healthy, result.Status);
        Assert.IsTrue(result.Value!.Blocked);
        Assert.AreEqual("AUTHZ-001", result.Value.Violations.Single().PolicyId);
    }

    [TestMethod]
    public async Task Empty_content_is_refused_before_any_request_is_made()
    {
        var handler = new StubHttpMessageHandler(_ => throw new InvalidOperationException("must not be called"));

        var result = await GovernanceStub.Build(handler).SubmitAsync("   ");

        Assert.AreEqual(HealthState.Degraded, result.Status);
        Assert.AreEqual(0, handler.Requests.Count);
    }

    [TestMethod]
    public async Task A_submission_carries_content_metadata_and_correlation_id_only()
    {
        var (client, handler) = GovernanceStub.Recording();

        await client.SubmitAsync(
            "do the thing",
            new Dictionary<string, object?> { ["ticket"] = "OPS-1" },
            "cor-123");

        var body = handler.Bodies.Single();
        StringAssert.Contains(body, "do the thing");
        StringAssert.Contains(body, "OPS-1");
        StringAssert.Contains(body, "cor-123");
        // Identity is the kernel's to decide. The client has no way to assert one.
        Assert.IsFalse(body.Contains("identity"), "the client must not send an identity");
    }

    [TestMethod]
    public async Task An_unreachable_kernel_makes_submission_unavailable()
    {
        var result = await GovernanceStub.Unreachable().SubmitAsync("anything");

        Assert.AreEqual(HealthState.Unavailable, result.Status);
        Assert.IsNull(result.Value);
    }

    // -- tools ---------------------------------------------------------------

    [TestMethod]
    public async Task Tools_are_read_with_their_governance_fields()
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, """
            {"tools": [{"tool_id": "security_read", "risk_level": "L1",
                        "operation_type": "read", "availability": "available"}]}
            """);

        var result = await GovernanceStub.Build(handler).GetToolsAsync();

        Assert.AreEqual(HealthState.Healthy, result.Status);
        var tool = result.Value!.Tools.Single();
        Assert.AreEqual("security_read", tool.ToolId);
        Assert.AreEqual("L1", tool.RiskLevel);
    }

    [TestMethod]
    public async Task An_endpoint_with_a_trailing_slash_still_resolves()
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, GovernanceStub.HealthyBody);
        var client = GovernanceStub.Build(handler, endpoint: "http://127.0.0.1:8081/");

        var result = await client.GetHealthAsync();

        Assert.AreEqual(HealthState.Healthy, result.Status);
        Assert.AreEqual("/health", handler.Requests.Single().RequestUri!.AbsolutePath);
    }

    [TestMethod]
    public async Task A_malformed_endpoint_is_reported_rather_than_thrown()
    {
        var handler = StubHttpMessageHandler.Returning(HttpStatusCode.OK, GovernanceStub.HealthyBody);
        var client = GovernanceStub.Build(handler, endpoint: "not-a-url");

        var result = await client.GetHealthAsync();

        Assert.AreNotEqual(HealthState.Healthy, result.Status);
        Assert.IsNotNull(result.ErrorMessage);
    }
}
