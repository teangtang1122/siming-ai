param([switch]$Restart)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$LogDir = Join-Path $Root "artifacts\logs"
$BackendPort = 8000
$FrontendPort = 5173
$BackendUrl = "http://127.0.0.1:$BackendPort"
$FrontendUrl = "http://127.0.0.1:$FrontendPort"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Step {
  param([string]$Message)
  Write-Host "[agent] $Message" -ForegroundColor Cyan
}

function Write-Warn {
  param([string]$Message)
  Write-Host "[agent] $Message" -ForegroundColor Yellow
}

function Stop-Port {
  param(
    [int]$Port,
    [string]$Name
  )

  $Pids = @()
  try {
    $Connections = netstat -ano 2>$null | Select-String ":$Port\s+.*LISTENING"
    foreach ($Line in $Connections) {
      if ($Line -match '\s+(\d+)\s*$') {
        $ProcessId = $Matches[1]
        if ($ProcessId -and $ProcessId -ne "0") {
          $Pids += $ProcessId
        }
      }
    }
  } catch {
    Write-Warn "Could not query port $Port ($Name)"
    return
  }

  if ($Pids.Count -eq 0) {
    Write-Step "Port $Port ($Name) is free"
    return
  }

  foreach ($ProcessId in ($Pids | Sort-Object -Unique)) {
    Write-Step "Stopping PID $ProcessId on port $Port ($Name)..."
    try {
      taskkill /F /T /PID $ProcessId 2>$null | Out-Null
      Start-Sleep -Milliseconds 500
    } catch {
      Write-Warn ("Failed to kill PID " + $ProcessId + ": " + $_)
    }
  }

  # Wait up to 5 seconds for the port to free
  $Deadline = (Get-Date).AddSeconds(5)
  while ((Get-Date) -lt $Deadline) {
    $StillListening = netstat -ano 2>$null | Select-String ":$Port\s+.*LISTENING"
    if (-not $StillListening) {
      Write-Step "Port $Port ($Name) released"
      return
    }
    Start-Sleep -Milliseconds 500
  }

  throw "Port $Port ($Name) is still occupied after killing processes. Try again in a moment."
}

function Require-Command {
  param(
    [string[]]$Names,
    [string]$Hint
  )

  foreach ($Name in $Names) {
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($Command) {
      return $Command.Source
    }
  }

  throw "$Hint"
}

function Resolve-BackendPython {
  $VenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
  if (Test-Path $VenvPython) {
    return $VenvPython
  }
  $Python = Get-Command "python" -ErrorAction SilentlyContinue
  if ($Python) {
    return $Python.Source
  }
  $Py = Get-Command "py" -ErrorAction SilentlyContinue
  if ($Py) {
    return $Py.Source
  }
  throw "Python was not found. Install Python or create backend\.venv and try again."
}

function Test-TcpPort {
  param(
    [string]$HostName,
    [int]$Port
  )

  try {
    $Client = New-Object System.Net.Sockets.TcpClient
    $Result = $Client.BeginConnect($HostName, $Port, $null, $null)
    $Connected = $Result.AsyncWaitHandle.WaitOne(300)
    if ($Connected) {
      $Client.EndConnect($Result)
      $Client.Close()
      return $true
    }
    $Client.Close()
    return $false
  } catch {
    return $false
  }
}

function Test-Http {
  param([string]$Url)

  try {
    $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
    return $Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500
  } catch {
    return $false
  }
}

function Wait-Http {
  param(
    [string]$Url,
    [int]$Seconds = 45
  )

  $Deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $Deadline) {
    if (Test-Http $Url) {
      return $true
    }
    Start-Sleep -Milliseconds 700
  }
  return $false
}

function Start-ServiceWindow {
  param(
    [string]$Title,
    [string]$WorkingDirectory,
    [string]$Command,
    [string]$LogFile
  )

  $EscapedTitle = $Title.Replace("'", "''")
  $EscapedCommand = $Command.Replace("'", "''")
  $EscapedLog = $LogFile.Replace("'", "''")
  $Inline = @"
`$Host.UI.RawUI.WindowTitle = '$EscapedTitle'
Set-Location '$WorkingDirectory'
Write-Host '$EscapedTitle'
Write-Host 'Working directory: $WorkingDirectory'
Write-Host 'Log file: $EscapedLog'
Write-Host ''
& powershell.exe -NoProfile -Command '$EscapedCommand' *>&1 | Tee-Object -FilePath '$EscapedLog' -Append
Write-Host ''
Write-Host 'Process exited. Press any key to close this window.'
`$null = `$Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
"@

  Start-Process powershell.exe -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-NoExit",
    "-Command",
    $Inline
  ) -WorkingDirectory $WorkingDirectory
}

Write-Step "Checking runtime dependencies..."
Require-Command -Names @("node") -Hint "Node.js was not found. Install Node.js and try again." | Out-Null
Require-Command -Names @("npm") -Hint "npm was not found. Install Node.js/npm and try again." | Out-Null

if (-not (Test-Path $BackendDir)) {
  throw "Backend directory not found: $BackendDir"
}
if (-not (Test-Path $FrontendDir)) {
  throw "Frontend directory not found: $FrontendDir"
}
$PythonExe = Resolve-BackendPython
Write-Step "Using backend Python: $PythonExe"

if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
  Write-Warn "frontend\node_modules was not found; running npm install first."
  Push-Location $FrontendDir
  try {
    npm install
  } finally {
    Pop-Location
  }
}

if ($Restart) {
  Write-Step "Restart mode: stopping existing services..."
  Stop-Port -Port $BackendPort -Name "backend"
  Stop-Port -Port $FrontendPort -Name "frontend"
  Write-Step "All services stopped. Starting fresh..."
  Write-Host ""
}

Write-Step "Starting backend on $BackendUrl ..."
$BackendAlreadyRunning = Test-Http "$BackendUrl/health"
if ($Restart) {
  $BackendAlreadyRunning = $false
}
if ($BackendAlreadyRunning) {
  Write-Warn "Backend is already running on port $BackendPort."
} elseif (Test-TcpPort -HostName "127.0.0.1" -Port $BackendPort) {
  throw "Port $BackendPort is already in use, but $BackendUrl/health did not respond. Close that process or change the backend port."
} else {
  $BackendLog = Join-Path $LogDir "backend.log"
  $BackendCommand = "`$env:PYTHONPATH='.'; & '$PythonExe' -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort --reload"
  Start-ServiceWindow -Title "Novel Agent Backend" -WorkingDirectory $BackendDir -Command $BackendCommand -LogFile $BackendLog
}

if (-not (Wait-Http "$BackendUrl/health" 45)) {
  throw "Backend did not become ready in time. Check artifacts\logs\backend.log."
}

Write-Step "Starting frontend on $FrontendUrl ..."
$FrontendAlreadyRunning = Test-Http $FrontendUrl
if ($Restart) {
  $FrontendAlreadyRunning = $false
}
if ($FrontendAlreadyRunning) {
  Write-Warn "Frontend is already running on port $FrontendPort."
} elseif (Test-TcpPort -HostName "127.0.0.1" -Port $FrontendPort) {
  throw "Port $FrontendPort is already in use, but $FrontendUrl did not respond. Close that process or change the frontend port."
} else {
  $FrontendLog = Join-Path $LogDir "frontend.log"
  $FrontendCommand = "npm run dev -- --host 127.0.0.1 --port $FrontendPort"
  Start-ServiceWindow -Title "Novel Agent Frontend" -WorkingDirectory $FrontendDir -Command $FrontendCommand -LogFile $FrontendLog
}

if (-not (Wait-Http $FrontendUrl 45)) {
  throw "Frontend did not become ready in time. Check artifacts\logs\frontend.log."
}

Write-Step "Opening $FrontendUrl ..."
Start-Process $FrontendUrl
Write-Step "Ready. Backend: $BackendUrl  Frontend: $FrontendUrl"
