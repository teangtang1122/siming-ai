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

  $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash.ToLowerInvariant()
  Set-Content -LiteralPath $ShaPath -Encoding UTF8 -Value @(
    "$sha  $AppName.exe",
    "$sha  $LegacyAppName.exe"
  )

  gh release view $Tag -R $Repo 1>$null 2>$null
  if ($LASTEXITCODE -ne 0) {
    gh release create $Tag -R $Repo --title $Tag --notes "Moshu $Tag"
  }
  gh release upload $Tag -R $Repo $ExePath $LegacyExePath $ShaPath $ManifestPath --clobber
} finally {
  Pop-Location
}

Write-Host "Published: https://github.com/$Repo/releases/tag/$Tag"
