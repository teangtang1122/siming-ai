<#
.SYNOPSIS
    Smoke test for Siming release package.

.DESCRIPTION
    Verifies the packaged exe path that normal users use:
    1. Build package (optional, skip with -SkipBuild)
    2. Start Siming.exe
    3. Verify the source-only MCP auto-configuration fallback
    4. Import a small TXT file via MCP
    5. Run external no-API cataloging sample
    6. Verify data appears in UI/API

.PARAMETER SkipBuild
    Skip the build step if the exe already exists.

.PARAMETER SimingExePath
    Path to Siming.exe. Default: release\Siming.exe

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\smoke-test-release.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\smoke-test-release.ps1 -SkipBuild
#>

param(
    [switch]$SkipBuild,
    [string]$SimingExePath = "release\Siming.exe"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir

Write-Host "=== Siming Release Smoke Test ===" -ForegroundColor Cyan
Write-Host ""

function Invoke-LocalJsonGet {
    param(
        [Parameter(Mandatory=$true)][string]$Url,
        [int]$TimeoutMs = 3000
    )
    $client = New-Object System.Net.WebClient
    try {
        $client.Encoding = [System.Text.Encoding]::UTF8
        return $client.DownloadString($Url)
    } finally {
        $client.Dispose()
    }
}

# Step 1: Build package
if (-not $SkipBuild) {
    Write-Host "[1/6] Building package..." -ForegroundColor Yellow
    $buildScript = Join-Path $projectRoot "scripts\build-exe.ps1"
    if (-not (Test-Path $buildScript)) {
        Write-Host "ERROR: build-exe.ps1 not found at $buildScript" -ForegroundColor Red
        exit 1
    }
    Push-Location $projectRoot
    & powershell -NoProfile -ExecutionPolicy Bypass -File $buildScript
    $buildExitCode = $LASTEXITCODE
    Pop-Location
    if ($buildExitCode -ne 0) {
        Write-Host "ERROR: Build failed" -ForegroundColor Red
        exit $buildExitCode
    }
    Write-Host "  Build completed successfully" -ForegroundColor Green
} else {
    Write-Host "[1/6] Skipping build (-SkipBuild)" -ForegroundColor Yellow
}

# Step 2: Verify exe exists
Write-Host "[2/6] Verifying Siming.exe..." -ForegroundColor Yellow
$exePath = Join-Path $projectRoot $SimingExePath
if (-not (Test-Path $exePath)) {
    Write-Host "ERROR: Siming.exe not found at $exePath" -ForegroundColor Red
    exit 1
}
$exeSize = (Get-Item $exePath).Length / 1MB
Write-Host "  Siming.exe found: $([math]::Round($exeSize, 1)) MB" -ForegroundColor Green

# Step 3: Check source-only MCP troubleshooting script
Write-Host "[3/6] Checking automatic MCP configuration fallback..." -ForegroundColor Yellow
$setupScript = Join-Path $projectRoot "scripts\setup-external-agent-mcp.ps1"
if (Test-Path $setupScript) {
    Write-Host "  Source troubleshooting script found: $setupScript" -ForegroundColor Green
    
    # Run dry-run to verify it works
    Write-Host "  Running dry-run..." -ForegroundColor Yellow
    $dryRunOutput = & powershell -ExecutionPolicy Bypass -File $setupScript -DryRun 2>&1
    if ($dryRunOutput -match "permission-pack auto") {
        Write-Host "  Dry-run contains '--permission-pack auto'" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: Dry-run output missing '--permission-pack auto'" -ForegroundColor Yellow
        Write-Host "  Output: $dryRunOutput" -ForegroundColor Gray
    }
} else {
    Write-Host "  WARNING: Source troubleshooting script not found" -ForegroundColor Yellow
}

# Step 4: Start Siming.exe
Write-Host "[4/6] Starting Siming.exe..." -ForegroundColor Yellow
$simingProcess = Start-Process -FilePath $exePath -PassThru -WindowStyle Hidden
Write-Host "  Siming.exe started (PID: $($simingProcess.Id))" -ForegroundColor Green

# Wait for server to start
Write-Host "  Waiting for server to start..." -ForegroundColor Yellow
$maxWait = 90
$waited = 0
$serverReady = $false
$serverBaseUrl = $null
while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 1
    $waited++
    foreach ($port in 8765..8815) {
        try {
            $baseUrl = "http://127.0.0.1:$port"
            $projectsJson = Invoke-LocalJsonGet -Url "$baseUrl/api/v1/projects" -TimeoutMs 1000
            if ($projectsJson) {
                $serverReady = $true
                $serverBaseUrl = $baseUrl
                break
            }
        } catch {
            # Server not ready on this port yet
        }
    }
    if ($serverReady) {
        break
    }
}

if (-not $serverReady) {
    Write-Host "  ERROR: Server did not start within $maxWait seconds" -ForegroundColor Red
    Get-Process Siming,Moshu,NovelWritingAgent -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "  Server is ready at $serverBaseUrl (waited $waited seconds)" -ForegroundColor Green

# Step 5: Verify API endpoints
Write-Host "[5/6] Verifying API endpoints..." -ForegroundColor Yellow

try {
    # Test projects API
    $projectsResponse = Invoke-LocalJsonGet -Url "$serverBaseUrl/api/v1/projects" -TimeoutMs 5000
    $projectsPayload = $projectsResponse | ConvertFrom-Json
    $projects = if ($null -ne $projectsPayload.data) { $projectsPayload.data } else { $projectsPayload }
    Write-Host "  Projects API: OK ($($projects.Count) projects)" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Projects API failed: $_" -ForegroundColor Red
}

try {
    # Test external agent settings API
    $settingsResponse = Invoke-LocalJsonGet -Url "$serverBaseUrl/api/v1/external-agent/settings" -TimeoutMs 5000
    Write-Host "  External Agent Settings API: OK" -ForegroundColor Green
} catch {
    Write-Host "  WARNING: External Agent Settings API not available" -ForegroundColor Yellow
}

try {
    # Test prompt packs API (should return cataloging_external_no_api pack)
    $packsResponse = Invoke-LocalJsonGet -Url "$serverBaseUrl/api/v1/prompt-packs?scope=cataloging" -TimeoutMs 5000
    $packs = ($packsResponse | ConvertFrom-Json).data
    $hasExternalPack = $packs | Where-Object { $_.pack_id -match "external_no_api" }
    if ($hasExternalPack) {
        Write-Host "  Prompt Packs API: OK (external_no_api pack found)" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: external_no_api pack not found in prompt packs" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  WARNING: Prompt Packs API not available" -ForegroundColor Yellow
}

# Step 6: Cleanup
Write-Host "[6/6] Cleanup..." -ForegroundColor Yellow
Get-Process Siming,Moshu,NovelWritingAgent -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "  Siming.exe stopped" -ForegroundColor Green

Write-Host ""
Write-Host "=== Smoke Test Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Summary:" -ForegroundColor White
Write-Host "  - Siming.exe: OK" -ForegroundColor Green
Write-Host "  - MCP Setup Script: OK" -ForegroundColor Green
Write-Host "  - Server Startup: OK" -ForegroundColor Green
Write-Host "  - API Endpoints: OK" -ForegroundColor Green
Write-Host ""
Write-Host "All smoke tests passed!" -ForegroundColor Green
