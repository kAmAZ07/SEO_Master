param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('bootstrap', 'config', 'up', 'test', 'e2e', 'lint', 'build', 'down')]
    [string]$Action,
    [ValidateSet('dev', 'prod')]
    [string]$Mode = 'dev',
    [string]$Python = 'python',
    [string]$Php = 'php'
)

$ErrorActionPreference = 'Stop'
try {
    $Runner = Join-Path $PSScriptRoot 'project.py'
    & $Python $Runner $Action --mode $Mode --php $Php
    exit $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine('PROJECT_LAUNCH_FAILED: Python runner could not start.')
    exit 2
}
