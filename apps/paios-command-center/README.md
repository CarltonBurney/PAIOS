# PAIOS Command Center Container

.NET 8 web container for a single command-center view of local LLMs, agent tools, and operational browser surfaces.

## Run

```powershell
cd apps\paios-command-center
.\scripts\start.ps1
```

The launcher waits for the Docker engine and falls back to the local .NET 8
SDK if Docker Desktop is unavailable. Force one runtime with `-Mode Docker` or
`-Mode Dotnet`. See [scripts/README.md](scripts/README.md) for Docker Desktop
recovery steps.

Either runtime serves http://localhost:8080.

To drive the runtimes directly:

```powershell
docker compose up --build                        # container
dotnet run --project Paios.CommandCenter.csproj  # local SDK
```

## Endpoints

- `/` serves the command-center shell.
- `/health` exposes the ASP.NET Core health check.
- `/api/workspaces` returns the workspace registry used by the shell.

This is the initial container contract. Add local LLM adapters and authenticated application routes behind the same shell as each integration becomes available. Keep API keys and tenant credentials in environment variables or a local secret store.
