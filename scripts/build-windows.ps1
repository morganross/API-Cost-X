param(
    [string]$Version = "0.0.0-local"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$IsRunningOnWindows = [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
    [System.Runtime.InteropServices.OSPlatform]::Windows
)
if (-not $IsRunningOnWindows) {
    throw "scripts/build-windows.ps1 must be run on Windows."
}

if ($Version.StartsWith("v")) {
    $Version = $Version.Substring(1)
}
$SafeVersion = $Version -replace "[^0-9A-Za-z_.-]", "-"

function Require-Command {
    param([string]$Name)
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $Command) {
        throw "Required command not found: $Name"
    }
    return $Command.Source
}

function Find-InnoSetup {
    $Command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    $ProgramFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    $Candidates = @()
    if ($ProgramFilesX86) {
        $Candidates += Join-Path $ProgramFilesX86 "Inno Setup 6\ISCC.exe"
    }
    $ProgramFiles = [Environment]::GetEnvironmentVariable("ProgramFiles")
    if ($ProgramFiles) {
        $Candidates += Join-Path $ProgramFiles "Inno Setup 6\ISCC.exe"
    }

    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate) {
            return $Candidate
        }
    }

    throw "Inno Setup 6 is required to build APICostX-Setup.exe. Install it, then rerun this script."
}

$Python = Require-Command "python"
Require-Command "node" | Out-Null
Require-Command "npm" | Out-Null
$InnoSetup = Find-InnoSetup

$BuildVenv = Join-Path $Root ".venv-windows-build"
if (-not (Test-Path -LiteralPath (Join-Path $BuildVenv "Scripts\\python.exe"))) {
    & $Python -m venv $BuildVenv
}
$BuildPython = Join-Path $BuildVenv "Scripts\\python.exe"

& $BuildPython -m pip install --upgrade pip
& $BuildPython -m pip install -e (Join-Path $Root "api") pyinstaller==6.11.1

$WebDist = Join-Path $Root "assets\\react-build"
if (Test-Path -LiteralPath $WebDist) {
    Remove-Item -LiteralPath $WebDist -Recurse -Force
}

Push-Location (Join-Path $Root "web-gui")
try {
    npm ci
    npm run build
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath (Join-Path $WebDist "index.html"))) {
    throw "Web GUI build did not produce assets\\react-build\\index.html"
}

$DistApp = Join-Path $Root "dist\\APICostX"
if (Test-Path -LiteralPath $DistApp) {
    Remove-Item -LiteralPath $DistApp -Recurse -Force
}

& $BuildPython -m PyInstaller --noconfirm --clean (Join-Path $Root "installer\\pyinstaller\\apicostx.spec")

if (-not (Test-Path -LiteralPath (Join-Path $DistApp "APICostX.exe"))) {
    throw "PyInstaller did not produce dist\\APICostX\\APICostX.exe"
}

& $InnoSetup "/DMyAppVersion=$SafeVersion" (Join-Path $Root "installer\\inno\\APICostX.iss")

$Installer = Join-Path $Root "dist\\installer\\APICostX-Setup-$SafeVersion.exe"
if (-not (Test-Path -LiteralPath $Installer)) {
    throw "Inno Setup did not produce $Installer"
}

$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Installer
$HashLine = "$($Hash.Hash.ToLowerInvariant())  $(Split-Path -Leaf $Installer)"
$HashLine | Set-Content -Encoding ascii -LiteralPath (Join-Path $Root "dist\\installer\\SHA256SUMS-windows.txt")

Write-Host "Built $Installer"
Write-Host $HashLine
