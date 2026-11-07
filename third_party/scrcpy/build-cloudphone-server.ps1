[CmdletBinding()]
param(
    [string]$OutputPath = (Join-Path $PSScriptRoot "out\scrcpy-server-cloudphone-v4.1"),
    [string]$JavaHomePath = "",
    [string]$AndroidSdkPath = "",
    [string]$BuildRootPath = "",
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$sourceUrl = "https://codeload.github.com/Genymobile/scrcpy/tar.gz/refs/tags/v4.1"
$expectedSourceHash = "537b2ade623cb94b6edddfa5c61bf0b0af21484aa8365ea2531b686ea573249a"
$archiveRoot = Join-Path $PSScriptRoot ".work"
$patchPath = Join-Path $PSScriptRoot "cloudphone-v4.1.patch"
$patchHash = (Get-FileHash -LiteralPath $patchPath -Algorithm SHA256).Hash.ToLowerInvariant()
$workBase = if ($BuildRootPath) { $BuildRootPath } else { $env:TEMP }
$workRoot = Join-Path $workBase ("cloudphone-scrcpy-v4.1-" + $patchHash.Substring(0, 12))
$archivePath = Join-Path $archiveRoot "scrcpy-v4.1.tar.gz"
$sourceRoot = Join-Path $workRoot "scrcpy-4.1"

if ($JavaHomePath) {
    $env:JAVA_HOME = (Resolve-Path -LiteralPath $JavaHomePath).Path
}
if ($AndroidSdkPath) {
    $env:ANDROID_SDK_ROOT = (Resolve-Path -LiteralPath $AndroidSdkPath).Path
    $env:ANDROID_HOME = $env:ANDROID_SDK_ROOT
}
if (-not $env:JAVA_HOME -or -not (Test-Path -LiteralPath (Join-Path $env:JAVA_HOME "bin\jlink.exe"))) {
    throw "A complete JDK with jlink is required. Pass -JavaHomePath."
}
if (-not $env:ANDROID_SDK_ROOT -or -not (Test-Path -LiteralPath (Join-Path $env:ANDROID_SDK_ROOT "platforms\android-34\android.jar"))) {
    throw "Android SDK platform 34 is required to build the pinned scrcpy server."
}

New-Item -ItemType Directory -Path $archiveRoot -Force | Out-Null
New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $archivePath)) {
    Invoke-WebRequest -Uri $sourceUrl -OutFile $archivePath -UseBasicParsing
}
$actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedSourceHash) {
    throw "scrcpy v4.1 source hash mismatch: $actualHash"
}

if (-not (Test-Path -LiteralPath $sourceRoot)) {
    tar -xzf $archivePath -C $workRoot
}

$controlMessagePath = Join-Path $sourceRoot "server\src\main\java\com\genymobile\scrcpy\control\ControlMessage.java"
$alreadyPatched = Select-String -LiteralPath $controlMessagePath -SimpleMatch "TYPE_SET_VIDEO_QUALITY" -Quiet
if (-not $alreadyPatched) {
    & git -C $sourceRoot apply --check --recount $patchPath
    if ($LASTEXITCODE -ne 0) {
        throw "The cloud-phone patch does not apply cleanly to pinned scrcpy v4.1"
    }
    & git -C $sourceRoot apply --recount $patchPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to apply the cloud-phone patch"
    }
}

$projectGradle = Join-Path $PSScriptRoot "..\..\.tools\gradle-8.9\bin\gradle.bat"
$gradle = if (Test-Path -LiteralPath $projectGradle) {
    (Resolve-Path -LiteralPath $projectGradle).Path
} else {
    Join-Path $sourceRoot "gradlew.bat"
}
$gradleArguments = @("-p", $sourceRoot, "--no-daemon")
if ($Offline) { $gradleArguments += "--offline" }
$gradleArguments += @(":server:testDebugUnitTest", ":server:assembleRelease")
& $gradle @gradleArguments
if ($LASTEXITCODE -ne 0) {
    throw "scrcpy server build failed"
}

$builtServer = Join-Path $sourceRoot "server\build\outputs\apk\release\server-release-unsigned.apk"
if (-not (Test-Path -LiteralPath $builtServer)) {
    throw "Gradle completed without producing the expected server artifact"
}
New-Item -ItemType Directory -Path (Split-Path -Parent $OutputPath) -Force | Out-Null
Copy-Item -LiteralPath $builtServer -Destination $OutputPath -Force
Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256
