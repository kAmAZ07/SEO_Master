param(
    [string]$Version = '0.2.0'
)

$ErrorActionPreference = 'Stop'

$pluginRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$distDir = Join-Path $pluginRoot 'dist'
$packageDir = Join-Path $distDir 'seo-master-connector'
$zipPath = Join-Path $distDir ("seo-master-connector-$Version.zip")

if (Test-Path $packageDir) {
    Remove-Item -Recurse -Force $packageDir
}
if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}

New-Item -ItemType Directory -Path $packageDir -Force | Out-Null

$includePaths = @(
    'seo-master-plugin.php',
    'readme.txt',
    'uninstall.php',
    'admin',
    'includes'
)

foreach ($relativePath in $includePaths) {
    $source = Join-Path $pluginRoot $relativePath
    $target = Join-Path $packageDir $relativePath

    if (Test-Path $source -PathType Container) {
        Copy-Item -Path $source -Destination $target -Recurse -Force
    } elseif (Test-Path $source -PathType Leaf) {
        Copy-Item -Path $source -Destination $target -Force
    }
}

if (-not (Test-Path $distDir)) {
    New-Item -ItemType Directory -Path $distDir -Force | Out-Null
}

Compress-Archive -Path (Join-Path $packageDir '*') -DestinationPath $zipPath -Force

Write-Host "Package created: $zipPath"
