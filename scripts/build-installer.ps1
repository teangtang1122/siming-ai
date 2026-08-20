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
$ToolchainPath = Join-Path $Root "build-toolchain.json"

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

  # Package managers can expose an ISCC.exe shim whose own file version is
  # unrelated to the installed compiler. Prefer the real Program Files binary.
  $Command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
  if ($Command) { return $Command.Source }

  throw "Inno Setup compiler ISCC.exe is required. Install Inno Setup or set SIMING_INNO_ISCC."
}

function Read-PinnedInnoVersion {
  if (-not (Test-Path -LiteralPath $ToolchainPath -PathType Leaf)) {
    throw "Pinned build toolchain is missing: $ToolchainPath"
  }
  $Toolchain = Get-Content -LiteralPath $ToolchainPath -Raw | ConvertFrom-Json
  if ([string]::IsNullOrWhiteSpace([string]$Toolchain.inno_setup)) {
    throw "Pinned build toolchain is missing 'inno_setup': $ToolchainPath"
  }
  return [string]$Toolchain.inno_setup
}

function Assert-InnoCompilerVersion {
  param(
    [Parameter(Mandatory=$true)][string]$CompilerPath,
    [Parameter(Mandatory=$true)][string]$ExpectedVersion
  )

  $ProbeDir = Join-Path ([System.IO.Path]::GetTempPath()) ("siming-inno-version-" + [guid]::NewGuid().ToString("N"))
  $ProbeScript = Join-Path $ProbeDir "version-probe.iss"
  New-Item -ItemType Directory -Force -Path $ProbeDir | Out-Null
  [System.IO.File]::WriteAllText(
    $ProbeScript,
    "[Setup]`r`nAppName=Siming Compiler Probe`r`nAppVersion=1.0`r`nDefaultDirName={tmp}\SimingCompilerProbe`r`n",
    [System.Text.UTF8Encoding]::new($false)
  )
  $SavedErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $VersionOutput = (& $CompilerPath "/O-" $ProbeScript 2>&1 | Out-String).Trim()
    $CompilerExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $SavedErrorActionPreference
    Remove-Item -LiteralPath $ProbeDir -Recurse -Force -ErrorAction SilentlyContinue
  }
  if ($CompilerExitCode -ne 0) {
    throw "Unable to query Inno Setup version from $CompilerPath (exit $CompilerExitCode): $VersionOutput"
  }
  $VersionMatch = [regex]::Match($VersionOutput, 'Compiler engine version:\s+.*?(?<Version>\d+\.\d+\.\d+)')
  if (-not $VersionMatch.Success) {
    throw "Unable to determine Inno Setup version from $CompilerPath output: $VersionOutput"
  }
  $ActualVersion = $VersionMatch.Groups["Version"].Value
  if ($ActualVersion -ne $ExpectedVersion) {
    throw "Inno Setup $ExpectedVersion is required for reproducible packaging; found $ActualVersion at $CompilerPath."
  }
  Write-Step "Pinned Inno Setup $ActualVersion verified: $CompilerPath"
}

function Read-AppVersion {
  $VersionFile = Join-Path $Root "backend\app\version.py"
  $Match = Select-String -Path $VersionFile -Pattern 'APP_VERSION\s*=\s*["'']([^"'']+)' | Select-Object -First 1
  if (-not $Match) { throw "Unable to read APP_VERSION from $VersionFile" }
  return $Match.Matches.Groups[1].Value
}

Write-Step "Checking Inno Setup compiler..."
$Iscc = Resolve-InnoCompiler
$PinnedInnoVersion = Read-PinnedInnoVersion
Assert-InnoCompilerVersion -CompilerPath $Iscc -ExpectedVersion $PinnedInnoVersion
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
