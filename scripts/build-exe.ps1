param(
  [switch]$OneDir,
  [string]$PipIndexUrl = "https://pypi.org/simple",
  [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$BuildDir = Join-Path $Root ".build"
$VenvDir = Join-Path $BuildDir "packager-venv"
$DistDir = if ($OutputDirectory) {
  [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
  Join-Path $Root "release"
}
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

function Test-PackagingPython {
  param([Parameter(Mandatory=$true)][string]$PythonPath)
  $PreviousErrorAction = $ErrorActionPreference
  try {
    $ErrorActionPreference = "SilentlyContinue"
    $BaseExecutable = & $PythonPath -c "import sys,tkinter; print(sys._base_executable)" 2>$null
    $ProbeExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $PreviousErrorAction
  }
  if ($ProbeExitCode -ne 0 -or -not $BaseExecutable) {
    return $false
  }

  $NormalizedBase = [System.IO.Path]::GetFullPath(($BaseExecutable | Select-Object -Last 1))
  return [bool]$NormalizedBase
}

function Resolve-BuildPython {
  if ($env:SIMING_BUILD_PYTHON) {
    $ConfiguredPython = [System.IO.Path]::GetFullPath($env:SIMING_BUILD_PYTHON)
    if (-not (Test-Path -LiteralPath $ConfiguredPython)) {
      throw "SIMING_BUILD_PYTHON does not exist: $ConfiguredPython"
    }
    if (-not (Test-PackagingPython -PythonPath $ConfiguredPython)) {
      throw "SIMING_BUILD_PYTHON must provide Tk and a PyInstaller-compatible Windows runtime: $ConfiguredPython"
    }
    return $ConfiguredPython
  }

  # Packaging is intentionally isolated from the backend test environment.
  # Managed Python distributions can be valid for tests while producing a
  # PyInstaller bootloader that cannot start on a normal Windows desktop.
  $Python = Get-Command "python" -ErrorAction SilentlyContinue
  if ($Python -and (Test-PackagingPython -PythonPath $Python.Source)) {
    return $Python.Source
  }
  $Py = Get-Command "py" -ErrorAction SilentlyContinue
  if ($Py -and (Test-PackagingPython -PythonPath $Py.Source)) {
    return $Py.Source
  }
  $BackendPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
  if ((Test-Path $BackendPython) -and (Test-PackagingPython -PythonPath $BackendPython)) {
    return $BackendPython
  }
  throw "A Windows Python runtime with Tk and PyInstaller support is required on the packaging machine."
}

function Get-PythonRuntimeIdentity {
  param([Parameter(Mandatory=$true)][string]$PythonPath)
  $IdentityJson = & $PythonPath -c "import json,sys; print(json.dumps({'version': f'{sys.version_info.major}.{sys.version_info.minor}', 'base_executable': sys._base_executable}))"
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Python runtime: $PythonPath"
  }
  return ($IdentityJson | ConvertFrom-Json)
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

function Stop-ReleaseSimingProcesses {
  $ReleaseExePath = Join-Path $DistDir "$AppName.exe"
  $resolved = (Resolve-Path -LiteralPath $ReleaseExePath -ErrorAction SilentlyContinue).Path
  if (-not $resolved) { return }
  Get-Process $AppName -ErrorAction SilentlyContinue |
    Where-Object {
      try { $_.Path -eq $resolved } catch { $false }
    } |
    ForEach-Object {
      Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
      try { $_.WaitForExit(5000) } catch {}
    }
}

function Remove-ReleaseExecutable {
  $ReleaseExePath = Join-Path $DistDir "$AppName.exe"
  if (-not (Test-Path -LiteralPath $ReleaseExePath)) { return }
  for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
    try {
      Remove-Item -LiteralPath $ReleaseExePath -Force
      return
    } catch {
      Stop-ReleaseSimingProcesses
      Start-Sleep -Seconds 1
      if ($Attempt -eq 20) {
        throw "Cannot replace $ReleaseExePath because it is still locked. Close Siming.exe or any scanner holding the file, then rerun packaging. Last error: $($_.Exception.Message)"
      }
    }
  }
}

Write-Step "Checking build tools..."
$PythonExe = Resolve-BuildPython
Write-Step "Using build Python: $PythonExe"
Require-Command -Names @("node") -Hint "Node.js is required on the packaging machine." | Out-Null
Require-Command -Names @("npm") -Hint "npm is required on the packaging machine." | Out-Null

$BuildPythonRuntime = Get-PythonRuntimeIdentity -PythonPath $PythonExe
$BuildPythonVersion = $BuildPythonRuntime.version
$BuildPythonBase = [System.IO.Path]::GetFullPath($BuildPythonRuntime.base_executable)
$ExistingVenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (Test-Path -LiteralPath $ExistingVenvPython) {
  $PackagerPythonRuntime = Get-PythonRuntimeIdentity -PythonPath $ExistingVenvPython
  $PackagerPythonVersion = $PackagerPythonRuntime.version
  $PackagerPythonBase = [System.IO.Path]::GetFullPath($PackagerPythonRuntime.base_executable)
  $VersionChanged = $PackagerPythonVersion -ne $BuildPythonVersion
  $RuntimeChanged = -not $PackagerPythonBase.Equals($BuildPythonBase, [System.StringComparison]::OrdinalIgnoreCase)
  if ($VersionChanged -or $RuntimeChanged) {
    $ResolvedBuildDir = [System.IO.Path]::GetFullPath($BuildDir).TrimEnd('\') + '\'
    $ResolvedVenvDir = [System.IO.Path]::GetFullPath($VenvDir)
    if (-not $ResolvedVenvDir.StartsWith($ResolvedBuildDir, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to replace packager environment outside the build directory: $ResolvedVenvDir"
    }
    Write-Step "Recreating packager environment for Python $BuildPythonVersion at $BuildPythonBase..."
    Remove-Item -LiteralPath $ResolvedVenvDir -Recurse -Force
  }
}

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

Write-Step "Verifying the Windows GUI runtime..."
Invoke-Native $VenvPython @("-c", "import tkinter; print(f'Tk {tkinter.TkVersion}')")

Write-Step "Installing backend dependencies and PyInstaller..."
Invoke-Native $VenvPython @("-m", "pip", "install", "-i", $PipIndexUrl, "--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org", "--upgrade", "pip")
Invoke-Native $VenvPython @("-m", "pip", "install", "-i", $PipIndexUrl, "--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org", "-r", (Join-Path $BackendDir "requirements.txt"), "pyinstaller")

Write-Step "Cleaning previous package output..."
Stop-ReleaseSimingProcesses
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
Remove-ReleaseExecutable
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
    "--add-data", "$(Join-Path $BackendDir 'alembic')${Separator}alembic",
    "--collect-submodules", "app",
    "--collect-submodules", "uvicorn",
    "--collect-submodules", "httptools",
    "--collect-submodules", "watchfiles",
    "--collect-all", "winpty",
    "--hidden-import", "sqlite3",
    "--hidden-import", "app.database.migrations",
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
$IsPrerelease = $Version.Contains("-")
$ReleaseTag = "v$Version"
$UpdateChannel = if ($IsPrerelease) { "preview" } else { "stable" }
$DownloadUrl = if ($IsPrerelease) {
  "https://github.com/$DefaultUpdateRepo/releases/download/$ReleaseTag/$AppName.exe"
} else {
  "https://github.com/$DefaultUpdateRepo/releases/latest/download/$AppName.exe"
}
$Manifest = [ordered]@{
  version = $Version
  channel = $UpdateChannel
  download_url = $DownloadUrl
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
$VerifierScript = Join-Path $ScriptDir "verify-release-assets.ps1"
& $VerifierScript -ReleaseDir (Split-Path -Parent $ExePath) -AppName $AppName -ExpectedVersion $Version
Write-Host "Update manifest: $ManifestPath"
Write-Host "SHA256 manifest: $ShaPath"
if ($OneDir) {
  Write-Host "Executable folder: $(Join-Path $DistDir $AppName)"
  Write-Host "Run: $(Join-Path (Join-Path $DistDir $AppName) "$AppName.exe")"
} else {
  Write-Host "Executable: $ExePath"
}
