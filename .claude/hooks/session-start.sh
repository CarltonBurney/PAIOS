#!/bin/bash
# Installs the toolchain this repository needs but that the base image lacks:
# the .NET 8 SDK (apps/paios-command-center) and PowerShell 7 (its launcher
# script). Without these, a web session cannot build, run, or even parse-check
# the Command Center.
#
# Remote-only: local machines are assumed to have their own setup.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

PWSH_VERSION="7.4.6"
PWSH_ROOT="/opt/microsoft/powershell/7"

install_dotnet() {
  if command -v dotnet >/dev/null 2>&1; then
    echo "dotnet already present: $(dotnet --version)"
    return
  fi

  echo "Installing .NET 8 SDK..."
  export DEBIAN_FRONTEND=noninteractive
  sudo -n true 2>/dev/null && SUDO=sudo || SUDO=""
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq dotnet-sdk-8.0
  echo "dotnet installed: $(dotnet --version)"
}

install_powershell() {
  if command -v pwsh >/dev/null 2>&1; then
    echo "pwsh already present: $(pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()')"
    return
  fi

  echo "Installing PowerShell ${PWSH_VERSION}..."
  sudo -n true 2>/dev/null && SUDO=sudo || SUDO=""
  tarball="$(mktemp -d)/powershell.tar.gz"
  curl -fsSL --retry 3 -o "$tarball" \
    "https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VERSION}/powershell-${PWSH_VERSION}-linux-x64.tar.gz"
  $SUDO mkdir -p "$PWSH_ROOT"
  $SUDO tar -xzf "$tarball" -C "$PWSH_ROOT"
  $SUDO chmod +x "$PWSH_ROOT/pwsh"
  $SUDO ln -sf "$PWSH_ROOT/pwsh" /usr/local/bin/pwsh
  rm -f "$tarball"
  echo "pwsh installed: $(pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()')"
}

install_dotnet
install_powershell

# Warm the NuGet cache and prove the project compiles, so the first build in a
# session is fast and a broken restore surfaces here rather than mid-task.
if [ -f "${CLAUDE_PROJECT_DIR:-.}/apps/paios-command-center/Paios.CommandCenter.csproj" ]; then
  echo "Restoring Command Center packages..."
  dotnet restore "${CLAUDE_PROJECT_DIR:-.}/apps/paios-command-center/Paios.CommandCenter.csproj" --nologo -v q
fi

echo "Session toolchain ready."
