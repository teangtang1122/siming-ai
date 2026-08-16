param(
  [string]$ReleaseDir = "release",
  [switch]$RequireTrustedSignature,
  [switch]$AllowUnsignedManualRelease
)

$ErrorActionPreference = "Stop"

$InstallerPath = Join-Path $ReleaseDir "Siming-Setup.exe"
$ShaPath = Join-Path $ReleaseDir "Siming-Setup.sha256"
foreach ($RequiredPath in @($InstallerPath, $ShaPath)) {
  if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
    throw "Windows installer asset is missing: $RequiredPath"
  }
}

$ActualSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $InstallerPath).Hash.ToLowerInvariant()
$ShaTokens = ((Get-Content -LiteralPath $ShaPath -TotalCount 1).Trim() -split '\s+')
if ($ShaTokens.Count -lt 2 -or $ShaTokens[1] -ne "Siming-Setup.exe") {
  throw "Siming-Setup.sha256 must contain the Siming-Setup.exe file name."
}
if ($ShaTokens[0].ToLowerInvariant() -ne $ActualSha) {
  throw "Siming-Setup.sha256 does not match Siming-Setup.exe."
}
if ($RequireTrustedSignature -and $AllowUnsignedManualRelease) {
  throw "Choose either -RequireTrustedSignature or -AllowUnsignedManualRelease, not both."
}

if ($RequireTrustedSignature -or $AllowUnsignedManualRelease) {
  $Signature = Get-AuthenticodeSignature -FilePath $InstallerPath
  if ($Signature.Status -eq "Valid" -and $Signature.SignerCertificate) {
    if (-not $Signature.TimeStamperCertificate) {
      throw "Windows installer Authenticode signature has no trusted timestamp."
    }
    Write-Host "Trusted installer signer: $($Signature.SignerCertificate.Subject) thumbprint=$($Signature.SignerCertificate.Thumbprint)" -ForegroundColor Green
  } elseif ($AllowUnsignedManualRelease -and $Signature.Status -eq "NotSigned") {
    Write-Warning "Siming-Setup.exe is unsigned and may only be distributed for explicit manual installation. The in-app updater will reject it."
  } else {
    throw "Windows installer Authenticode signature is not trusted: status=$($Signature.Status) message=$($Signature.StatusMessage)"
  }
}

Write-Host "Windows installer verified: Siming-Setup.exe sha256=$ActualSha" -ForegroundColor Green
