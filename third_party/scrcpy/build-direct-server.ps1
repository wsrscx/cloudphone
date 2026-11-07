[CmdletBinding()]
param(
    [string]$OutputPath = "",
    [string]$JavaHomePath = "",
    [string]$AndroidSdkPath = "",
    [string]$BuildRootPath = "",
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
if (-not $OutputPath) { $OutputPath = Join-Path $PSScriptRoot "out\scrcpy-server-direct-v4.1" }
if (-not $BuildRootPath) { $BuildRootPath = Join-Path $PSScriptRoot ".work\direct-build" }
$archivePath = Join-Path $PSScriptRoot "scrcpy-v4.1.tar.gz"
$expectedSourceHash = "537b2ade623cb94b6edddfa5c61bf0b0af21484aa8365ea2531b686ea573249a"
$patchPath = Join-Path $PSScriptRoot "direct-build-v4.1.patch"
$patchHash = (Get-FileHash -LiteralPath $patchPath -Algorithm SHA256).Hash.ToLowerInvariant()
$sourceRoot = Join-Path $BuildRootPath ("scrcpy-4.1-" + $patchHash.Substring(0, 12))

if ($JavaHomePath) { $env:JAVA_HOME = (Resolve-Path -LiteralPath $JavaHomePath).Path }
if ($AndroidSdkPath) {
    $env:ANDROID_SDK_ROOT = (Resolve-Path -LiteralPath $AndroidSdkPath).Path
    $env:ANDROID_HOME = $env:ANDROID_SDK_ROOT
}
if (-not $env:JAVA_HOME -or -not (Test-Path -LiteralPath (Join-Path $env:JAVA_HOME "bin\jlink.exe"))) {
    throw "A complete JDK with jlink is required. Pass -JavaHomePath."
}
if (-not $env:ANDROID_SDK_ROOT -or -not (Test-Path -LiteralPath (Join-Path $env:ANDROID_SDK_ROOT "platforms\android-34\android.jar"))) {
    throw "Android SDK platform 34 is required. Pass -AndroidSdkPath."
}
if (-not (Test-Path -LiteralPath $archivePath)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $archivePath) | Out-Null
    Write-Host "Downloading pinned scrcpy v4.1 source archive..."
    Invoke-WebRequest -Uri "https://codeload.github.com/Genymobile/scrcpy/tar.gz/refs/tags/v4.1" -OutFile $archivePath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $archivePath)) {
        throw "Unable to download the pinned scrcpy source archive."
    }
}
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedSourceHash) {
    throw "Pinned scrcpy v4.1 source hash mismatch."
}

$sourceMarker = Join-Path $sourceRoot "server\src\main\java\com\genymobile\scrcpy\Server.java"
if (-not (Test-Path -LiteralPath $sourceMarker)) {
    New-Item -ItemType Directory -Path $sourceRoot -Force | Out-Null
    tar -xzf $archivePath -C $sourceRoot --strip-components=1
    if ($LASTEXITCODE -ne 0) { throw "Unable to extract pinned scrcpy source." }
    if (-not (Test-Path -LiteralPath $sourceMarker)) {
        throw "Pinned scrcpy source extraction is incomplete."
    }
}
$rootBuild = Join-Path $sourceRoot "build.gradle"
$alreadyPatched = Select-String -LiteralPath $rootBuild -SimpleMatch "gradle:8.7.3" -Quiet
if (-not $alreadyPatched) {
    $oldGitCeiling = $env:GIT_CEILING_DIRECTORIES
    try {
        # The build tree lives below the project worktree. Stop Git from
        # resolving the parent repository so patch paths stay source-relative.
        $env:GIT_CEILING_DIRECTORIES = Split-Path -Parent $sourceRoot
        & git -C $sourceRoot apply --check --recount $patchPath
        if ($LASTEXITCODE -ne 0) { throw "The direct-stream build patch does not apply cleanly." }
        & git -C $sourceRoot apply --recount $patchPath
        if ($LASTEXITCODE -ne 0) { throw "Unable to apply the direct-stream build patch." }
    } finally {
        $env:GIT_CEILING_DIRECTORIES = $oldGitCeiling
    }
}

$gradle = Join-Path $PSScriptRoot "..\..\.tools\gradle-8.9\bin\gradle.bat"
$env:GRADLE_USER_HOME = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\.tools\gradle-user-home")).Path
$gradleProperties = Join-Path $sourceRoot "gradle.properties"
if (-not (Test-Path -LiteralPath $gradleProperties) -or
    -not (Select-String -LiteralPath $gradleProperties -SimpleMatch "android.overridePathCheck=true" -Quiet)) {
    [IO.File]::AppendAllText($gradleProperties, "android.overridePathCheck=true" + [Environment]::NewLine)
}
$arguments = @("-p", $sourceRoot, "--no-daemon", "--max-workers=1")
if ($Offline) { $arguments += "--offline" }
$arguments += @(":server:testDebugUnitTest", ":server:assembleRelease", ":server:lintVitalRelease")
& $gradle @arguments
if ($LASTEXITCODE -ne 0) { throw "Upstream scrcpy direct-stream build failed." }

$builtServer = Join-Path $sourceRoot "server\build\outputs\apk\release\server-release-unsigned.apk"
if (-not (Test-Path -LiteralPath $builtServer)) {
    throw "Gradle did not produce the upstream scrcpy server artifact."
}
New-Item -ItemType Directory -Path (Split-Path -Parent $OutputPath) -Force | Out-Null
Copy-Item -LiteralPath $builtServer -Destination $OutputPath -Force
Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256
