param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Output,
    [Parameter(Mandatory=$true)][string]$SigningKeyEnv,
    [string]$Revision = 'HEAD'
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$pythonPath = (Get-Item -LiteralPath $Python).FullName
$outputPath = [System.IO.Path]::GetFullPath($Output)
Push-Location -LiteralPath $repoRoot
try {
    & $pythonPath -B -m scripts.plugin_package build --revision $Revision --output $outputPath --signing-key-env $SigningKeyEnv
    if ($LASTEXITCODE -ne 0) { throw 'Signed package build failed; no artifact may be published.' }
} finally {
    Pop-Location
}
