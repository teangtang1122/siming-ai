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
$AppName = "Siming"
$DefaultUpdateRepo = "teangtang1122/siming-ai"

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

function Resolve-BuildPython {
  $BackendPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
  if (Test-Path $BackendPython) {
    return $BackendPython
  }
  $Python = Get-Command "python" -ErrorAction SilentlyContinue
  if ($Python) {
    return $Python.Source
  }
  $Py = Get-Command "py" -ErrorAction SilentlyContinue
  if ($Py) {
    return $Py.Source
  }
  throw "Python is required on the packaging machine."
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
$PythonExe = Resolve-BuildPython
Write-Step "Using build Python: $PythonExe"
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
  Invoke-Native $PythonExe @("-m", "venv", $VenvDir)
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

Write-Step "Installing backend dependencies and PyInstaller..."
Invoke-Native $VenvPython @("-m", "pip", "install", "-i", $PipIndexUrl, "--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org", "--upgrade", "pip")
Invoke-Native $VenvPython @("-m", "pip", "install", "-i", $PipIndexUrl, "--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org", "-r", (Join-Path $BackendDir "requirements.txt"), "pyinstaller")

Write-Step "Cleaning previous package output..."
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
foreach ($StaleAsset in @("Moshu.exe", "NovelWritingAgent.exe")) {
  Remove-Item -LiteralPath (Join-Path $DistDir $StaleAsset) -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath (Join-Path $BuildDir "release-assets") -Recurse -Force -ErrorAction SilentlyContinue
$PyInstallerMode = if ($OneDir) { "--onedir" } else { "--onefile" }
$Separator = ":"
$FrontendDist = Join-Path $FrontendDir "dist"

Write-Step "Creating Windows executable..."
$IconPath = Join-Path $BackendDir "Siming.ico"
if (-not (Test-Path -LiteralPath $IconPath)) {
  $IconPath = Join-Path $BackendDir "Moshu.ico"
}
Push-Location $Root
try {
  $PyInstallerArgs = [System.Collections.ArrayList]@(
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
  if (Test-Path -LiteralPath $IconPath) {
    Write-Step "Using icon: $IconPath"
    # Insert before the last element (the entry-point .py script)
    $lastIndex = $PyInstallerArgs.Count - 1
    $PyInstallerArgs.Insert($lastIndex, "--icon")
    $PyInstallerArgs.Insert($lastIndex + 1, $IconPath)
  }
  Invoke-Native $VenvPython $PyInstallerArgs
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
$Manifest = [ordered]@{
  version = $Version
  download_url = "https://github.com/$DefaultUpdateRepo/releases/latest/download/$AppName.exe"
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
)
$ShaLines = $ShaLinesArray -join [Environment]::NewLine
[System.IO.File]::WriteAllText($ShaPath, $ShaLines + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
Write-Host "Update manifest: $ManifestPath"
Write-Host "SHA256 manifest: $ShaPath"
if ($OneDir) {
  Write-Host "Executable folder: $(Join-Path $DistDir $AppName)"
  Write-Host "Run: $(Join-Path (Join-Path $DistDir $AppName) "$AppName.exe")"
} else {
  Write-Host "Executable: $ExePath"
}
