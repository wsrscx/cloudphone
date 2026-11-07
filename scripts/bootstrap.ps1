[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$JavaHomePath = "",
    [string]$AndroidSdkPath = "",
    [string]$ProxyUrl = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$toolsRoot = Join-Path $repoRoot ".tools"
$venvRoot = Join-Path $repoRoot ".venv"
$androidRoot = Join-Path $repoRoot "android"
New-Item -ItemType Directory -Force -Path $toolsRoot | Out-Null

$oldHttpProxy = $env:HTTP_PROXY
$oldHttpsProxy = $env:HTTPS_PROXY
$oldGradleOptions = $env:GRADLE_OPTS
$oldGradleUserHome = $env:GRADLE_USER_HOME
$oldJavaHome = $env:JAVA_HOME
$oldAndroidSdkRoot = $env:ANDROID_SDK_ROOT
$oldPath = $env:PATH

try {

$env:GRADLE_USER_HOME = Join-Path $toolsRoot "gradle-user-home"

if ($ProxyUrl) {
    $proxy = [Uri]$ProxyUrl
    if ($proxy.Scheme -notin @("http", "https") -or -not $proxy.Host -or $proxy.Port -le 0) {
        throw "ProxyUrl must be an HTTP(S) URL with a host and port."
    }
    $env:HTTP_PROXY = $ProxyUrl
    $env:HTTPS_PROXY = $ProxyUrl
    $proxyOptions = "-Dhttp.proxyHost=$($proxy.Host) -Dhttp.proxyPort=$($proxy.Port) -Dhttps.proxyHost=$($proxy.Host) -Dhttps.proxyPort=$($proxy.Port)"
    $env:GRADLE_OPTS = if ($env:GRADLE_OPTS) { "$env:GRADLE_OPTS $proxyOptions" } else { $proxyOptions }
}

function Test-CompatiblePython([string]$Candidate) {
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) {
        return $false
    }
    $compatible = & $Candidate -c "import sys; print(int(sys.version_info >= (3, 11)))"
    return $LASTEXITCODE -eq 0 -and $compatible.Trim() -eq "1"
}

function Find-CompatiblePython {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    $candidates = @(
        $PythonExe,
        $(if ($pythonCommand) { $pythonCommand.Source } else { "" }),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-CompatiblePython $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Test-JavaHome([string]$Candidate) {
    if (-not $Candidate) {
        return $false
    }
    $java = Join-Path $Candidate "bin\java.exe"
    if (-not (Test-Path -LiteralPath $java)) {
        return $false
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Candidate "bin\jlink.exe"))) {
        return $false
    }
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $java
    $startInfo.Arguments = "-version"
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($startInfo)
    $versionLine = $process.StandardError.ReadLine()
    $process.WaitForExit()
    return $versionLine -match 'version "(17|18|19|20|21)'
}

function Find-JavaHome {
    $candidates = @(
        $JavaHomePath,
        $env:JAVA_HOME,
        "D:\MCLDownload\ext\jre-v64-220420\jdk17",
        "D:\DevEco Studio\jbr",
        "C:\Program Files\Android\Android Studio\jbr"
    )
    foreach ($candidate in $candidates) {
        if (Test-JavaHome $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Test-AndroidSdk([string]$Candidate) {
    if (-not $Candidate) {
        return $false
    }
    return (Test-Path -LiteralPath (Join-Path $Candidate "platforms\android-34\android.jar")) -and
        (Test-Path -LiteralPath (Join-Path $Candidate "platform-tools\adb.exe"))
}

function Find-AndroidSdk {
    $candidates = @(
        $AndroidSdkPath,
        $env:ANDROID_SDK_ROOT,
        $env:ANDROID_HOME,
        (Join-Path $env:LOCALAPPDATA "Android\Sdk"),
        (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "plan\android-sdk"),
        (Join-Path $toolsRoot "android-sdk")
    )
    foreach ($candidate in $candidates) {
        if (Test-AndroidSdk $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

$python = Find-CompatiblePython
if (-not $python) {
    throw "Python 3.11 or newer was not found. This script never installs or changes Python; pass -PythonExe explicitly."
}

$javaHome = Find-JavaHome
if (-not $javaHome) {
    throw "A JDK between 17 and 21 was not found. Pass -JavaHomePath with a compatible JDK."
}

$androidSdk = Find-AndroidSdk
if (-not $androidSdk) {
    throw "Android SDK 34 with platform-tools was not found. Pass -AndroidSdkPath."
}

if (-not (Test-Path -LiteralPath (Join-Path $venvRoot "Scripts\python.exe"))) {
    & $python -m venv $venvRoot
}
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
$requirements = Join-Path $repoRoot "server\requirements.txt"
if (Test-Path -LiteralPath $requirements) {
    & $venvPython -m pip install -r $requirements
}

New-Item -ItemType Directory -Force -Path $androidRoot | Out-Null
$escapedSdk = $androidSdk.Replace("\", "\\").Replace(":", "\:")
[IO.File]::WriteAllText((Join-Path $androidRoot "local.properties"), "sdk.dir=$escapedSdk`r`n")

$env:JAVA_HOME = $javaHome
$env:ANDROID_SDK_ROOT = $androidSdk
$env:PATH = "$(Join-Path $javaHome 'bin');$(Join-Path $androidSdk 'platform-tools');$env:PATH"

$wrapperJar = Join-Path $androidRoot "gradle\wrapper\gradle-wrapper.jar"
if ((Test-Path -LiteralPath (Join-Path $androidRoot "settings.gradle")) -and -not (Test-Path -LiteralPath $wrapperJar)) {
    $gradleVersion = "8.9"
    $gradleZip = Join-Path $toolsRoot "gradle-$gradleVersion-bin.zip"
    $gradleDir = Join-Path $toolsRoot "gradle-$gradleVersion"
    if (-not (Test-Path -LiteralPath (Join-Path $gradleDir "bin\gradle.bat"))) {
        $downloadUrl = "https://services.gradle.org/distributions/gradle-$gradleVersion-bin.zip"
        $checksumUrl = "$downloadUrl.sha256"
        $expected = (Invoke-RestMethod -Uri $checksumUrl).Trim().ToLowerInvariant()
        $actual = if (Test-Path -LiteralPath $gradleZip) {
            (Get-FileHash -LiteralPath $gradleZip -Algorithm SHA256).Hash.ToLowerInvariant()
        } else {
            ""
        }
        if ($expected -ne $actual) {
            $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
            if ($curl) {
                & $curl.Source --location --fail --retry 10 --retry-all-errors --retry-delay 2 --continue-at - --output $gradleZip $downloadUrl
                if ($LASTEXITCODE -ne 0) {
                    throw "Gradle download failed."
                }
            } else {
                Invoke-WebRequest -Uri $downloadUrl -OutFile $gradleZip
            }
        }
        $actual = (Get-FileHash -LiteralPath $gradleZip -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($expected -ne $actual) {
            throw "Gradle checksum mismatch."
        }
        Expand-Archive -LiteralPath $gradleZip -DestinationPath $toolsRoot -Force
    }
    & (Join-Path $gradleDir "bin\gradle.bat") -p $androidRoot wrapper --gradle-version $gradleVersion --distribution-type bin
}

Write-Host "Environment ready."
Write-Host "Python: $venvPython"
Write-Host "JAVA_HOME: $javaHome"
Write-Host "ANDROID_SDK_ROOT: $androidSdk"
} finally {
    $env:HTTP_PROXY = $oldHttpProxy
    $env:HTTPS_PROXY = $oldHttpsProxy
    $env:GRADLE_OPTS = $oldGradleOptions
    $env:GRADLE_USER_HOME = $oldGradleUserHome
    $env:JAVA_HOME = $oldJavaHome
    $env:ANDROID_SDK_ROOT = $oldAndroidSdkRoot
    $env:PATH = $oldPath
}
