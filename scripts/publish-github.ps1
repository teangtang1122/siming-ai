param(
  [string]$Repo = "teangtang1122/siming-ai",
  [ValidateSet("public", "private")]
  [string]$Visibility = "private",
  [string]$Tag = "v2.6.4",
  [string]$CommitMessage = "",
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppName = "Siming"
$ExePath = Join-Path $Root "release\$AppName.exe"
$ManifestPath = Join-Path $Root "release\update.json"
$ShaPath = Join-Path $Root "release\sha256.txt"

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

  $remote = git remote get-url origin 2>$null
  if (-not $remote) {
    git remote add origin "https://github.com/$Repo.git"
  }

  gh repo view $Repo 1>$null 2>$null
  if ($LASTEXITCODE -ne 0) {
    gh repo create $Repo "--$Visibility" --source . --remote origin
  }

  if (-not $SkipBuild) {
    & (Join-Path $Root "scripts\build-exe.ps1")
  }

  if (-not (Test-Path $ExePath)) {
    throw "Release executable not found. Run build-exe.bat or publish without -SkipBuild."
  }

  $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash.ToLowerInvariant()
  $shaLines = @("$sha  $AppName.exe")
  Set-Content -LiteralPath $ShaPath -Encoding UTF8 -Value $shaLines

  $status = git status --porcelain
  if ($status) {
    if (-not $CommitMessage) {
      $CommitMessage = "release: Siming $Tag"
    }
    git add .
    git commit -m $CommitMessage
  }

  if (-not (git tag --list $Tag)) {
    git tag -a $Tag -m "Siming $Tag"
  }
  git push -u origin main --follow-tags

  $PreviousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  gh release view $Tag -R $Repo *>$null
  $ReleaseExists = $LASTEXITCODE -eq 0
  $ErrorActionPreference = $PreviousErrorActionPreference
  if (-not $ReleaseExists) {
    gh release create $Tag -R $Repo --title $Tag --notes "Siming $Tag"
  }
  $ExistingRelease = gh release view $Tag -R $Repo --json assets | ConvertFrom-Json
  $ExistingAssetNames = @($ExistingRelease.assets | ForEach-Object { $_.name })
  foreach ($LegacyAssetName in @("Moshu.exe", "NovelWritingAgent.exe")) {
    if ($ExistingAssetNames -contains $LegacyAssetName) {
      gh release delete-asset $Tag $LegacyAssetName -R $Repo -y
    }
  }
  $assets = @($ExePath, $ShaPath, $ManifestPath)
  gh release upload $Tag -R $Repo @assets --clobber
} finally {
  Pop-Location
}

Write-Host "Published: https://github.com/$Repo/releases/tag/$Tag"
