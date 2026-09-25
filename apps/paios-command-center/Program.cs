using System.Text.Json;
using Paios.CommandCenter.Configuration;
using Paios.CommandCenter.Dashboard;
using Paios.CommandCenter.Governance;
using Paios.CommandCenter.Operations;
using Paios.CommandCenter.Providers;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddHealthChecks();

// Provider probes must fail fast: a half-started local model server that never
// answers should surface as "unavailable", not hang the workspace.
builder.Services.AddHttpClient("providers", client => client.Timeout = TimeSpan.FromSeconds(5));

builder.Services.AddSingleton(sp => new ProviderConfigurationStore(
    Path.Combine(sp.GetRequiredService<IHostEnvironment>().ContentRootPath, "config", "providers.json"),
    sp.GetRequiredService<ILogger<ProviderConfigurationStore>>()));

builder.Services.AddSingleton(sp => new OllamaAdapter(
    sp.GetRequiredService<IHttpClientFactory>().CreateClient("providers")));

builder.Services.AddSingleton(sp => new OpenAiCompatibleAdapter(
    sp.GetRequiredService<IHttpClientFactory>().CreateClient("providers"),
    Environment.GetEnvironmentVariable));

builder.Services.AddSingleton<ProviderRegistry>();

// Operations: every checkable component registers one health check. The
// aggregator isolates failures, so adding a check cannot destabilize the view.
builder.Services.AddSingleton<IServiceHealthCheck>(sp =>
    new ApplicationHealthCheck(sp.GetRequiredService<IHostEnvironment>()));

builder.Services.AddSingleton<IServiceHealthCheck>(sp =>
    new ProviderHealthCheck(sp.GetRequiredService<ProviderRegistry>()));

builder.Services.AddSingleton<IServiceHealthCheck>(sp =>
{
    var contentRoot = sp.GetRequiredService<IHostEnvironment>().ContentRootPath;
    var servicesConfigPath = Path.Combine(contentRoot, "config", "services.json");
    var logger = sp.GetRequiredService<ILogger<Program>>();

    return new InfrastructureHealthCheck(
        async () =>
        {
            // Read per request so config edits apply without a restart, and a
            // malformed file degrades to "nothing declared" instead of throwing.
            try
            {
                if (!File.Exists(servicesConfigPath))
                {
                    return new OperationsConfiguration();
                }

                var json = await File.ReadAllTextAsync(servicesConfigPath);
                return JsonSerializer.Deserialize<OperationsConfiguration>(json) ?? new OperationsConfiguration();
            }
            catch (Exception ex) when (ex is JsonException or IOException or UnauthorizedAccessException)
            {
                logger.LogError(ex, "Could not read {Path}; monitoring nothing.", servicesConfigPath);
                return new OperationsConfiguration();
            }
        },
        sp.GetRequiredService<IHttpClientFactory>().CreateClient("providers"),
        TimeSpan.FromSeconds(3));
});

// Governance: the control plane is a separate process reached over HTTP, so it
// gets its own client with its own timeout. Kept short for the same reason the
// provider timeout is — a kernel that is starting up should read as unavailable
// rather than stall the dashboard.
builder.Services.AddHttpClient("governance", client => client.Timeout = TimeSpan.FromSeconds(5));

builder.Services.AddSingleton(sp => new GovernanceConfigurationStore(
    Path.Combine(sp.GetRequiredService<IHostEnvironment>().ContentRootPath, "config", "governance.json"),
    sp.GetRequiredService<ILogger<GovernanceConfigurationStore>>()));

builder.Services.AddSingleton(sp => new GovernanceClient(
    sp.GetRequiredService<IHttpClientFactory>().CreateClient("governance"),
    // Read per request so an endpoint or token-name change applies without a restart.
    cancellationToken => sp.GetRequiredService<GovernanceConfigurationStore>().LoadAsync(cancellationToken),
    Environment.GetEnvironmentVariable,
    sp.GetRequiredService<ILogger<GovernanceClient>>()));

builder.Services.AddSingleton<IServiceHealthCheck>(sp =>
    new GovernanceHealthCheck(sp.GetRequiredService<GovernanceClient>()));

builder.Services.AddSingleton<OperationsRegistry>();
builder.Services.AddSingleton<DashboardAggregator>();

var app = builder.Build();

app.UseDefaultFiles();
app.UseStaticFiles();
app.MapHealthChecks("/health");

// Workspace ids are the contract between the nav rail and every data route.
// They intentionally match the `data-workspace` attributes in index.html.
app.MapGet("/api/workspaces", () => Results.Ok(new[]
{
    new { id = "dashboard", name = "Command Center", kind = "aggregate" },
    new { id = "llm", name = "Local LLMs", kind = "providers" },
    new { id = "governance", name = "Governance", kind = "control_plane" },
    new { id = "agents", name = "Agent Lab", kind = "not_implemented" },
    new { id = "ops", name = "Operations", kind = "operations" }
}));

app.MapGet("/api/providers", async (ProviderRegistry registry, CancellationToken cancellationToken)
    => Results.Ok(await registry.GetProvidersAsync(cancellationToken)));

app.MapGet("/api/providers/{id}/health", async (string id, ProviderRegistry registry, CancellationToken cancellationToken) =>
{
    var provider = await registry.GetProviderAsync(id, cancellationToken);
    return provider is null ? Results.NotFound(new { error = $"Unknown provider '{id}'." }) : Results.Ok(provider);
});

app.MapGet("/api/providers/{id}/models", async (string id, ProviderRegistry registry, CancellationToken cancellationToken) =>
{
    var result = await registry.GetModelsAsync(id, cancellationToken);
    if (result is null)
    {
        return Results.NotFound(new { error = $"Unknown provider '{id}'." });
    }

    // A provider that cannot be read is reported as 200 with its status and
    // error attached — the workspace renders the failure rather than breaking.
    return Results.Ok(new
    {
        providerId = id,
        status = result.Status,
        errorMessage = result.ErrorMessage,
        models = result.Models
    });
});

app.MapPost("/api/providers", async (
    ProviderRegistry.RegistrationRequest request,
    ProviderRegistry registry,
    CancellationToken cancellationToken) =>
{
    var result = await registry.RegisterAsync(request, cancellationToken);
    return result.Success
        ? Results.Created($"/api/providers/{request.ProviderId}", result.Provider)
        : Results.BadRequest(new { error = result.Error });
});

app.MapGet("/api/dashboard", async (DashboardAggregator dashboard, CancellationToken cancellationToken)
    => Results.Ok(await dashboard.GetSnapshotAsync(cancellationToken)));

app.MapGet("/api/operations/services", async (OperationsRegistry operations, CancellationToken cancellationToken)
    => Results.Ok(await operations.GetSnapshotAsync(cancellationToken)));

app.MapGet("/api/operations/services/{id}", async (string id, OperationsRegistry operations, CancellationToken cancellationToken) =>
{
    var service = await operations.GetServiceAsync(id, cancellationToken);
    return service is null ? Results.NotFound(new { error = $"Unknown service '{id}'." }) : Results.Ok(service);
});

// Governance reads are proxied rather than exposed directly to the browser: the
// kernel's bearer token lives in this process's environment and must not reach a
// client. Every route below returns 200 with a status when the kernel is
// unreachable, so the workspace renders the failure instead of breaking.
app.MapGet("/api/governance/status", async (GovernanceClient governance, CancellationToken cancellationToken) =>
{
    var result = await governance.GetStatusAsync(cancellationToken);
    return Results.Ok(new
    {
        status = result.Status,
        errorMessage = result.ErrorMessage,
        endpoint = await governance.GetEndpointAsync(cancellationToken),
        control_plane = result.Value
    });
});

app.MapGet("/api/governance/health", async (GovernanceClient governance, CancellationToken cancellationToken) =>
{
    var result = await governance.GetHealthAsync(cancellationToken);
    return Results.Ok(new
    {
        status = result.Status,
        errorMessage = result.ErrorMessage,
        endpoint = await governance.GetEndpointAsync(cancellationToken),
        control_plane = result.Value
    });
});

app.MapGet("/api/governance/tools", async (GovernanceClient governance, CancellationToken cancellationToken) =>
{
    var result = await governance.GetToolsAsync(cancellationToken);
    return Results.Ok(new
    {
        status = result.Status,
        errorMessage = result.ErrorMessage,
        tools = result.Value?.Tools ?? [],
        detail = result.Value?.Detail
    });
});

// Submitting a request is a write against governance, so it is a POST and it
// carries no identity: the kernel resolves entitlements from this process's
// credential and ignores anything a body claims. A refusal comes back as 200
// with a disposition, because "you may not do this" is a successful answer.
app.MapPost("/api/governance/requests", async (
    GovernanceRequestBody body,
    GovernanceClient governance,
    CancellationToken cancellationToken) =>
{
    if (string.IsNullOrWhiteSpace(body.Content))
    {
        return Results.BadRequest(new { error = "'content' must be a non-empty string." });
    }

    var result = await governance.SubmitAsync(
        body.Content, body.Metadata, body.CorrelationId, cancellationToken);

    return Results.Ok(new
    {
        status = result.Status,
        errorMessage = result.ErrorMessage,
        decision = result.Value
    });
});

app.MapDelete("/api/providers/{id}", async (string id, ProviderRegistry registry, CancellationToken cancellationToken) =>
{
    var removed = await registry.RemoveAsync(id, cancellationToken);
    return removed
        ? Results.NoContent()
        : Results.NotFound(new { error = $"No configured provider '{id}'. Auto-detected providers cannot be removed; disable auto-detection in config/providers.json instead." });
});

app.Run();

/// <summary>
/// Body of a governance submission. Deliberately has no identity field — see the
/// route comment above.
/// </summary>
public sealed record GovernanceRequestBody(
    string? Content,
    Dictionary<string, object?>? Metadata,
    string? CorrelationId);

// Exposed so the test project can spin up the real application host.
public partial class Program;
