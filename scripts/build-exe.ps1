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
$ToolchainPath = Join-Path $Root "build-toolchain.json"
$PythonBuildLock = Join-Path $BackendDir "requirements-windows-build.lock"
$ProductDisplayName = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("5Y+45ZG9IChTaW1pbmcp"))
$FileDescription = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("5Y+45ZG9IChTaW1pbmcpIOahjOmdouW6lOeUqA=="))

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

function Read-BuildToolchain {
  param([Parameter(Mandatory=$true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Pinned build toolchain is missing: $Path"
  }
  try {
    $Toolchain = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
  } catch {
    throw "Unable to parse pinned build toolchain $Path. $($_.Exception.Message)"
  }
  foreach ($Property in @("python", "python_implementation", "python_architecture", "pip", "setuptools", "pyinstaller", "node", "npm", "inno_setup")) {
    if ([string]::IsNullOrWhiteSpace([string]$Toolchain.$Property)) {
      throw "Pinned build toolchain is missing '$Property': $Path"
    }
  }
  return $Toolchain
}

function Read-NativeVersion {
  param(
    [Parameter(Mandatory=$true)][string]$FilePath,
    [string[]]$Arguments = @("--version")
  )

  $Output = & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0 -or -not $Output) {
    throw "Unable to read tool version: $FilePath $($Arguments -join ' ')"
  }
  return ([string]($Output | Select-Object -Last 1)).Trim()
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
  $IdentityJson = & $PythonPath -c "import json,platform,sys; print(json.dumps({'version': platform.python_version(), 'implementation': platform.python_implementation(), 'architecture': platform.architecture()[0], 'base_executable': sys._base_executable}))"
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Python runtime: $PythonPath"
  }
  return ($IdentityJson | ConvertFrom-Json)
}

function Get-WindowsVersionParts {
  param([Parameter(Mandatory=$true)][string]$Version)

  if ($Version -notmatch '^v?(?<Major>\d+)\.(?<Minor>\d+)\.(?<Patch>\d+)(?:\.(?<Build>\d+))?(?:[-+].*)?$') {
    throw "APP_VERSION must start with a Windows-compatible semantic version: $Version"
  }
  $Parts = @(
    [int]$Matches.Major,
    [int]$Matches.Minor,
    [int]$Matches.Patch,
    $(if ($Matches.Build) { [int]$Matches.Build } else { 0 })
  )
  if (@($Parts | Where-Object { $_ -lt 0 -or $_ -gt 65535 }).Count -gt 0) {
    throw "Each Windows version component must be between 0 and 65535: $Version"
  }
  return $Parts
}

function Write-WindowsVersionInfo {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Version
  )

  $VersionParts = @(Get-WindowsVersionParts -Version $Version)
  $VersionTuple = "(" + ($VersionParts -join ", ") + ")"
  $FileVersion = $VersionParts -join "."
  $Content = @"
# UTF-8 PyInstaller version resource generated from backend/app/version.py.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=$VersionTuple,
    prodvers=$VersionTuple,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName', u'teangtang1122'),
          StringStruct(u'FileDescription', u'$FileDescription'),
          StringStruct(u'FileVersion', u'$FileVersion'),
          StringStruct(u'InternalName', u'Siming'),
          StringStruct(u'LegalCopyright', u'Copyright (C) 2026 teangtang1122'),
          StringStruct(u'OriginalFilename', u'Siming.exe'),
          StringStruct(u'ProductName', u'$ProductDisplayName'),
          StringStruct(u'ProductVersion', u'$Version')
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@
  [System.IO.File]::WriteAllText(
    $Path,
    $Content,
    [System.Text.UTF8Encoding]::new($false)
  )
}

function Assert-WindowsVersionInfo {
  param(
    [Parameter(Mandatory=$true)][string]$ExecutablePath,
    [Parameter(Mandatory=$true)][string]$Version
  )

  $ExpectedFileVersion = (@(Get-WindowsVersionParts -Version $Version) -join ".")
  $VersionInfo = (Get-Item -LiteralPath $ExecutablePath).VersionInfo
  $Expected = [ordered]@{
    CompanyName = "teangtang1122"
    ProductName = $ProductDisplayName
    FileDescription = $FileDescription
    FileVersion = $ExpectedFileVersion
    ProductVersion = $Version
    OriginalFilename = "Siming.exe"
  }
  foreach ($Entry in $Expected.GetEnumerator()) {
    $Actual = [string]$VersionInfo.($Entry.Key)
    if ($Actual -ne [string]$Entry.Value) {
      throw "Windows version resource mismatch for $($Entry.Key): expected '$($Entry.Value)', got '$Actual'."
    }
  }
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

$Toolchain = Read-BuildToolchain -Path $ToolchainPath
if (-not (Test-Path -LiteralPath $PythonBuildLock -PathType Leaf)) {
  throw "Pinned Windows build dependencies are missing: $PythonBuildLock"
}

Write-Step "Checking pinned build tools..."
$PythonExe = Resolve-BuildPython
Write-Step "Using build Python: $PythonExe"
$NodeExe = Require-Command -Names @("node") -Hint "Node.js is required on the packaging machine."
$NpmExe = Require-Command -Names @("npm") -Hint "npm is required on the packaging machine."

$BuildPythonRuntime = Get-PythonRuntimeIdentity -PythonPath $PythonExe
$BuildPythonVersion = $BuildPythonRuntime.version
$BuildPythonImplementation = $BuildPythonRuntime.implementation
$BuildPythonArchitecture = $BuildPythonRuntime.architecture
if ($BuildPythonVersion -ne [string]$Toolchain.python) {
  throw "Python $($Toolchain.python) is required for reproducible packaging; found $BuildPythonVersion at $PythonExe. Set SIMING_BUILD_PYTHON to the pinned runtime."
}
if ($BuildPythonImplementation -ne [string]$Toolchain.python_implementation) {
  throw "Python implementation $($Toolchain.python_implementation) is required; found $BuildPythonImplementation at $PythonExe."
}
if ($BuildPythonArchitecture -ne [string]$Toolchain.python_architecture) {
  throw "Python architecture $($Toolchain.python_architecture) is required; found $BuildPythonArchitecture at $PythonExe."
}
$NodeVersion = (Read-NativeVersion -FilePath $NodeExe).TrimStart("v")
$NpmVersion = Read-NativeVersion -FilePath $NpmExe
if ($NodeVersion -ne [string]$Toolchain.node) {
  throw "Node.js $($Toolchain.node) is required for reproducible packaging; found $NodeVersion."
}
if ($NpmVersion -ne [string]$Toolchain.npm) {
  throw "npm $($Toolchain.npm) is required for reproducible packaging; found $NpmVersion."
}
Write-Step "Pinned toolchain verified: Python $BuildPythonVersion, Node.js $NodeVersion, npm $NpmVersion."
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
  Invoke-Native $NpmExe @("ci")
  Invoke-Native $NpmExe @("run", "build")
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

Write-Step "Installing the locked Windows packaging environment..."
Invoke-Native $VenvPython @("-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:", "--no-deps", "-i", $PipIndexUrl, "pip==$($Toolchain.pip)", "setuptools==$($Toolchain.setuptools)")
# proxy_tools 0.1.0 is the only locked package published as an sdist. Build it
# with the already pinned setuptools instead of resolving an isolated build env.
Invoke-Native $VenvPython @("-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:", "--no-binary=proxy_tools", "--no-build-isolation", "--no-deps", "-i", $PipIndexUrl, "-r", $PythonBuildLock)
Invoke-Native $VenvPython @((Join-Path $ScriptDir "verify-python-build-lock.py"), "--lock", $PythonBuildLock, "--pyinstaller-version", [string]$Toolchain.pyinstaller)
Invoke-Native $VenvPython @("-m", "pip", "check")
$BackendPathForPython = $BackendDir.Replace("\", "\\")
$Version = (& $VenvPython -c "import sys; sys.path.insert(0, '$BackendPathForPython'); from app.version import APP_VERSION; print(APP_VERSION)").Trim()
$VersionInfoPath = Join-Path $BuildDir "$AppName-version-info.txt"
Write-Step "Generating Windows version resource for $AppName $Version..."
Write-WindowsVersionInfo -Path $VersionInfoPath -Version $Version

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
$DynamicWorkspaceModules = @(
  & $VenvPython -c "import sys; sys.path.insert(0, '$BackendPathForPython'); from app.services.workspace.dynamic_modules import LEGACY_HANDLER_MODULES; print(*LEGACY_HANDLER_MODULES, sep='\n')"
)
if ($LASTEXITCODE -ne 0 -or $DynamicWorkspaceModules.Count -eq 0) {
  throw "Unable to resolve dynamic workspace modules for packaging."
}
$DynamicWorkspaceModules = @($DynamicWorkspaceModules | Where-Object { $_ })

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
    "--version-file", $VersionInfoPath,
    "--distpath", $DistDir,
    "--workpath", (Join-Path $BuildDir "pyinstaller-work"),
    "--specpath", $BuildDir,
    "--paths", $BackendDir,
    "--add-data", "${FrontendDist}${Separator}frontend/dist",
    "--add-data", "$(Join-Path $BackendDir 'alembic')${Separator}alembic",
    "--add-data", "$(Join-Path $BackendDir 'prompt_specs')${Separator}prompt_specs",
    "--collect-submodules", "app",
    "--collect-submodules", "app.services.workspace.tools",
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
  $EntryPointIndex = $PyInstallerArgs.Count - 1
  foreach ($ModuleName in $DynamicWorkspaceModules) {
    $PyInstallerArgs.Insert($EntryPointIndex, "--hidden-import")
    $PyInstallerArgs.Insert($EntryPointIndex + 1, [string]$ModuleName)
    $EntryPointIndex += 2
  }
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

Write-Step "Verifying Windows version resource..."
Assert-WindowsVersionInfo -ExecutablePath $ExePath -Version $Version

Write-Step "Verifying packaged MCP stdio and critical write tools..."
Invoke-Native $VenvPython @((Join-Path $ScriptDir "smoke-packaged-mcp.py"), $ExePath)

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
