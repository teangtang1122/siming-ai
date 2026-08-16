param(
  [string]$Repo = "teangtang1122/siming-ai",
  [ValidateSet("public", "private")]
  [string]$Visibility = "private",
  [string]$Tag = "",
  [string]$CommitMessage = "",
  [switch]$SkipBuild,
  [switch]$CommitDirtyChanges,
  [switch]$IncludeAndroid,
  [switch]$ManualDownloadOnly,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppName = "Siming"
$InstallerPath = Join-Path $Root "release\$AppName-Setup.exe"
$InstallerShaPath = Join-Path $Root "release\$AppName-Setup.sha256"
$ApkPath = Join-Path $Root "release\$AppName.apk"
$ApkShaPath = Join-Path $Root "release\$AppName-apk-sha256.txt"
$ReleaseAssets = @($InstallerPath, $InstallerShaPath)
if ($IncludeAndroid) {
  $ReleaseAssets += @($ApkPath, $ApkShaPath)
}
$ForbiddenWindowsAssets = @("Siming.exe", "update.json", "sha256.txt", "Moshu.exe", "NovelWritingAgent.exe")

function Require-Command {
  param([string]$Name, [string]$Hint)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw $Hint
  }
}

function Assert-NativeSuccess {
  param([string]$Action)
  if ($LASTEXITCODE -ne 0) {
    throw "$Action failed with exit code $LASTEXITCODE."
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
    Assert-NativeSuccess "git init"
  }

  $remote = git remote get-url origin 2>$null
  if (-not $remote) {
    if ($DryRun) { throw "Dry run requires an origin remote." }
    git remote add origin "https://github.com/$Repo.git"
    Assert-NativeSuccess "git remote add origin"
  }

  if (-not $DryRun) {
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $RepoViewOutput = gh repo view $Repo 2>&1
    $RepoViewExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorActionPreference
    if ($RepoViewExitCode -ne 0) {
      $RepoViewMessage = ($RepoViewOutput | Out-String).Trim()
      throw "Unable to verify GitHub repository '$Repo'. Publishing stopped without changing repository state. $RepoViewMessage"
    }
  }

  if (-not $SkipBuild -and -not $DryRun) {
    & (Join-Path $Root "scripts\build-installer.ps1")
  }

  $MissingReleaseAssets = @($ReleaseAssets | Where-Object { -not (Test-Path -LiteralPath $_) })
  if ($MissingReleaseAssets.Count -gt 0) {
    $MissingAssetList = ($MissingReleaseAssets | ForEach-Object { Split-Path -Leaf $_ }) -join ", "
    if ($DryRun) {
      Write-Host "[dry-run] Required release assets missing: $MissingAssetList" -ForegroundColor Yellow
    } else {
      throw "Required release assets are missing: $MissingAssetList. Build and verify the selected distributions before publishing."
    }
  }

  if (-not $DryRun) {
    if ($ManualDownloadOnly) {
      & (Join-Path $Root "scripts\verify-windows-installer.ps1") -ReleaseDir (Split-Path -Parent $InstallerPath) -AllowUnsignedManualRelease
    } else {
      & (Join-Path $Root "scripts\verify-windows-installer.ps1") -ReleaseDir (Split-Path -Parent $InstallerPath) -RequireTrustedSignature
    }
    if ($IncludeAndroid) {
      $version = $Tag.TrimStart("v")
      & (Join-Path $Root "scripts\verify-android-release.ps1") -ReleaseDir (Split-Path -Parent $ApkPath) -ExpectedVersion $version
    }
  }

  $status = git status --porcelain
  if ($status) {
    if (-not $CommitDirtyChanges) {
      throw "Working tree has uncommitted changes. Commit intentionally first, or explicitly stage reviewed files and pass -CommitDirtyChanges with -CommitMessage."
    }
    if (-not $CommitMessage) {
      throw "-CommitDirtyChanges requires an explicit -CommitMessage."
    }
    $UnstagedChanges = @(git diff --name-only)
    $UntrackedChanges = @(git ls-files --others --exclude-standard)
    $StagedChanges = @(git diff --cached --name-only)
    if ($UnstagedChanges.Count -gt 0 -or $UntrackedChanges.Count -gt 0) {
      throw "Automatic staging is disabled for releases. Review the working tree, explicitly stage only approved files, and rerun with -CommitDirtyChanges."
    }
    if ($StagedChanges.Count -eq 0) {
      throw "-CommitDirtyChanges requires explicitly staged, reviewed changes."
    }
    if ($DryRun) {
      Write-Host "[dry-run] Would commit explicitly staged changes with message: $CommitMessage" -ForegroundColor Cyan
      git diff --cached --name-only
    } else {
      git commit -m $CommitMessage
      Assert-NativeSuccess "git commit"
    }
  }

  if ($DryRun) {
    Write-Host "[dry-run] Tag: $Tag" -ForegroundColor Cyan
    Write-Host "[dry-run] Repo: $Repo" -ForegroundColor Cyan
    Write-Host "[dry-run] Assets:" -ForegroundColor Cyan
    foreach ($Asset in $ReleaseAssets) {
      Write-Host "  $Asset exists=$(Test-Path -LiteralPath $Asset)"
    }
    Write-Host "[dry-run] Legacy Windows assets are forbidden in Releases: $($ForbiddenWindowsAssets -join ', ')" -ForegroundColor Cyan
    return
  }

  $CurrentBranch = (git branch --show-current).Trim()
  Assert-NativeSuccess "git branch --show-current"
  if (-not $CurrentBranch) {
    throw "Release publishing requires a named branch, not detached HEAD."
  }

  $PreviousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $ExistingTagCommit = git rev-list -n 1 $Tag 2>$null
  $ExistingTagExitCode = $LASTEXITCODE
  $ErrorActionPreference = $PreviousErrorActionPreference
  if ($ExistingTagExitCode -ne 0) {
    $ExistingTagCommit = ""
  } else {
    $ExistingTagCommit = ($ExistingTagCommit | Select-Object -First 1).Trim()
  }

  $HeadCommit = (git rev-parse HEAD).Trim()
  Assert-NativeSuccess "git rev-parse HEAD"
  if ($ExistingTagCommit -and $ExistingTagCommit -ne $HeadCommit) {
    throw "Tag $Tag already points to $ExistingTagCommit, not HEAD $HeadCommit."
  }
  if (-not $ExistingTagCommit) {
    git tag -a $Tag -m "Siming $Tag"
    Assert-NativeSuccess "git tag $Tag"
  }

  git push -u origin $CurrentBranch
  Assert-NativeSuccess "git push branch $CurrentBranch"
  git push origin $Tag
  Assert-NativeSuccess "git push tag $Tag"

  $PreviousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  gh release view $Tag -R $Repo *>$null
  $ReleaseExists = $LASTEXITCODE -eq 0
  $ErrorActionPreference = $PreviousErrorActionPreference

  $ReleaseWasDraft = $false
  if (-not $ReleaseExists) {
    $Version = $Tag.TrimStart("v")
    $NotesPath = Join-Path $Root "docs\release-notes-$Version.md"
    $ReleaseArgs = @(
      "release", "create", $Tag,
      "-R", $Repo,
      "--title", $Tag,
      "--draft"
    )
    if (Test-Path -LiteralPath $NotesPath) {
      $ReleaseArgs += @("--notes-file", $NotesPath)
    } else {
      $ReleaseArgs += @("--notes", "Siming $Tag")
    }
    if ($Tag.Contains("-")) {
      $ReleaseArgs += "--prerelease"
    }
    gh @ReleaseArgs
    Assert-NativeSuccess "create draft release $Tag"
    $ReleaseWasDraft = $true
  } else {
    $ReleaseState = gh release view $Tag -R $Repo --json isDraft | ConvertFrom-Json
    Assert-NativeSuccess "read release state for $Tag"
    $ReleaseWasDraft = [bool]$ReleaseState.isDraft
    if ($Tag.Contains("-")) {
      gh release edit $Tag -R $Repo --prerelease
      Assert-NativeSuccess "mark release $Tag as prerelease"
    }
  }

  $ExistingRelease = gh release view $Tag -R $Repo --json assets | ConvertFrom-Json
  Assert-NativeSuccess "read release assets for $Tag"
  $ExistingAssetNames = @($ExistingRelease.assets | ForEach-Object { $_.name })
  foreach ($LegacyAssetName in $ForbiddenWindowsAssets) {
    if ($ExistingAssetNames -contains $LegacyAssetName) {
      gh release delete-asset $Tag $LegacyAssetName -R $Repo -y
      Assert-NativeSuccess "delete legacy release asset $LegacyAssetName"
    }
  }

  gh release upload $Tag -R $Repo @ReleaseAssets --clobber
  Assert-NativeSuccess "upload release assets for $Tag"

  $UploadedRelease = gh release view $Tag -R $Repo --json assets,isDraft | ConvertFrom-Json
  Assert-NativeSuccess "verify uploaded release assets for $Tag"
  $UploadedAssetNames = @($UploadedRelease.assets | ForEach-Object { $_.name })
  $ExpectedAssetNames = @($ReleaseAssets | ForEach-Object { Split-Path -Leaf $_ })
  $MissingUploadedAssets = @($ExpectedAssetNames | Where-Object { $_ -notin $UploadedAssetNames })
  if ($MissingUploadedAssets.Count -gt 0) {
    throw "Release remains unpublished because uploaded assets are incomplete: $($MissingUploadedAssets -join ', ')."
  }
  $ForbiddenUploadedAssets = @($ForbiddenWindowsAssets | Where-Object { $_ -in $UploadedAssetNames })
  if ($ForbiddenUploadedAssets.Count -gt 0) {
    throw "Release remains unpublished because legacy Windows assets are still present: $($ForbiddenUploadedAssets -join ', ')."
  }

  if ($ReleaseWasDraft) {
    gh release edit $Tag -R $Repo --draft=false
    Assert-NativeSuccess "publish verified release $Tag"
  }

  $PublishedRelease = gh release view $Tag -R $Repo --json isDraft,url | ConvertFrom-Json
  Assert-NativeSuccess "verify published release $Tag"
  if ($PublishedRelease.isDraft) {
    throw "Release $Tag is still a draft after verified asset upload."
  }
  $PublishedUrl = $PublishedRelease.url
} finally {
  Pop-Location
}

Write-Host "Published: $PublishedUrl"
