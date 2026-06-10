param(
  [string]$Repo = "teangtang1122/NovelWritingAgent",
  [ValidateSet("public", "private")]
  [string]$Visibility = "private",
  [string]$Tag = "v0.1.1"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppName = "Moshu"
$LegacyAppName = "NovelWritingAgent"
$ExePath = Join-Path $Root "release\$AppName.exe"
$LegacyExePath = Join-Path $Root "release\$LegacyAppName.exe"
$ManifestPath = Join-Path $Root "release\update.json"
$ShaPath = Join-Path $Root "release\sha256.txt"
$SetupMcpScriptName = "setup-external-agent-mcp.ps1"
$SetupMcpScriptSource = Join-Path $Root "scripts\$SetupMcpScriptName"
$SetupMcpScriptPath = Join-Path $Root "release\$SetupMcpScriptName"

function Require-Command {
  param([string]$Name, [string]$Hint)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw $Hint
  }
}

Require-Command "git" "Git is required."
Require-Command "gh" "GitHub CLI is required. Install it, then run: gh auth login"

Push-Location $Root
try {
  if (-not (Test-Path ".git")) {
    git init -b main
  }

  $status = git status --porcelain
  if ($status) {
    git add .
    git commit -m "Update Moshu"
  }

  $remote = git remote get-url origin 2>$null
  if (-not $remote) {
    git remote add origin "https://github.com/$Repo.git"
  }

  gh repo view $Repo 1>$null 2>$null
  if ($LASTEXITCODE -ne 0) {
    gh repo create $Repo "--$Visibility" --source . --remote origin --push
  } else {
    git push -u origin main
  }

  if (-not (git tag --list $Tag)) {
    git tag -a $Tag -m "Moshu $Tag"
  }
  git push origin $Tag

  if (-not (Test-Path $ExePath)) {
    & (Join-Path $Root "build-exe.bat")
  }
  if (-not (Test-Path $LegacyExePath)) {
    Copy-Item -LiteralPath $ExePath -Destination $LegacyExePath -Force
  }
  if ((Test-Path $SetupMcpScriptSource) -and (-not (Test-Path $SetupMcpScriptPath))) {
    Copy-Item -LiteralPath $SetupMcpScriptSource -Destination $SetupMcpScriptPath -Force
  }

  $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash.ToLowerInvariant()
  $shaLines = @(
    "$sha  $AppName.exe",
    "$sha  $LegacyAppName.exe"
  )
  if (Test-Path $SetupMcpScriptPath) {
    $setupSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $SetupMcpScriptPath).Hash.ToLowerInvariant()
    $shaLines += "$setupSha  $SetupMcpScriptName"
  }
  Set-Content -LiteralPath $ShaPath -Encoding UTF8 -Value $shaLines

  $PreviousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  gh release view $Tag -R $Repo *>$null
  $ReleaseExists = $LASTEXITCODE -eq 0
  $ErrorActionPreference = $PreviousErrorActionPreference
  if (-not $ReleaseExists) {
    gh release create $Tag -R $Repo --title $Tag --notes "Moshu $Tag"
  }
  $assets = @($ExePath, $LegacyExePath, $ShaPath, $ManifestPath)
  if (Test-Path $SetupMcpScriptPath) {
    $assets += $SetupMcpScriptPath
  }
  gh release upload $Tag -R $Repo @assets --clobber
} finally {
  Pop-Location
}

Write-Host "Published: https://github.com/$Repo/releases/tag/$Tag"
