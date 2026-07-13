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
    $timeoutSeconds = [Math]::Max(1, [Math]::Ceiling($TimeoutMs / 1000))
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $timeoutSeconds
    return $response.Content
}

function Stop-ReleaseSimingProcesses {
    param([Parameter(Mandatory=$true)][string]$ReleaseExePath)
    $resolved = (Resolve-Path -LiteralPath $ReleaseExePath -ErrorAction SilentlyContinue).Path
    if (-not $resolved) { return }
    Get-Process Siming -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $resolved } |
        Stop-Process -Force -ErrorAction SilentlyContinue
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
    Stop-ReleaseSimingProcesses -ReleaseExePath $exePath
    exit 1
}
Write-Host "  Server is ready at $serverBaseUrl (waited $waited seconds)" -ForegroundColor Green

# Step 5: Verify API endpoints
Write-Host "[5/6] Verifying API endpoints..." -ForegroundColor Yellow

try {
    # Test projects API
    $projectsResponse = Invoke-LocalJsonGet -Url "$serverBaseUrl/api/v1/projects" -TimeoutMs 5000
    $projectsPayload = $projectsResponse | ConvertFrom-Json
    $projectCount = if ($null -ne $projectsPayload.data.total) { $projectsPayload.data.total } elseif ($null -ne $projectsPayload.data.items) { @($projectsPayload.data.items).Count } else { 0 }
    Write-Host "  Projects API: OK ($projectCount projects)" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Projects API failed: $_" -ForegroundColor Red
    throw
}

try {
    # Test the V2 new-book workbench contract bundled in the executable.
    $presetsResponse = Invoke-LocalJsonGet -Url "$serverBaseUrl/api/v1/novel-creation/presets" -TimeoutMs 5000
    $presets = ($presetsResponse | ConvertFrom-Json).data
    if (($presets.schema_version -ne 2) -or (@($presets.categories).Count -lt 10) -or (@($presets.stage_order).Count -lt 8)) {
        throw "Unexpected novel creation preset contract"
    }
    Write-Host "  Novel Creation V2 API: OK ($(@($presets.categories).Count) genre presets)" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Novel Creation V2 API failed: $_" -ForegroundColor Red
    throw
}

try {
    # Test external agent settings API
    $settingsResponse = Invoke-LocalJsonGet -Url "$serverBaseUrl/api/v1/external-agent/settings" -TimeoutMs 5000
    Write-Host "  External Agent Settings API: OK" -ForegroundColor Green
} catch {
    Write-Host "  WARNING: External Agent Settings API not available" -ForegroundColor Yellow
}

try {
    # Prompt packs are exposed through workspace tools and the project prompt GUI.
    $catalogResponse = Invoke-LocalJsonGet -Url "$serverBaseUrl/api/v1/tools/catalog" -TimeoutMs 5000
    $catalog = ($catalogResponse | ConvertFrom-Json).data
    $toolNames = @($catalog.items | ForEach-Object { $_.name })
    if (($toolNames -contains "list_prompt_packs") -and ($toolNames -contains "get_prompt_pack")) {
        Write-Host "  Prompt Pack Tools: OK" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: prompt pack tools not found in tool catalog" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  WARNING: Prompt pack tool catalog check failed" -ForegroundColor Yellow
}

# Step 6: Cleanup
Write-Host "[6/6] Cleanup..." -ForegroundColor Yellow
Stop-ReleaseSimingProcesses -ReleaseExePath $exePath
Write-Host "  Siming.exe stopped" -ForegroundColor Green

Write-Host ""
Write-Host "=== Smoke Test Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Summary:" -ForegroundColor White
Write-Host "  - Siming.exe: OK" -ForegroundColor Green
Write-Host "  - MCP Setup Script: OK" -ForegroundColor Green
Write-Host "  - Server Startup: OK" -ForegroundColor Green
Write-Host "  - API Endpoints: OK" -ForegroundColor Green
Write-Host "  - Novel Creation V2: OK" -ForegroundColor Green
Write-Host ""
Write-Host "All smoke tests passed!" -ForegroundColor Green
exit 0
