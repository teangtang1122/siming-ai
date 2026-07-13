param(
  [string]$ReleaseDir = "release",
  [string]$AppName = "Siming",
  [string]$ExpectedVersion = ""
)

$ErrorActionPreference = "Stop"

$ExePath = Join-Path $ReleaseDir "$AppName.exe"
$ManifestPath = Join-Path $ReleaseDir "update.json"
$ShaPath = Join-Path $ReleaseDir "sha256.txt"

foreach ($AssetPath in @($ExePath, $ManifestPath, $ShaPath)) {
  if (-not (Test-Path -LiteralPath $AssetPath)) {
    throw "Release asset is missing: $AssetPath"
  }
}

$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$ActualSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash.ToLowerInvariant()
$ShaTokens = ((Get-Content -LiteralPath $ShaPath -TotalCount 1).Trim() -split '\s+')

if ($ShaTokens.Count -lt 2 -or $ShaTokens[1] -ne "$AppName.exe") {
  throw "sha256.txt must contain the $AppName.exe file name."
}
if ($Manifest.sha256 -ne $ActualSha) {
  throw "update.json SHA256 does not match $AppName.exe."
}
if ($ShaTokens[0].ToLowerInvariant() -ne $ActualSha) {
  throw "sha256.txt SHA256 does not match $AppName.exe."
}
if ($ExpectedVersion -and $Manifest.version -ne $ExpectedVersion) {
  throw "update.json version '$($Manifest.version)' does not match expected '$ExpectedVersion'."
}
if (-not $Manifest.version -or -not $Manifest.download_url) {
  throw "update.json must contain version and download_url."
}

Write-Host "Release assets verified: $AppName.exe version=$($Manifest.version) sha256=$ActualSha" -ForegroundColor Green
