param(
  [string]$Repo = "teangtang1122/siming-ai",
  [ValidateSet("public", "private")]
  [string]$Visibility = "private",
  [string]$Tag = "",
  [string]$CommitMessage = "",
  [switch]$SkipBuild,
  [switch]$CommitDirtyChanges,
  [switch]$DryRun
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
if (-not $DryRun) {
  Require-Command "gh" "GitHub CLI is required. Install it, then run: gh auth login"
}

Push-Location $Root
try {
  if (-not $Tag) {
    $PackageJsonPath = Join-Path $Root "frontend\package.json"
    if (-not (Test-Path -LiteralPath $PackageJsonPath)) {
      throw "Cannot derive release tag: frontend\package.json not found. Pass -Tag explicitly."
    }
    $PackageJson = Get-Content -LiteralPath $PackageJsonPath -Raw | ConvertFrom-Json
    if (-not $PackageJson.version) {
      throw "Cannot derive release tag: frontend\package.json has no version. Pass -Tag explicitly."
    }
    $Tag = "v$($PackageJson.version)"
  }

  if (-not (Test-Path ".git")) {
    if ($DryRun) { throw "Dry run requires an existing git repository." }
    git init -b main
  }

  $remote = git remote get-url origin 2>$null
  if (-not $remote) {
    if ($DryRun) { throw "Dry run requires an origin remote." }
    git remote add origin "https://github.com/$Repo.git"
  }

  if (-not $DryRun) {
    gh repo view $Repo 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
      gh repo create $Repo "--$Visibility" --source . --remote origin
    }
  }

  if (-not $SkipBuild -and -not $DryRun) {
    & (Join-Path $Root "scripts\build-exe.ps1")
  }

  if (-not (Test-Path $ExePath)) {
    if ($DryRun) {
      Write-Host "[dry-run] Release executable missing: $ExePath" -ForegroundColor Yellow
    } else {
    throw "Release executable not found. Run build-exe.bat or publish without -SkipBuild."
    }
  }

  if (Test-Path $ExePath) {
    $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash.ToLowerInvariant()
    $version = $Tag.TrimStart("v")
    $isPrerelease = $version.Contains("-")
    $updateChannel = if ($isPrerelease) { "preview" } else { "stable" }
    $downloadUrl = if ($isPrerelease) {
      "https://github.com/$Repo/releases/download/$Tag/$AppName.exe"
    } else {
      "https://github.com/$Repo/releases/latest/download/$AppName.exe"
    }
    $manifest = [ordered]@{
      version = $version
      channel = $updateChannel
      download_url = $downloadUrl
      sha256 = $sha
      repo = $Repo
    } | ConvertTo-Json -Depth 3
    $shaLines = @("$sha  $AppName.exe")
    if ($DryRun) {
      Write-Host "[dry-run] SHA256 would be written: $sha" -ForegroundColor Cyan
      Write-Host "[dry-run] Manifest version would be written: $version" -ForegroundColor Cyan
    } else {
      [System.IO.File]::WriteAllText($ManifestPath, $manifest + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
      [System.IO.File]::WriteAllText($ShaPath, ($shaLines -join [Environment]::NewLine) + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
      & (Join-Path $Root "scripts\verify-release-assets.ps1") -ReleaseDir (Split-Path -Parent $ExePath) -AppName $AppName -ExpectedVersion $version
    }
  }

  $status = git status --porcelain
  if ($status) {
    if (-not $CommitDirtyChanges) {
      throw "Working tree has uncommitted changes. Commit intentionally first, or pass -CommitDirtyChanges with -CommitMessage."
    }
    if (-not $CommitMessage) {
      throw "-CommitDirtyChanges requires an explicit -CommitMessage."
    }
    if ($DryRun) {
      Write-Host "[dry-run] Would commit dirty changes with message: $CommitMessage" -ForegroundColor Cyan
      git status --short
    } else {
      git add -u
      git add .github scripts backend frontend docs README.md
      git commit -m $CommitMessage
    }
  }

  if ($DryRun) {
    Write-Host "[dry-run] Tag: $Tag" -ForegroundColor Cyan
    Write-Host "[dry-run] Repo: $Repo" -ForegroundColor Cyan
    Write-Host "[dry-run] Assets:" -ForegroundColor Cyan
    foreach ($Asset in @($ExePath, $ShaPath, $ManifestPath)) {
      Write-Host "  $Asset exists=$(Test-Path -LiteralPath $Asset)"
    }
    return
  }

  $CurrentBranch = (git branch --show-current).Trim()
  if (-not $CurrentBranch) {
    throw "Release publishing requires a named branch, not detached HEAD."
  }
  $ExistingTagCommit = git rev-list -n 1 $Tag 2>$null
  if ($LASTEXITCODE -ne 0) {
    $ExistingTagCommit = ""
  } else {
    $ExistingTagCommit = ($ExistingTagCommit | Select-Object -First 1).Trim()
  }
  $HeadCommit = (git rev-parse HEAD).Trim()
  if ($ExistingTagCommit -and $ExistingTagCommit -ne $HeadCommit) {
    throw "Tag $Tag already points to $ExistingTagCommit, not HEAD $HeadCommit."
  }
  if (-not $ExistingTagCommit) {
    git tag -a $Tag -m "Siming $Tag"
  }
  git push -u origin $CurrentBranch
  git push origin $Tag

  $PreviousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  gh release view $Tag -R $Repo *>$null
  $ReleaseExists = $LASTEXITCODE -eq 0
  $ErrorActionPreference = $PreviousErrorActionPreference
  if (-not $ReleaseExists) {
    $ReleaseArgs = @(
      "release", "create", $Tag,
      "-R", $Repo,
      "--title", $Tag,
      "--notes", "Siming $Tag"
    )
    if ($Tag.Contains("-")) {
      $ReleaseArgs += "--prerelease"
    }
    gh @ReleaseArgs
  } elseif ($Tag.Contains("-")) {
    gh release edit $Tag -R $Repo --prerelease
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
