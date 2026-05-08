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

    $Candidates = @()
    $ProgramFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
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

function Copy-TreeClean {
    param(
        [string]$Source,
        [string]$Destination
    )

    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    robocopy $Source $Destination /E /XD __pycache__ .pytest_cache .mypy_cache .ruff_cache /XF *.pyc *.pyo | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed copying $Source to $Destination with exit code $LASTEXITCODE"
    }
}

function Compile-Launcher {
    param(
        [string]$SourcePath,
        [string]$OutputPath
    )

    Add-Type -TypeDefinition (Get-Content -Raw -LiteralPath $SourcePath) `
        -OutputAssembly $OutputPath `
        -OutputType ConsoleApplication `
        -ReferencedAssemblies @("System.dll")
}

$Python = Require-Command "python"
Require-Command "node" | Out-Null
Require-Command "npm" | Out-Null
$InnoSetup = Find-InnoSetup

$DistRoot = Join-Path $Root "dist"
$DistApp = Join-Path $DistRoot "APICostX"
$RuntimePython = Join-Path $DistApp "runtime\python"
$SitePackages = Join-Path $RuntimePython "Lib\site-packages"
$WebDist = Join-Path $Root "assets\react-build"

if (Test-Path -LiteralPath $DistApp) {
    Remove-Item -LiteralPath $DistApp -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $DistApp,$RuntimePython,$SitePackages | Out-Null

Push-Location (Join-Path $Root "web-gui")
try {
    npm ci
    npm run build
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath (Join-Path $WebDist "index.html"))) {
    throw "Web GUI build did not produce assets\react-build\index.html"
}

$PythonVersion = & $Python -c "import platform; print(platform.python_version())"
$EmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$EmbedZip = Join-Path $env:TEMP "python-$PythonVersion-embed-amd64.zip"
Invoke-WebRequest -Uri $EmbedUrl -OutFile $EmbedZip
Expand-Archive -LiteralPath $EmbedZip -DestinationPath $RuntimePython -Force

$PthFile = Get-ChildItem -LiteralPath $RuntimePython -Filter "python*._pth" | Select-Object -First 1
if (-not $PthFile) {
    throw "Could not find Python embedded ._pth file in $RuntimePython"
}
@(
    "python$($PythonVersion.Split('.')[0])$($PythonVersion.Split('.')[1]).zip",
    ".",
    "Lib\site-packages",
    "import site"
) | Set-Content -Encoding ascii -LiteralPath $PthFile.FullName

& $Python -m pip install --upgrade pip
& $Python -m pip install --target $SitePackages (Join-Path $Root "api")

Copy-TreeClean -Source (Join-Path $Root "api\app") -Destination (Join-Path $DistApp "app")
Copy-TreeClean -Source (Join-Path $Root "packages") -Destination (Join-Path $DistApp "packages")
Copy-TreeClean -Source $WebDist -Destination (Join-Path $DistApp "assets\react-build")
Copy-Item -LiteralPath (Join-Path $Root ".env.example") -Destination (Join-Path $DistApp ".env.example") -Force
Copy-Item -LiteralPath (Join-Path $Root "LICENSE") -Destination (Join-Path $DistApp "LICENSE") -Force
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination (Join-Path $DistApp "README.md") -Force

Compile-Launcher `
    -SourcePath (Join-Path $Root "installer\launcher\APICostXLauncher.cs") `
    -OutputPath (Join-Path $DistApp "APICostX.exe")

if (-not (Test-Path -LiteralPath (Join-Path $DistApp "APICostX.exe"))) {
    throw "Launcher build did not produce dist\APICostX\APICostX.exe"
}

& $InnoSetup "/DMyAppVersion=$SafeVersion" (Join-Path $Root "installer\inno\APICostX.iss")

$Installer = Join-Path $Root "dist\installer\APICostX-Setup-$SafeVersion.exe"
if (-not (Test-Path -LiteralPath $Installer)) {
    throw "Inno Setup did not produce $Installer"
}

$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Installer
$HashLine = "$($Hash.Hash.ToLowerInvariant())  $(Split-Path -Leaf $Installer)"
$HashLine | Set-Content -Encoding ascii -LiteralPath (Join-Path $Root "dist\installer\SHA256SUMS-windows.txt")

Write-Host "Built $Installer"
Write-Host $HashLine
