param(
  [Parameter(Mandatory = $true)]
  [string]$Repository,
  [Parameter(Mandatory = $true)]
  [string]$Tag,
  [Parameter(Mandatory = $true)]
  [string]$DownloadDir,
  [string]$ExpectedVersion = ""
)

$ErrorActionPreference = "Stop"

$ExpectedAssets = @(
  "Siming-Setup.exe",
  "Siming-Setup.sha256",
  "Siming.apk",
  "Siming-apk-sha256.txt"
)
$ForbiddenAssets = @("Siming.exe", "update.json", "sha256.txt")

if (-not $ExpectedVersion) {
  if ($Tag -notmatch '^v(.+)$') { throw "Release tag must start with v: $Tag" }
  $ExpectedVersion = $Matches[1]
}

New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
$Verified = $false
for ($Attempt = 1; $Attempt -le 18; $Attempt++) {
  try {
    $Release = gh release view $Tag -R $Repository --json assets | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "gh release view failed with exit code $LASTEXITCODE" }

    $AvailableAssets = @($Release.assets | ForEach-Object { $_.name })
    $MissingAssets = @($ExpectedAssets | Where-Object { $_ -notin $AvailableAssets })
    $UnexpectedAssets = @($AvailableAssets | Where-Object { $_ -notin $ExpectedAssets })
    $ForbiddenFound = @($ForbiddenAssets | Where-Object { $_ -in $AvailableAssets })
    if ($ForbiddenFound.Count -gt 0) {
      throw "Published release contains legacy Windows assets: $($ForbiddenFound -join ', ')"
    }
    if ($MissingAssets.Count -gt 0) {
      throw "Published release assets are incomplete: $($MissingAssets -join ', ')"
    }
    if ($UnexpectedAssets.Count -gt 0) {
      throw "Published release contains unexpected assets: $($UnexpectedAssets -join ', ')"
    }

    foreach ($Asset in $ExpectedAssets) {
      Remove-Item -LiteralPath (Join-Path $DownloadDir $Asset) -Force -ErrorAction SilentlyContinue
    }
    gh release download $Tag -R $Repository `
      -p "Siming-Setup.exe" `
      -p "Siming-Setup.sha256" `
      -p "Siming.apk" `
      -p "Siming-apk-sha256.txt" `
      -D $DownloadDir --clobber
    if ($LASTEXITCODE -ne 0) { throw "gh release download failed with exit code $LASTEXITCODE" }

    $DownloadedAssets = @(
      Get-ChildItem -LiteralPath $DownloadDir -File |
        ForEach-Object { $_.Name } |
        Sort-Object
    )
    $ExpectedNames = @($ExpectedAssets | Sort-Object)
    if (($DownloadedAssets -join "`n") -ne ($ExpectedNames -join "`n")) {
      throw "Downloaded asset names do not match the four-file release contract: $($DownloadedAssets -join ', ')"
    }

    & (Join-Path $PSScriptRoot "verify-windows-installer.ps1") `
      -ReleaseDir $DownloadDir `
      -AllowUnsignedManualRelease
    if ($LASTEXITCODE -ne 0) { throw "Published Windows installer verification failed." }
    & (Join-Path $PSScriptRoot "verify-android-release.ps1") `
      -ReleaseDir $DownloadDir `
      -ExpectedVersion $ExpectedVersion
    if ($LASTEXITCODE -ne 0) { throw "Published Android release verification failed." }

    $Verified = $true
    break
  } catch {
    if ($Attempt -eq 18) { throw }
    Write-Host "Published asset verification attempt $Attempt/18 failed: $($_.Exception.Message)"
    Start-Sleep -Seconds 10
  }
}

if (-not $Verified) { throw "Published release assets could not be verified." }
Write-Host "Published release verified from a fresh download: $Tag" -ForegroundColor Green
