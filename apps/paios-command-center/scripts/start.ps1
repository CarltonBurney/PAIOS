<#
.SYNOPSIS
    Starts the PAIOS Command Center, with or without Docker Desktop.

.DESCRIPTION
    Docker Desktop restarts frequently drop docker.exe out of the current
    shell's PATH, and the engine can report "Running" in the UI while the API
    is still initializing. Every Docker call here resolves the executable
    directly and runs under a hard timeout so nothing hangs the terminal.

    If the engine does not become healthy in time, the script runs the app on
    the local .NET SDK instead. Both paths serve http://localhost:8080.

.PARAMETER Mode
    Auto    Wait for the Docker engine, fall back to the .NET SDK. (default)
    Docker  Require Docker; fail if the engine never becomes healthy.
    Dotnet  Skip Docker entirely and run on the local .NET SDK.

.PARAMETER TimeoutSeconds
    How long to wait for the Docker engine to answer. Default 90.

.EXAMPLE
    .\scripts\start.ps1
.EXAMPLE
    .\scripts\start.ps1 -Mode Dotnet
#>
[CmdletBinding()]
param(
    [ValidateSet('Auto', 'Docker', 'Dotnet')]
    [string]$Mode = 'Auto',

    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$appRoot = Split-Path -Parent $PSScriptRoot
$appUrl = 'http://localhost:8080'

function Resolve-DockerCli {
    # PATH first, then the locations Docker Desktop installs into. A restart
    # can strip the PATH entry while the executable is still perfectly usable.
    $onPath = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty Source
    if ($onPath) { return $onPath }

    $candidates = @(
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe",
        "$env:LOCALAPPDATA\Programs\Docker\Docker\resources\bin\docker.exe",
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "$env:ProgramData\DockerDesktop\version-bin\docker.exe"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }

    return $null
}

function Invoke-WithTimeout {
    <#
        Runs an executable and kills it if it exceeds $Seconds. Docker CLI calls
        block indefinitely against a half-started engine, so no engine probe may
        run unbounded.
    #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [int]$Seconds = 15
    )

    $stdout = New-TemporaryFile
    $stderr = New-TemporaryFile
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -PassThru -NoNewWindow `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr

        # Touching Handle caches it, without which Windows PowerShell can report
        # ExitCode as unavailable on a Start-Process -PassThru object.
        $null = $process.Handle

        if (-not $process.WaitForExit($Seconds * 1000)) {
            # Kill($true) kills the process tree but only exists on PowerShell 7+;
            # Windows PowerShell 5.1 falls back to killing the process itself.
            try { $process.Kill($true) } catch { try { $process.Kill() } catch { } }
            return [pscustomobject]@{ TimedOut = $true; ExitCode = $null; Output = '' }
        }

        return [pscustomobject]@{
            TimedOut = $false
            ExitCode = $process.ExitCode
            Output   = ([string](Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue))
        }
    }
    finally {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

function Wait-DockerEngine {
    param(
        [Parameter(Mandatory)][string]$DockerCli,
        [Parameter(Mandatory)][int]$Seconds
    )

    $deadline = (Get-Date).AddSeconds($Seconds)
    $probeSeconds = 15
    $attempt = 0

    while ((Get-Date) -lt $deadline) {
        $attempt++
        $result = Invoke-WithTimeout -FilePath $DockerCli -Arguments @('info', '--format', '{{.ServerVersion}}') -Seconds $probeSeconds

        if (-not $result.TimedOut -and $result.ExitCode -eq 0) {
            $version = $result.Output.Trim()
            if (-not $version) { $version = 'unknown' }
            Write-Host "Docker engine healthy (server $version)." -ForegroundColor Green
            return $true
        }

        $state = if ($result.TimedOut) { 'no response' } else { "exit code $($result.ExitCode)" }
        Write-Host "Waiting for the Docker engine... (attempt $attempt, $state)" -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }

    return $false
}

function Start-WithDocker {
    # Exits the script rather than returning: native command output flows to the
    # host as it streams, and a returned exit code would be tangled up with it.
    param([Parameter(Mandatory)][string]$DockerCli)

    Write-Host "Building and starting the container. Open $appUrl once the build finishes." -ForegroundColor Cyan
    Push-Location $appRoot
    try {
        & $DockerCli compose up --build
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

function Start-WithDotnet {
    $dotnet = Get-Command dotnet -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty Source
    if (-not $dotnet) {
        throw "The .NET SDK was not found. Install the .NET 8 SDK from https://dotnet.microsoft.com/download or start Docker Desktop and re-run with -Mode Docker."
    }

    Write-Host "Running on the local .NET SDK. Open $appUrl once the host starts." -ForegroundColor Cyan
    Push-Location $appRoot
    try {
        $env:ASPNETCORE_URLS = $appUrl
        $env:ASPNETCORE_ENVIRONMENT = 'Development'
        & $dotnet run --project Paios.CommandCenter.csproj
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

if ($Mode -eq 'Dotnet') {
    Start-WithDotnet
}

$dockerCli = Resolve-DockerCli
if (-not $dockerCli) {
    if ($Mode -eq 'Docker') {
        throw "docker.exe was not found on PATH or in the standard Docker Desktop install locations."
    }
    Write-Host "docker.exe not found; falling back to the local .NET SDK." -ForegroundColor Yellow
    Start-WithDotnet
}

Write-Host "Using Docker CLI at $dockerCli" -ForegroundColor DarkGray

if (Wait-DockerEngine -DockerCli $dockerCli -Seconds $TimeoutSeconds) {
    Start-WithDocker -DockerCli $dockerCli
}

if ($Mode -eq 'Docker') {
    throw "The Docker engine did not become healthy within $TimeoutSeconds seconds. See scripts/README.md for recovery steps, or re-run with -Mode Dotnet."
}

Write-Host "The Docker engine did not become healthy within $TimeoutSeconds seconds; falling back to the local .NET SDK." -ForegroundColor Yellow
Start-WithDotnet
