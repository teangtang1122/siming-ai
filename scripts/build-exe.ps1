param(
  [switch]$OneDir,
  [string]$PipIndexUrl = "https://pypi.org/simple"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$BuildDir = Join-Path $Root ".build"
$VenvDir = Join-Path $BuildDir "packager-venv"
$DistDir = Join-Path $Root "release"
$AppName = "Moshu"
$LegacyAppName = "NovelWritingAgent"
$DefaultUpdateRepo = "teangtang1122/NovelWritingAgent"
$SetupMcpScriptName = "setup-external-agent-mcp.ps1"

function Write-Step {
  param([string]$Message)
  Write-Host "[package] $Message" -ForegroundColor Cyan
}

function Require-Command {
  param([string[]]$Names, [string]$Hint)
  foreach ($Name in $Names) {
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($Command) { return $Command.Source }
  }
  throw $Hint
}

function Invoke-Native {
  param(
    [Parameter(Mandatory=$true)][string]$FilePath,
    [string[]]$Arguments = @()
  )
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
  }
}

Write-Step "Checking build tools..."
$PythonExe = Require-Command -Names @("py", "python") -Hint "Python is required on the packaging machine."
Require-Command -Names @("node") -Hint "Node.js is required on the packaging machine." | Out-Null
Require-Command -Names @("npm") -Hint "npm is required on the packaging machine." | Out-Null

Write-Step "Building frontend static files..."
Push-Location $FrontendDir
try {
  if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Invoke-Native "npm" @("install")
  }
  Invoke-Native "npm" @("run", "build")
} finally {
  Pop-Location
}

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
  Write-Step "Creating packaging virtual environment..."
  if ((Split-Path -Leaf $PythonExe) -eq "py.exe" -or (Split-Path -Leaf $PythonExe) -eq "py") {
    py -m venv $VenvDir
  } else {
    python -m venv $VenvDir
  }
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

Write-Step "Installing backend dependencies and PyInstaller..."
Invoke-Native $VenvPython @("-m", "pip", "install", "-i", $PipIndexUrl, "--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org", "--upgrade", "pip")
Invoke-Native $VenvPython @("-m", "pip", "install", "-i", $PipIndexUrl, "--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org", "-r", (Join-Path $BackendDir "requirements.txt"), "pyinstaller")

Write-Step "Cleaning previous package output..."
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
$PyInstallerMode = if ($OneDir) { "--onedir" } else { "--onefile" }
$Separator = ":"
$FrontendDist = Join-Path $FrontendDir "dist"

Write-Step "Creating Windows executable..."
Push-Location $Root
try {
  Invoke-Native $VenvPython @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    $PyInstallerMode,
    "--windowed",
    "--name", $AppName,
    "--distpath", $DistDir,
    "--workpath", (Join-Path $BuildDir "pyinstaller-work"),
    "--specpath", $BuildDir,
    "--paths", $BackendDir,
    "--add-data", "${FrontendDist}${Separator}frontend/dist",
    "--collect-submodules", "app",
    "--collect-submodules", "uvicorn",
    "--collect-submodules", "httptools",
    "--collect-submodules", "watchfiles",
    "--hidden-import", "sqlite3",
    "--hidden-import", "webview",
    "--hidden-import", "webview.platforms",
    "--hidden-import", "clr_loader",
    "--hidden-import", "pythonnet",
    (Join-Path $BackendDir "launcher.py")
  )
} finally {
  Pop-Location
}

Write-Step "Done."
$ExePath = if ($OneDir) {
  Join-Path (Join-Path $DistDir $AppName) "$AppName.exe"
} else {
  Join-Path $DistDir "$AppName.exe"
}
$BackendPathForPython = $BackendDir.Replace("\", "\\")
$Version = (& $VenvPython -c "import sys; sys.path.insert(0, '$BackendPathForPython'); from app.version import APP_VERSION; print(APP_VERSION)").Trim()
$Sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash.ToLowerInvariant()
$LegacyExePath = if ($OneDir) {
  Join-Path (Join-Path $DistDir $AppName) "$LegacyAppName.exe"
} else {
  Join-Path $DistDir "$LegacyAppName.exe"
}
Copy-Item -LiteralPath $ExePath -Destination $LegacyExePath -Force
$SetupMcpScriptSource = Join-Path $ScriptDir $SetupMcpScriptName
$SetupMcpScriptPath = if ($OneDir) {
  Join-Path (Join-Path $DistDir $AppName) $SetupMcpScriptName
} else {
  Join-Path $DistDir $SetupMcpScriptName
}
if (Test-Path -LiteralPath $SetupMcpScriptSource) {
  Copy-Item -LiteralPath $SetupMcpScriptSource -Destination $SetupMcpScriptPath -Force
}
$Manifest = [ordered]@{
  version = $Version
  download_url = "https://github.com/$DefaultUpdateRepo/releases/latest/download/$AppName.exe"
  legacy_download_url = "https://github.com/$DefaultUpdateRepo/releases/latest/download/$LegacyAppName.exe"
  sha256 = $Sha256
  repo = $DefaultUpdateRepo
} | ConvertTo-Json -Depth 3
$ManifestPath = if ($OneDir) {
  Join-Path (Join-Path $DistDir $AppName) "update.json"
} else {
  Join-Path $DistDir "update.json"
}
[System.IO.File]::WriteAllText($ManifestPath, $Manifest + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
$ShaPath = if ($OneDir) {
  Join-Path (Join-Path $DistDir $AppName) "sha256.txt"
} else {
  Join-Path $DistDir "sha256.txt"
}
$ShaLinesArray = @(
  "$Sha256  $AppName.exe"
  "$Sha256  $LegacyAppName.exe"
)
if (Test-Path -LiteralPath $SetupMcpScriptPath) {
  $SetupScriptSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $SetupMcpScriptPath).Hash.ToLowerInvariant()
  $ShaLinesArray += "$SetupScriptSha256  $SetupMcpScriptName"
}
$ShaLines = $ShaLinesArray -join [Environment]::NewLine
[System.IO.File]::WriteAllText($ShaPath, $ShaLines + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
Write-Host "Update manifest: $ManifestPath"
Write-Host "SHA256 manifest: $ShaPath"
if ($OneDir) {
  Write-Host "Executable folder: $(Join-Path $DistDir $AppName)"
  Write-Host "Run: $(Join-Path (Join-Path $DistDir $AppName) "$AppName.exe")"
} else {
  Write-Host "Executable: $ExePath"
  Write-Host "Legacy-compatible alias: $LegacyExePath"
}
if (Test-Path -LiteralPath $SetupMcpScriptPath) {
  Write-Host "MCP setup script: $SetupMcpScriptPath"
}
