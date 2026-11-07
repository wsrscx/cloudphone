[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PublicIp,
    [int]$PublicPort = 8443,
    [string]$AdminPassword = "",
    [string]$AdbPath = "",
    [switch]$PreserveIdentity,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python312 = Join-Path $repoRoot ".venv312\Scripts\python.exe"
$pythonLegacy = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $python312) { $python312 } else { $pythonLegacy }
if (-not (Test-Path -LiteralPath $python)) {
    throw "Run scripts/bootstrap.ps1 first."
}

$arguments = @(
    (Join-Path $PSScriptRoot "configure.py"),
    "--public-ip", $PublicIp,
    "--public-port", $PublicPort
)
if ($AdminPassword) {
    $arguments += @("--admin-password", $AdminPassword)
}
if ($AdbPath) {
    $arguments += @("--adb-path", $AdbPath)
}
if ($Force) {
    $arguments += "--force"
}
if ($PreserveIdentity) {
    $arguments += "--preserve-identity"
}

$oldPythonPath = $env:PYTHONPATH
try {
    if ($python -eq $python312) {
        $bundledSitePackages = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages"
        $legacySitePackages = Join-Path $repoRoot ".venv\Lib\site-packages"
        $env:PYTHONPATH = @($bundledSitePackages, $legacySitePackages) -join [IO.Path]::PathSeparator
    }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Configuration generation failed."
    }
} finally {
    $env:PYTHONPATH = $oldPythonPath
}
