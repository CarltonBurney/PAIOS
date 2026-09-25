using System.Net;
using Microsoft.Extensions.Logging.Abstractions;
using Paios.CommandCenter.Configuration;
using Paios.CommandCenter.Governance;

namespace Paios.CommandCenter.Tests;

/// <summary>
/// Builds <see cref="GovernanceClient"/> instances against canned control plane
/// behaviour, so the bridge can be asserted without a Python process running.
/// The live path is covered separately by the end-to-end run.
/// </summary>
internal static class GovernanceStub
{
    public const string Endpoint = "http://127.0.0.1:8081";
    public const string TokenEnvVar = "PAIOS_TEST_TOKEN";
    public const string Token = "test-token";

    /// <summary>A kernel reporting itself fully healthy, with real-looking counts.</summary>
    public const string HealthyBody = """
        {
          "status": "healthy",
          "environment": "dev",
          "checks": {
            "policy": {"status": "healthy", "detail": "7 policies loaded"},
            "risk_model": {"status": "healthy", "detail": "risk model 'v1' with 5 levels"},
            "tool_registry": {"status": "healthy", "detail": "6 tools registered"},
            "provider": {"status": "healthy", "detail": "mock/mock-1"}
          }
        }
        """;

    /// <summary>A kernel that is up but whose policy document failed to load.</summary>
    public const string DegradedBody = """
        {
          "status": "degraded",
          "environment": "dev",
          "checks": {
            "policy": {"status": "unavailable", "detail": "policy document is unreadable"},
            "risk_model": {"status": "healthy", "detail": "risk model 'v1' with 5 levels"},
            "tool_registry": {"status": "healthy", "detail": "6 tools registered"},
            "provider": {"status": "healthy", "detail": "mock/mock-1"}
          }
        }
        """;

    public const string StatusBody = """
        {
          "environment": "dev",
          "provider": {"name": "mock", "model": "mock-1"},
          "policies": {"name": "PAIOS Governance Policies v1.0", "total": 7, "enabled": 7},
          "risk_model": {"name": "PAIOS Risk Model v1.0", "default_level": "L0", "levels": 5, "detectors": 13},
          "tools": {"registered": 6, "enabled": 5},
          "approval_handler": "deny_by_default",
          "authentication": "token"
        }
        """;

    /// <summary>Status with no tool registry attached — null counts, not zeros.</summary>
    public const string StatusWithoutToolsBody = """
        {
          "environment": "dev",
          "policies": {"name": "p", "total": 7, "enabled": 7},
          "risk_model": {"name": "r", "default_level": "L0", "levels": 5, "detectors": 13},
          "tools": {"registered": null, "enabled": null},
          "approval_handler": "deny_by_default",
          "authentication": "token"
        }
        """;

    public static GovernanceClient Disabled()
        => Build(new StubHttpMessageHandler(_ => throw new InvalidOperationException(
            "a disabled client must never make a request")), enabled: false);

    /// <summary>Nothing listening on the endpoint.</summary>
    public static GovernanceClient Unreachable(string message = "Connection refused")
        => Build(StubHttpMessageHandler.Throwing(new HttpRequestException(message)));

    public static GovernanceClient TimingOut()
        => Build(StubHttpMessageHandler.Throwing(new TaskCanceledException("timed out")));

    /// <summary>Healthy on /health and answering /api/governance/* as given.</summary>
    public static GovernanceClient Healthy(string statusBody = StatusBody)
        => Routed(HttpStatusCode.OK, HealthyBody, HttpStatusCode.OK, statusBody);

    public static GovernanceClient Degraded()
        => Routed(HttpStatusCode.ServiceUnavailable, DegradedBody, HttpStatusCode.OK, StatusBody);

    /// <summary>Health is fine; the authenticated read is refused.</summary>
    public static GovernanceClient RejectingCredential()
        => Routed(HttpStatusCode.OK, HealthyBody, HttpStatusCode.Unauthorized,
            """{"error":{"code":"unauthenticated","detail":"a valid bearer token is required"}}""");

    public static GovernanceClient ReturningUnparseableBody()
        => Routed(HttpStatusCode.OK, "not json at all", HttpStatusCode.OK, StatusBody);

    public static (GovernanceClient Client, StubHttpMessageHandler Handler) Recording(
        string? token = Token)
    {
        var handler = Routing(HttpStatusCode.OK, HealthyBody, HttpStatusCode.OK, StatusBody);
        return (Build(handler, token: token), handler);
    }

    private static GovernanceClient Routed(
        HttpStatusCode healthStatus, string healthBody,
        HttpStatusCode otherStatus, string otherBody)
        => Build(Routing(healthStatus, healthBody, otherStatus, otherBody));

    private static StubHttpMessageHandler Routing(
        HttpStatusCode healthStatus, string healthBody,
        HttpStatusCode otherStatus, string otherBody)
        => new(request =>
        {
            var isHealth = request.RequestUri!.AbsolutePath == "/health";
            return new HttpResponseMessage(isHealth ? healthStatus : otherStatus)
            {
                Content = new StringContent(
                    isHealth ? healthBody : otherBody,
                    System.Text.Encoding.UTF8,
                    "application/json")
            };
        });

    public static GovernanceClient Build(
        StubHttpMessageHandler handler,
        bool enabled = true,
        string? token = Token,
        string? tokenEnvVar = TokenEnvVar,
        string endpoint = Endpoint)
        => new(
            new HttpClient(handler),
            _ => Task.FromResult(new GovernanceConfiguration
            {
                Enabled = enabled,
                Endpoint = endpoint,
                TokenEnvVar = tokenEnvVar
            }),
            name => name == tokenEnvVar ? token : null,
            NullLogger<GovernanceClient>.Instance);

    /// <summary>A client whose configuration read itself fails.</summary>
    public static GovernanceClient WithBrokenConfiguration()
        => new(
            new HttpClient(StubHttpMessageHandler.Returning(HttpStatusCode.OK, HealthyBody)),
            _ => throw new IOException("config is unreadable"),
            _ => Token,
            NullLogger<GovernanceClient>.Instance);
}
