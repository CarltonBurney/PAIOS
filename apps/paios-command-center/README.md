# PAIOS Command Center Container

.NET 8 web container for a single command-center view of local LLMs, agent tools, and operational browser surfaces.

## Run with Docker

```powershell
docker compose up --build
```

Open http://localhost:8080.

## Endpoints

- `/` serves the command-center shell.
- `/health` exposes the ASP.NET Core health check.
- `/api/workspaces` returns the workspace registry used by the shell.

This is the initial container contract. Add local LLM adapters and authenticated application routes behind the same shell as each integration becomes available. Keep API keys and tenant credentials in environment variables or a local secret store.
