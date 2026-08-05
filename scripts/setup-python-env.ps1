# Python Environment Setup Module
# 提供统一的 Python 运行时和虚拟环境设置功能
#
# 使用方式：
#   作为模块导入: . .\setup-python-env.ps1
#                 $python = Initialize-PythonEnvironment -BackendDir "path\to\backend"
#
#   直接运行:     可以在 PowerShell 中 dot-source 后手动调用，或用 start-agent.bat/build-exe.bat

$ErrorActionPreference = "Stop"

# ========== 辅助函数 ==========

function Write-EnvStep {
    param([string]$Message)
    Write-Host "[env-setup] $Message" -ForegroundColor Cyan
}

function Write-EnvWarn {
    param([string]$Message)
    Write-Host "[env-setup] $Message" -ForegroundColor Yellow
}

function Write-EnvError {
    param([string]$Message)
    Write-Host "[env-setup] $Message" -ForegroundColor Red
}

# ========== 检测函数 ==========

function Test-PythonRuntime {
    param([string]$RuntimeDir)
    if (-not $RuntimeDir) { return $false }
    $PythonExe = Join-Path $RuntimeDir "python.exe"
    return Test-Path $PythonExe
}

function Test-Venv {
    param([string]$VenvPath)
    if (-not $VenvPath) { return $false }
    $PythonExe = Join-Path $VenvPath "Scripts\python.exe"
    return Test-Path $PythonExe
}

# ========== 核心设置函数 ==========

function Download-PythonEmbeddable {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Version,
        [Parameter(Mandatory=$true)]
        [string]$TargetDir
    )
    
    $ZipName = "python-$Version-embed-amd64.zip"
    $DownloadUrl = "https://www.python.org/ftp/python/$Version/$ZipName"
    
    # 检查是否有自定义镜像
    if ($env:SIMING_PYTHON_MIRROR) {
        $DownloadUrl = "$env:SIMING_PYTHON_MIRROR/$ZipName"
        Write-EnvStep "Using custom Python mirror: $env:SIMING_PYTHON_MIRROR"
    }
    
    Write-EnvStep "Downloading Python $Version from $DownloadUrl ..."
    
    $ZipPath = Join-Path $env:TEMP $ZipName
    
    try {
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath -UseBasicParsing
    } catch {
        Write-EnvError "Failed to download Python: $($_.Exception.Message)"
        throw "Download failed. Please check your network connection or set SIMING_PYTHON_MIRROR."
    }
    
    if (-not (Test-Path $TargetDir)) {
        New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
    }
    
    Write-EnvStep "Extracting Python to $TargetDir ..."
    try {
        Expand-Archive -Path $ZipPath -DestinationPath $TargetDir -Force
    } finally {
        Remove-Item -Path $ZipPath -Force -ErrorAction SilentlyContinue
    }
    
    Write-EnvStep "Python $Version downloaded successfully."
}

function Install-PipToEmbeddable {
    param([Parameter(Mandatory=$true)]
    [string]$RuntimeDir)
    
    $PythonExe = Join-Path $RuntimeDir "python.exe"
    
    Write-EnvStep "Setting up pip in Python embeddable package ..."
    
    # 下载 get-pip.py
    $GetPipPath = Join-Path $env:TEMP "get-pip.py"
    try {
        Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPipPath -UseBasicParsing
    } catch {
        Write-EnvError "Failed to download get-pip.py: $($_.Exception.Message)"
        throw "Failed to download pip installer."
    }
    
    # 运行 get-pip.py
    Push-Location $RuntimeDir
    try {
        & $PythonExe $GetPipPath
        if ($LASTEXITCODE -ne 0) {
            throw "get-pip.py failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
        Remove-Item -Path $GetPipPath -Force -ErrorAction SilentlyContinue
    }
    
    # 修改 python312._pth 文件，启用 site-packages
    $PthFiles = Get-ChildItem -Path $RuntimeDir -Filter "python*._pth" -ErrorAction SilentlyContinue
    foreach ($Pth in $PthFiles) {
        $Content = Get-Content -Path $Pth.FullName -Raw
        if ($Content -match '#import site') {
            $Content = $Content -replace '#import site', 'import site'
            # 使用 .NET 写入以避免 BOM
            [System.IO.File]::WriteAllText($Pth.FullName, $Content, [System.Text.UTF8Encoding]::new($false))
            Write-EnvStep "Updated $($Pth.Name) to enable site-packages."
        }
    }
    
    Write-EnvStep "pip installed successfully."
}

function Create-VirtualEnv {
    param(
        [Parameter(Mandatory=$true)]
        [string]$PythonRuntimeDir,
        [Parameter(Mandatory=$true)]
        [string]$TargetVenvDir
    )
    
    $PythonExe = Join-Path $PythonRuntimeDir "python.exe"
    
    Write-EnvStep "Creating virtual environment at $TargetVenvDir ..."
    
    # 注意：Python embedded (嵌入式版本) 不包含 venv 模块
    # 我们需要使用 virtualenv 包来创建虚拟环境
    
    # 先检查 virtualenv 是否可导入
    $virtualenvAvailable = $false
    try {
        $output = & $PythonExe -c "import virtualenv; print('ok')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $output -match 'ok') {
            $virtualenvAvailable = $true
            Write-EnvStep "virtualenv is already installed."
        }
    } catch {}
    
    # 如果 virtualenv 不可用，先安装它
    if (-not $virtualenvAvailable) {
        Write-EnvStep "Installing virtualenv package ..."
        $pipOutput = & $PythonExe -m pip install --no-warn-script-location virtualenv 2>&1
        
        # 安装后验证是否可导入
        $virtualenvAvailable = $false
        try {
            $output = & $PythonExe -c "import virtualenv; print('ok')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $output -match 'ok') {
                $virtualenvAvailable = $true
                Write-EnvStep "virtualenv installed successfully."
            }
        } catch {}
        
        if (-not $virtualenvAvailable) {
            Write-EnvError "Failed to install virtualenv."
            Write-Host "  pip output: $(($pipOutput -join ' ') | Select-Object -First 500)" -ForegroundColor Red
            throw "Failed to install virtualenv package."
        }
    }
    
    # 使用 virtualenv 创建虚拟环境
    Write-EnvStep "Running virtualenv to create environment ..."
    $veOutput = & $PythonExe -m virtualenv $TargetVenvDir
    $veExitCode = $LASTEXITCODE
    
    if ($veExitCode -ne 0) {
        Write-EnvError "virtualenv failed with exit code: $veExitCode"
        if ($veOutput) {
            Write-Host "  output: $(($veOutput -join ' ') | Select-Object -First 500)" -ForegroundColor Red
        }
        throw "Failed to create virtual environment with virtualenv."
    }
    
    Write-EnvStep "Virtual environment created successfully."
}

function Clear-PipCacheAndTempFiles {
    param([string]$SitePackagesPath)
    
    if (-not (Test-Path $SitePackagesPath)) {
        return
    }
    
    Write-EnvStep "Cleaning up temporary files in site-packages ..."
    
    try {
        # 删除 .tmp 文件
        Get-ChildItem -Path $SitePackagesPath -Filter "*.tmp" -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
            Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
        }
        
        # 删除 ~ 结尾的残留目录
        Get-ChildItem -Path $SitePackagesPath -Directory -ErrorAction SilentlyContinue | Where-Object { 
            $_.Name -like "*-*~" -or $_.Name -like "*~" 
        } | ForEach-Object {
            Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
        
        # 删除 .dist-info 目录下可能的残留临时文件
        Get-ChildItem -Path $SitePackagesPath -Directory -Filter "*.dist-info" -ErrorAction SilentlyContinue | ForEach-Object {
            $tempFiles = Get-ChildItem -Path $_.FullName -Filter "*.tmp" -ErrorAction SilentlyContinue
            if ($tempFiles) {
                $tempFiles | Remove-Item -Force -ErrorAction SilentlyContinue
            }
        }
        
        Write-EnvStep "Temporary files cleaned."
    } catch {
        Write-EnvWarn "Failed to clean some temporary files: $($_.Exception.Message)"
    }
}

function Install-Dependencies {
    param(
        [Parameter(Mandatory=$true)]
        [string]$VenvPython,
        [Parameter(Mandatory=$true)]
        [string]$RequirementsPath,
        [int]$MaxRetries = 3
    )
    
    Write-EnvStep "Upgrading pip ..."
    $pipOutput = & $VenvPython -m pip install --no-warn-script-location --upgrade pip 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-EnvWarn "pip upgrade failed (exit code: $($LASTEXITCODE)), continuing anyway ..."
    }
    
    if (-not (Test-Path $RequirementsPath)) {
        Write-EnvWarn "Requirements file not found: $RequirementsPath"
        return
    }
    
    # 获取 site-packages 路径
    $sitePackages = & $VenvPython -c "import site; print(site.getsitepackages()[0])" 2>$null
    
    $retryCount = 0
    $retryDelay = 2 # 秒
    
    while ($retryCount -lt $MaxRetries) {
        if ($retryCount -gt 0) {
            Write-EnvWarn "Retry attempt $($retryCount + 1) / $MaxRetries ..."
            
            # 在重试前清理临时文件
            if ($sitePackages) {
                Clear-PipCacheAndTempFiles -SitePackagesPath $sitePackages
            }
            
            # 清理 pip 缓存
            Write-EnvStep "Clearing pip cache ..."
            & $VenvPython -m pip cache purge 2>$null
            
            Start-Sleep -Seconds $retryDelay
            $retryDelay *= 2 # 指数退避
        }
        
        Write-EnvStep "Installing dependencies from $RequirementsPath ..."
        $installOutput = & $VenvPython -m pip install --no-warn-script-location -r $RequirementsPath 2>&1
        $installExitCode = $LASTEXITCODE
        
        if ($installExitCode -eq 0) {
            Write-EnvStep "Dependencies installed successfully."
            return
        }
        
        # 检查是否是文件系统错误（可以重试）
        $outputString = ($installOutput -join " ")
        $isFilesystemError = $outputString -match "No such file or directory" -or 
                            $outputString -match "Permission denied" -or
                            $outputString -match "could not access"
        
        if (-not $isFilesystemError) {
            # 非文件系统错误，直接失败
            Write-EnvError "pip install failed (exit code: $installExitCode)"
            if ($installOutput) {
                Write-Host "  Last lines: $(($installOutput | Select-Object -Last 5) -join ' ')" -ForegroundColor Red
            }
            throw "Failed to install dependencies from $RequirementsPath."
        }
        
        $retryCount++
        if ($retryCount -ge $MaxRetries) {
            Write-EnvError "pip install failed after $MaxRetries attempts (exit code: $installExitCode)"
            if ($installOutput) {
                Write-Host "  Last error: $(($installOutput | Select-Object -Last 3) -join ' ')" -ForegroundColor Red
            }
            throw "Failed to install dependencies from $RequirementsPath after $MaxRetries retries."
        }
        
        Write-EnvWarn "Temporary error detected. Will retry..."
    }
}

# ========== 主入口函数 ==========

function Initialize-PythonEnvironment {
    param(
        [Parameter(Mandatory=$true)]
        [string]$BackendDir,
        [string]$PythonVersion = "3.12.3",
        [switch]$ForceRecreate
    )
    
    $RuntimeDir = Join-Path $BackendDir ".py-runtime"
    $VenvDir = Join-Path $BackendDir ".venv"
    $RequirementsFile = Join-Path $BackendDir "requirements.txt"
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    
    Write-EnvStep "=================================="
    Write-EnvStep "  Python Environment Setup"
    Write-EnvStep "=================================="
    Write-EnvStep "Backend directory: $BackendDir"
    Write-EnvStep "Python runtime:    $RuntimeDir"
    Write-EnvStep "Virtual env:       $VenvDir"
    Write-EnvStep "Python version:    $PythonVersion"
    Write-Host ""
    
    # 强制重建
    if ($ForceRecreate) {
        Write-EnvWarn "Force recreate mode: removing existing environments ..."
        if (Test-Path $RuntimeDir) { Remove-Item -Path $RuntimeDir -Recurse -Force }
        if (Test-Path $VenvDir) { Remove-Item -Path $VenvDir -Recurse -Force }
    }
    
    # Step 1: 检查或下载 Python 运行时
    if (-not (Test-PythonRuntime -RuntimeDir $RuntimeDir)) {
        Write-EnvStep "[1/3] Python runtime not found. Downloading ..."
        Download-PythonEmbeddable -Version $PythonVersion -TargetDir $RuntimeDir
        Install-PipToEmbeddable -RuntimeDir $RuntimeDir
    } else {
        Write-EnvStep "[1/3] Python runtime found at $RuntimeDir"
    }
    
    $RuntimePython = Join-Path $RuntimeDir "python.exe"
    if (-not (Test-Path $RuntimePython)) {
        throw "Python runtime executable not found at $RuntimePython after setup."
    }
    
    # Step 2: 检查或创建虚拟环境
    if (-not (Test-Venv -VenvPath $VenvDir)) {
        Write-EnvStep "[2/3] Virtual environment not found. Creating ..."
        Create-VirtualEnv -PythonRuntimeDir $RuntimeDir -TargetVenvDir $VenvDir
    } else {
        Write-EnvStep "[2/3] Virtual environment found at $VenvDir"
    }
    
    if (-not (Test-Path $VenvPython)) {
        throw "Virtual environment Python not found at $VenvPython after setup."
    }
    
    # Step 3: 安装依赖
    Write-EnvStep "[3/3] Installing dependencies ..."
    Install-Dependencies -VenvPython $VenvPython -RequirementsPath $RequirementsFile
    
    Write-Host ""
    Write-EnvStep "=================================="
    Write-EnvStep "  Python Environment Ready"
    Write-EnvStep "=================================="
    Write-EnvStep "Python: $VenvPython"
    Write-EnvStep "  Version: $(& $VenvPython --version)"
    Write-Host ""
    
    return $VenvPython
}

# 这个模块不提供直接运行入口，只能通过 dot-sourcing 导入后调用函数
# 例如：. .\setup-python-env.ps1; Initialize-PythonEnvironment -BackendDir "path\to\backend"
