param(
    [switch]$NoBuild,
    [switch]$KeepStack
)

$ErrorActionPreference = 'Stop'

$composeArgs = @('-f', 'docker-compose.dev.yml', '-f', 'tests/e2e/docker-compose.e2e.yml')
Write-Host 'Checking Docker daemon...'
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
docker info *> $null
$dockerInfoExitCode = $LASTEXITCODE
$ErrorActionPreference = $prevErrorAction
if ($dockerInfoExitCode -ne 0) {
    throw 'Docker daemon is not available. Start Docker Desktop and retry.'
}

$upArgs = @('compose') + $composeArgs + @('up', '-d')
if (-not $NoBuild) {
    $upArgs += '--build'
}

Write-Host 'Starting extended E2E stack...'
docker @upArgs

if ($LASTEXITCODE -ne 0) {
    throw 'Failed to start docker compose stack'
}

$env:E2E_API_GATEWAY_URL = 'http://localhost:8000'
$env:E2E_MANAGEMENT_URL = 'http://localhost:8004'
$env:E2E_CLIENT_GATEWAY_URL = 'http://localhost:8005'
$env:E2E_WORDPRESS_URL = 'http://localhost:8086'
$env:E2E_PROJECT_ID = 'e2e-project'
$env:E2E_HMAC_SECRET = 'e2e-shared-secret'
$env:E2E_INTERNAL_API_KEY = 'change-me-e2e'

Write-Host 'Running extended E2E tests...'
python -m pytest tests/e2e/test_extended_stack.py -q
$testExitCode = $LASTEXITCODE

if (-not $KeepStack) {
    Write-Host 'Stopping E2E stack...'
    docker compose @composeArgs down -v
}

exit $testExitCode
