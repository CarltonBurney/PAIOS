# Launcher and Docker Desktop recovery

`start.ps1` starts the Command Center on whichever runtime is actually
available, so a stalled Docker Desktop does not block local work.

```powershell
cd apps\paios-command-center
.\scripts\start.ps1              # wait for Docker, fall back to the .NET SDK
.\scripts\start.ps1 -Mode Docker # require Docker, fail if the engine is unhealthy
.\scripts\start.ps1 -Mode Dotnet # skip Docker entirely
```

Both runtimes serve <http://localhost:8080>.

## Why the script exists

Two Docker Desktop behaviours break a plain `docker compose up --build`:

- **A restart drops `docker` from the current shell's PATH.** The executable is
  still installed; only the session's PATH is stale. The script resolves
  `docker.exe` from PATH first and then from the standard install locations, so
  it keeps working in the shell that was already open.
- **The UI reports "Running" before the engine API answers.** CLI calls against
  a half-started engine block indefinitely instead of failing. Every engine
  probe here runs under a hard timeout and is retried until `-TimeoutSeconds`
  (default 90) elapses.

## If the engine never becomes healthy

Work through these in order, from an elevated PowerShell window:

1. **Pick up the refreshed PATH.** Open a new PowerShell window rather than
   reusing the one that was open across the restart.
2. **Confirm the backend is running.** Docker Desktop on Windows needs either
   WSL 2 or Hyper-V:
   ```powershell
   wsl --status
   wsl --update
   ```
3. **Check the engine's own view of startup:**
   ```powershell
   & "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe" info
   Get-Content "$env:APPDATA\Docker\log\host\com.docker.backend.exe.log" -Tail 50
   ```
4. **Restart the service and the app:**
   ```powershell
   Restart-Service com.docker.service
   Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
   ```
5. **Reset to factory defaults** from Docker Desktop -> Settings ->
   Troubleshoot, as a last resort. This removes local images and volumes; this
   project rebuilds from the Dockerfile, so nothing here is lost.

Meanwhile, `-Mode Dotnet` needs only the .NET 8 SDK and reproduces the same
routes the container serves.
