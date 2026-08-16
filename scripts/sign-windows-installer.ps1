param(
  [string]$ReleaseDir = "release",
  [Parameter(Mandatory = $true)]
  [string]$CertificatePath,
  [Parameter(Mandatory = $true)]
  [AllowEmptyString()]
  [string]$CertificatePassword,
  [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"

function Resolve-SignTool {
  if ($env:SIMING_SIGNTOOL_PATH) {
    $ConfiguredPath = [System.IO.Path]::GetFullPath($env:SIMING_SIGNTOOL_PATH)
    if (-not (Test-Path -LiteralPath $ConfiguredPath -PathType Leaf)) {
      throw "SIMING_SIGNTOOL_PATH does not exist: $ConfiguredPath"
    }
    return $ConfiguredPath
  }

  $Command = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
  if ($Command) { return $Command.Source }

  $WindowsKitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
  if (Test-Path -LiteralPath $WindowsKitsRoot -PathType Container) {
    $Candidates = @(
      Get-ChildItem -LiteralPath $WindowsKitsRoot -Filter "signtool.exe" -File -Recurse |
        Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
        Sort-Object FullName -Descending
    )
    if ($Candidates.Count -gt 0) { return $Candidates[0].FullName }
  }

  throw "Windows SDK signtool.exe is required to sign Siming-Setup.exe."
}

$ResolvedReleaseDir = [System.IO.Path]::GetFullPath($ReleaseDir)
$InstallerPath = Join-Path $ResolvedReleaseDir "Siming-Setup.exe"
$ShaPath = Join-Path $ResolvedReleaseDir "Siming-Setup.sha256"
$ResolvedCertificatePath = [System.IO.Path]::GetFullPath($CertificatePath)
foreach ($RequiredPath in @($InstallerPath, $ResolvedCertificatePath)) {
  if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
    throw "Windows installer signing input is missing: $RequiredPath"
  }
}

$SignTool = Resolve-SignTool
$SignArguments = @(
  "sign",
  "/fd", "SHA256",
  "/td", "SHA256",
  "/tr", $TimestampUrl,
  "/f", $ResolvedCertificatePath
)
if ($CertificatePassword) {
  $SignArguments += @("/p", $CertificatePassword)
}
$SignArguments += $InstallerPath

Write-Host "Signing Siming-Setup.exe with a trusted, timestamped Authenticode signature..."
& $SignTool @SignArguments
if ($LASTEXITCODE -ne 0) {
  throw "signtool.exe failed with exit code $LASTEXITCODE."
}

$Signature = Get-AuthenticodeSignature -FilePath $InstallerPath
if ($Signature.Status -ne "Valid" -or -not $Signature.SignerCertificate) {
  throw "Signed installer is not trusted: status=$($Signature.Status) message=$($Signature.StatusMessage)"
}
if (-not $Signature.TimeStamperCertificate) {
  throw "Signed installer has no trusted timestamp."
}

$Sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $InstallerPath).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
  $ShaPath,
  "$Sha256  Siming-Setup.exe" + [Environment]::NewLine,
  [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Windows installer signed and checksum refreshed." -ForegroundColor Green
Write-Host "Signer: $($Signature.SignerCertificate.Subject)"
Write-Host "Thumbprint: $($Signature.SignerCertificate.Thumbprint)"
Write-Host "SHA256: $Sha256"
