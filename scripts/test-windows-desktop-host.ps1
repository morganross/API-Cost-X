[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BundleRoot = Join-Path $RepoRoot "dist\APICostX"
$Launcher = Join-Path $BundleRoot "APICostX.exe"

function Assert-BundledFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing $Description at $Path"
    }

    Write-Host "Verified ${Description}: $Path"
}

$RequiredFiles = @(
    [pscustomobject]@{ RelativePath = "APICostX.exe"; Description = "desktop launcher" },
    [pscustomobject]@{ RelativePath = "runtime/python/python.exe"; Description = "bundled Python runtime" },
    [pscustomobject]@{ RelativePath = "assets/react-build/index.html"; Description = "React build entrypoint" },
    [pscustomobject]@{ RelativePath = ".env.example"; Description = "environment template" }
)

foreach ($RequiredFile in $RequiredFiles) {
    $Path = Join-Path $BundleRoot ($RequiredFile.RelativePath -replace "/", [IO.Path]::DirectorySeparatorChar)
    Assert-BundledFile -Path $Path -Description $RequiredFile.Description
}

Write-Host "Running APICostX.exe --self-test..."
$SelfTestProcess = Start-Process `
    -FilePath $Launcher `
    -ArgumentList "--self-test" `
    -WorkingDirectory $BundleRoot `
    -Wait `
    -PassThru

if ($SelfTestProcess.ExitCode -ne 0) {
    throw "APICostX.exe --self-test failed with exit code $($SelfTestProcess.ExitCode)"
}

Write-Host "APICostX desktop host smoke test passed."
