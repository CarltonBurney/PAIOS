var builder = WebApplication.CreateBuilder(args);
builder.Services.AddHealthChecks();

var app = builder.Build();

app.UseDefaultFiles();
app.UseStaticFiles();
app.MapHealthChecks("/health");
app.MapGet("/api/workspaces", () => Results.Ok(new[]
{
    new { id = "dashboard", name = "Command Center", kind = "local" },
    new { id = "helpdesk", name = "Helpdesk Agent", kind = "simulation" },
    new { id = "security", name = "Security Posture", kind = "simulation" },
    new { id = "certificates", name = "Certificate Monitor", kind = "simulation" }
}));

app.Run();
