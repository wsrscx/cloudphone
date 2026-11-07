[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python312 = Join-Path $repoRoot ".venv312\Scripts\python.exe"
$pythonLegacy = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $python312) { $python312 } else { $pythonLegacy }
$environmentFile = Join-Path $repoRoot "data\server.env"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python environment is missing. Run scripts/bootstrap.ps1 first."
}
if (-not (Test-Path -LiteralPath $environmentFile)) {
    throw "Server configuration is missing. Run scripts/configure.ps1 first."
}
$scrcpyServer = Join-Path $repoRoot "dist\scrcpy-server-direct-v4.1"
if (-not (Test-Path -LiteralPath $scrcpyServer)) {
    throw "Upstream scrcpy direct server is missing. Run scripts/build.ps1 first."
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "dist\cloudphone-remote-ime.apk"))) {
    throw "Remote IME APK is missing. Run scripts/build.ps1 first."
}

$oldPythonPath = $env:PYTHONPATH
$oldScrcpyServerPath = $env:CLOUDPHONE_SCRCPY_SERVER_PATH
try {
    $pythonPaths = @(Join-Path $repoRoot "server")
    if ($python -eq $python312) {
        $bundledSitePackages = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages"
        $legacySitePackages = Join-Path $repoRoot ".venv\Lib\site-packages"
        if (-not (Test-Path -LiteralPath $bundledSitePackages) -or
            -not (Test-Path -LiteralPath $legacySitePackages)) {
            throw "Python 3.12 compatibility dependencies are missing. Recreate the project environment."
        }
        $pythonPaths += $bundledSitePackages, $legacySitePackages
    }
    $env:PYTHONPATH = $pythonPaths -join [IO.Path]::PathSeparator
    # The direct-stream runtime must not inherit a legacy custom scrcpy path
    # from an existing server.env file.
    $env:CLOUDPHONE_SCRCPY_SERVER_PATH = $scrcpyServer
    & $python -m cloudphone.main
    if ($LASTEXITCODE -ne 0) {
        throw "CloudPhone server exited with code $LASTEXITCODE."
    }
} finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:CLOUDPHONE_SCRCPY_SERVER_PATH = $oldScrcpyServerPath
}
