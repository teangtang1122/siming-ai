param(
  [string]$PipIndexUrl = "https://pypi.org/simple",
  [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$BuildDir = Join-Path $Root ".build"
$ReleaseDir = if ($OutputDirectory) {
  [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
  Join-Path $Root "release"
}
$PayloadDistDir = Join-Path $BuildDir "installer-payload"
$InstallerScript = Join-Path $Root "installer\Siming.iss"
$InstallerExe = Join-Path $ReleaseDir "Siming-Setup.exe"
$InstallerShaPath = Join-Path $ReleaseDir "Siming-Setup.sha256"

function Write-Step {
  param([string]$Message)
  Write-Host "[installer] $Message" -ForegroundColor Cyan
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

function Resolve-InnoCompiler {
  if ($env:SIMING_INNO_ISCC) {
    $Configured = [System.IO.Path]::GetFullPath($env:SIMING_INNO_ISCC)
    if (-not (Test-Path -LiteralPath $Configured -PathType Leaf)) {
      throw "SIMING_INNO_ISCC does not exist: $Configured"
    }
    return $Configured
  }

  $Command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
  if ($Command) { return $Command.Source }

  $Roots = @(${env:ProgramFiles}, ${env:ProgramFiles(x86)}) |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) }
  foreach ($ProgramRoot in $Roots) {
    $Candidates = @(
      Get-ChildItem -LiteralPath $ProgramRoot -Directory -Filter "Inno Setup *" -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName "ISCC.exe" } |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
    )
    if ($Candidates.Count -gt 0) {
      return ($Candidates | Sort-Object -Descending | Select-Object -First 1)
    }
  }

  throw "Inno Setup compiler ISCC.exe is required. Install Inno Setup or set SIMING_INNO_ISCC."
}

function Read-AppVersion {
  $VersionFile = Join-Path $Root "backend\app\version.py"
  $Match = Select-String -Path $VersionFile -Pattern 'APP_VERSION\s*=\s*["'']([^"'']+)' | Select-Object -First 1
  if (-not $Match) { throw "Unable to read APP_VERSION from $VersionFile" }
  return $Match.Matches.Groups[1].Value
}

Write-Step "Checking Inno Setup compiler..."
$Iscc = Resolve-InnoCompiler
$Version = Read-AppVersion

Write-Step "Removing stale legacy release assets..."
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
foreach ($LegacyAsset in @("Siming.exe", "update.json", "sha256.txt")) {
  Remove-Item -LiteralPath (Join-Path $ReleaseDir $LegacyAsset) -Force -ErrorAction SilentlyContinue
}

Write-Step "Building installed onedir payload for Siming $Version..."
Remove-Item -LiteralPath $PayloadDistDir -Recurse -Force -ErrorAction SilentlyContinue
& (Join-Path $ScriptDir "build-exe.ps1") -OneDir -PipIndexUrl $PipIndexUrl -OutputDirectory $PayloadDistDir
if ($LASTEXITCODE -ne 0) { throw "Installed payload build failed." }

$PayloadAppDir = Join-Path $PayloadDistDir "Siming"
$PayloadExe = Join-Path $PayloadAppDir "Siming.exe"
foreach ($RequiredPath in @($PayloadExe, $InstallerScript)) {
  if (-not (Test-Path -LiteralPath $RequiredPath)) {
    throw "Installer input is missing: $RequiredPath"
  }
}

Write-Step "Compiling selectable-path installer..."
Remove-Item -LiteralPath $InstallerExe -Force -ErrorAction SilentlyContinue
$IsccArgs = @(
  "/DMyAppVersion=$Version",
  "/DSourceDir=$PayloadAppDir",
  "/DOutputDir=$ReleaseDir",
  $InstallerScript
)
Invoke-Native $Iscc $IsccArgs

if (-not (Test-Path -LiteralPath $InstallerExe -PathType Leaf)) {
  throw "Inno Setup completed without producing $InstallerExe"
}

$InstallerSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $InstallerExe).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
  $InstallerShaPath,
  "$InstallerSha  Siming-Setup.exe" + [Environment]::NewLine,
  [System.Text.UTF8Encoding]::new($false)
)

Write-Step "Done."
Write-Host "Installer: $InstallerExe"
Write-Host "Installer SHA256: $InstallerShaPath"
