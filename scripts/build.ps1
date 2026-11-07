[CmdletBinding()]
param(
    [string]$JavaHomePath = "",
    [string]$AndroidSdkPath = "",
    [switch]$SkipPythonTests,
    [switch]$SkipScrcpy,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$androidRoot = Join-Path $repoRoot "android"
$distRoot = Join-Path $repoRoot "dist"
$dataRoot = Join-Path $repoRoot "data"
$toolsRoot = Join-Path $repoRoot ".tools"
$python312 = Join-Path $repoRoot ".venv312\Scripts\python.exe"
$pythonLegacy = Join-Path $repoRoot ".venv\Scripts\python.exe"
$venvPython = if (Test-Path -LiteralPath $python312) { $python312 } else { $pythonLegacy }

function Test-Jdk([string]$Path) {
    return $Path -and
        (Test-Path -LiteralPath (Join-Path $Path "bin\java.exe")) -and
        (Test-Path -LiteralPath (Join-Path $Path "bin\javac.exe")) -and
        (Test-Path -LiteralPath (Join-Path $Path "bin\jlink.exe")) -and
        (Test-Path -LiteralPath (Join-Path $Path "bin\keytool.exe"))
}

function Find-Jdk {
    $candidates = @(
        $JavaHomePath,
        $env:JAVA_HOME,
        (Join-Path $toolsRoot "jdk-21\jdk-21.0.11+10"),
        "D:\MCLDownload\ext\jre-v64-220420\jdk17",
        "C:\Program Files\Android\Android Studio\jbr",
        "C:\Program Files\Java\jdk-21"
    )
    foreach ($candidate in $candidates) {
        if (Test-Jdk $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "A complete JDK 17-21 with jlink and keytool was not found. Pass -JavaHomePath."
}

function Test-Sdk([string]$Path) {
    return $Path -and
        (Test-Path -LiteralPath (Join-Path $Path "platforms\android-34\android.jar")) -and
        (Test-Path -LiteralPath (Join-Path $Path "platform-tools\adb.exe"))
}

function Find-Sdk {
    $candidates = @(
        $AndroidSdkPath,
        $env:ANDROID_SDK_ROOT,
        $env:ANDROID_HOME,
        (Join-Path $toolsRoot "android-sdk"),
        (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "plan\android-sdk"),
        (Join-Path $env:LOCALAPPDATA "Android\Sdk")
    )
    foreach ($candidate in $candidates) {
        if (Test-Sdk $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Android SDK 34 with platform-tools was not found. Pass -AndroidSdkPath."
}

function Convert-ToMappedRepoPath([string]$Path, [string]$MappedRoot) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $repoPrefix = $repoRoot.TrimEnd("\") + "\"
    if (-not $absolute.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        return $absolute
    }
    $relative = $absolute.Substring($repoRoot.Length).TrimStart("\")
    return Join-Path $MappedRoot $relative
}

function New-AsciiRepoMapping {
    foreach ($code in 90..80) {
        $drive = "$([char]$code):"
        if (-not (Test-Path -LiteralPath "$drive\")) {
            & (Join-Path $env:SystemRoot "System32\subst.exe") $drive $repoRoot
            if ($LASTEXITCODE -eq 0) {
                return $drive
            }
        }
    }
    throw "No free drive letter is available for the ASCII scrcpy build mapping."
}

function New-SigningConfiguration([string]$JavaHome) {
    $propertiesPath = Join-Path $androidRoot "signing.properties"
    $keystorePath = Join-Path $dataRoot "cloudphone-release.keystore"
    if ((Test-Path -LiteralPath $propertiesPath) -and (Test-Path -LiteralPath $keystorePath)) {
        return
    }
    if ((Test-Path -LiteralPath $propertiesPath) -or (Test-Path -LiteralPath $keystorePath)) {
        throw "Android signing files are incomplete. Restore or remove both signing files."
    }
    New-Item -ItemType Directory -Force $dataRoot | Out-Null
    $passwordBytes = New-Object byte[] 24
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($passwordBytes)
    } finally {
        $random.Dispose()
    }
    $password = [Convert]::ToBase64String($passwordBytes).Replace("=", "").Replace("/", "A").Replace("+", "B")
    $keytool = Join-Path $JavaHome "bin\keytool.exe"
    & $keytool -genkeypair -keystore $keystorePath -storepass $password -keypass $password `
        -alias cloudphone -keyalg EC -groupname secp256r1 -validity 3650 `
        -dname "CN=CloudPhone Client, OU=Local, O=CloudPhone, C=CN"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to generate the Android release signing key."
    }
    [IO.File]::WriteAllLines($propertiesPath, @(
        "storeFile=../data/cloudphone-release.keystore",
        "storePassword=$password",
        "keyAlias=cloudphone",
        "keyPassword=$password"
    ), [Text.UTF8Encoding]::new($false))
}

$javaHome = Find-Jdk
$androidSdk = Find-Sdk
New-Item -ItemType Directory -Force $distRoot | Out-Null
New-SigningConfiguration $javaHome
if (-not (Test-Path -LiteralPath (Join-Path $androidRoot "client-secrets.properties"))) {
    throw "Client configuration is missing. Run scripts/configure.ps1 before building."
}

if (-not $SkipPythonTests) {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Project Python environment is missing. Run scripts/bootstrap.ps1 first."
    }
    $oldPythonPath = $env:PYTHONPATH
    try {
        $pythonPaths = @(Join-Path $repoRoot "server")
        if ($venvPython -eq $python312) {
            $bundledSitePackages = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages"
            $legacySitePackages = Join-Path $repoRoot ".venv\Lib\site-packages"
            if (-not (Test-Path -LiteralPath $bundledSitePackages) -or
                -not (Test-Path -LiteralPath $legacySitePackages)) {
                throw "Python 3.12 compatibility dependencies are missing. Recreate the project environment."
            }
            $pythonPaths += $bundledSitePackages, $legacySitePackages
        }
        $env:PYTHONPATH = $pythonPaths -join [IO.Path]::PathSeparator
        & $venvPython -m pytest (Join-Path $repoRoot "server\tests") -q
        if ($LASTEXITCODE -ne 0) { throw "Python tests failed." }
        & $venvPython -m compileall -q (Join-Path $repoRoot "server\cloudphone")
        if ($LASTEXITCODE -ne 0) { throw "Python compile check failed." }
    } finally {
        $env:PYTHONPATH = $oldPythonPath
    }
}

$oldJavaHome = $env:JAVA_HOME
$oldAndroidSdkRoot = $env:ANDROID_SDK_ROOT
$oldAndroidHome = $env:ANDROID_HOME
$oldGradleUserHome = $env:GRADLE_USER_HOME
$mapping = $null
try {
    $mapping = New-AsciiRepoMapping
    $mappedRoot = "$mapping\"
    $mappedAndroidRoot = Join-Path $mappedRoot "android"
    $mappedToolsRoot = Join-Path $mappedRoot ".tools"
    $mappedJavaHome = Convert-ToMappedRepoPath $javaHome $mappedRoot
    $mappedAndroidSdk = Convert-ToMappedRepoPath $androidSdk $mappedRoot

    $env:JAVA_HOME = $mappedJavaHome
    $env:ANDROID_SDK_ROOT = $mappedAndroidSdk
    $env:ANDROID_HOME = $mappedAndroidSdk
    $env:GRADLE_USER_HOME = Join-Path $mappedToolsRoot "gradle-user-home"
    $escapedSdk = $mappedAndroidSdk.Replace("\", "\\").Replace(":", "\:")
    [IO.File]::WriteAllText(
        (Join-Path $mappedAndroidRoot "local.properties"),
        "sdk.dir=$escapedSdk" + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )

    $gradle = Join-Path $mappedToolsRoot "gradle-8.9\bin\gradle.bat"
    if (-not (Test-Path -LiteralPath $gradle)) {
        $gradle = Join-Path $mappedAndroidRoot "gradlew.bat"
    }
    $gradleArguments = @("-p", $mappedAndroidRoot, "--no-daemon", "--max-workers=1")
    if ($Offline) { $gradleArguments += "--offline" }
    $gradleArguments += @("test", "copyAndroidArtifacts")
    & $gradle @gradleArguments
    if ($LASTEXITCODE -ne 0) { throw "Android build failed." }

    if (-not $SkipScrcpy) {
        & (Join-Path $mappedRoot "third_party\scrcpy\build-direct-server.ps1") `
            -OutputPath (Join-Path $mappedRoot "dist\scrcpy-server-direct-v4.1") `
            -BuildRootPath (Join-Path $mappedRoot "third_party\scrcpy\.work\direct-build") `
            -JavaHomePath $mappedJavaHome `
            -AndroidSdkPath $mappedAndroidSdk `
            -Offline:$Offline
        if ($LASTEXITCODE -ne 0) { throw "scrcpy server build failed." }
    }

    Copy-Item -LiteralPath (Join-Path $repoRoot "third_party\scrcpy\LICENSE") `
        -Destination (Join-Path $distRoot "scrcpy-Apache-2.0-LICENSE.txt") -Force

    Write-Host "Build complete:"
    @(
        "cloudphone-client.apk",
        "cloudphone-remote-ime.apk",
        "scrcpy-server-direct-v4.1",
        "scrcpy-Apache-2.0-LICENSE.txt"
    ) | ForEach-Object {
        Get-Item -LiteralPath (Join-Path $distRoot $_)
    } | Select-Object Name, Length, LastWriteTime
} finally {
    $env:JAVA_HOME = $oldJavaHome
    $env:ANDROID_SDK_ROOT = $oldAndroidSdkRoot
    $env:ANDROID_HOME = $oldAndroidHome
    $env:GRADLE_USER_HOME = $oldGradleUserHome
    if ($mapping) {
        & (Join-Path $env:SystemRoot "System32\subst.exe") $mapping /d | Out-Null
    }
}
