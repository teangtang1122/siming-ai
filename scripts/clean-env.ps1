param([switch]$Force)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $Root "backend"

$RuntimeDir = Join-Path $BackendDir ".py-runtime"
$VenvDir = Join-Path $BackendDir ".venv"

function Write-Step {
    param([string]$Message)
    Write-Host "[clean] $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[clean] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "[clean] $Message" -ForegroundColor Red
}

function Test-DirectoryInUse {
    param([string]$Path)
    
    if (-not (Test-Path $Path)) {
        return $false
    }
    
    # 尝试列出目录中的文件，如果失败说明被占用
    try {
        $files = Get-ChildItem -Path $Path -Recurse -ErrorAction SilentlyContinue
        return $false
    } catch {
        return $true
    }
}

function Remove-SafeDirectory {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Path,
        [Parameter(Mandatory=$true)]
        [string]$Name
    )
    
    if (-not (Test-Path $Path)) {
        Write-Step "$Name not found - skipping"
        return $true
    }
    
    if (Test-DirectoryInUse -Path $Path) {
        Write-Error "$Name is currently in use. Please close any programs using it and try again."
        return $false
    }
    
    Write-Step "Removing $Name ..."
    
    try {
        # 先尝试正常删除
        Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
        
        # 验证是否删除成功
        if (Test-Path $Path) {
            Write-Warn "Normal deletion failed, trying alternative method ..."
            
            # 使用 robocopy 清空后再删除
            $EmptyDir = Join-Path $env:TEMP "empty_clean_$(Get-Random)"
            New-Item -ItemType Directory -Path $EmptyDir -Force | Out-Null
            
            try {
                robocopy $EmptyDir $Path /MIR /R:1 /W:1 2>$null | Out-Null
                Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
            } finally {
                Remove-Item -Path $EmptyDir -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        
        Write-Step "$Name removed successfully"
        return $true
    } catch {
        Write-Error "Failed to remove $Name : $($_.Exception.Message)"
        
        # 显示目录内容帮助用户诊断
        try {
            Write-Host "  Directory contents:" -ForegroundColor Red
            Get-ChildItem -Path $Path -ErrorAction SilentlyContinue | ForEach-Object {
                Write-Host "    - $($_.Name)" -ForegroundColor Red
            }
        } catch {}
        
        return $false
    }
}

# ========== 主流程 ==========

Write-Host ""
Write-Step "=================================="
Write-Step "  Python Environment Cleanup"
Write-Step "=================================="
Write-Host ""

# 检查 backend 目录是否存在
if (-not (Test-Path $BackendDir)) {
    Write-Error "Backend directory not found: $BackendDir"
    exit 1
}

Write-Step "Backend directory: $BackendDir"
Write-Host ""

$success = @()
$failed = @()

# 清理 .py-runtime
if (Remove-SafeDirectory -Path $RuntimeDir -Name ".py-runtime (Python runtime)") {
    $success += ".py-runtime"
} else {
    $failed += ".py-runtime"
}

Write-Host ""

# 清理 .venv
if (Remove-SafeDirectory -Path $VenvDir -Name ".venv (Virtual environment)") {
    $success += ".venv"
} else {
    $failed += ".venv"
}

Write-Host ""
Write-Host "=================================="

# 显示结果
if ($success.Count -gt 0) {
    Write-Host "  Successfully removed:" -ForegroundColor Green
    foreach ($item in $success) {
        Write-Host "    - $item" -ForegroundColor Green
    }
}

if ($failed.Count -gt 0) {
    Write-Host "  Failed to remove:" -ForegroundColor Red
    foreach ($item in $failed) {
        Write-Host "    - $item" -ForegroundColor Red
    }
    Write-Host ""
    Write-Error "Some directories could not be removed. Please close any Python processes and try again."
    Write-Host ""
    Write-Host "You can also try running this command manually:" -ForegroundColor Yellow
    Write-Host "  rd /s /q `"$RuntimeDir`"" -ForegroundColor Yellow
    Write-Host "  rd /s /q `"$VenvDir`"" -ForegroundColor Yellow
    exit 1
}

Write-Host "=================================="
Write-Step "Cleanup complete!"
Write-Host ""
Write-Host "Run start-agent.bat to set up a fresh environment."
Write-Host ""
