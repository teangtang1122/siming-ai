"""Guardrails for the Windows installer distribution."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_installer_allows_path_selection_and_defaults_desktop_shortcut_on():
    script = (ROOT / "installer" / "Siming.iss").read_text(encoding="utf-8")

    assert "DisableDirPage=no" in script
    assert "DefaultDirName={localappdata}\\Programs\\Siming" in script
    assert 'Name: "desktopicon"' in script
    desktop_task = next(
        line for line in script.splitlines() if 'Name: "desktopicon"' in line
    )
    assert "unchecked" not in desktop_task.lower()
    assert 'Tasks: desktopicon' in script
    assert 'DestName: ".siming-installed"' in script
    assert "UsePreviousAppDir=yes" in script
    assert "UsePreviousTasks=yes" in script


def test_installer_build_uses_onedir_payload_without_portable_release_asset():
    script = (ROOT / "scripts" / "build-installer.ps1").read_text(encoding="utf-8")

    assert '"build-exe.ps1"' in script
    assert "-OneDir" in script
    assert '"Siming-Setup.exe"' in script
    assert "ISCC.exe" in script
    assert 'Remove-Item -LiteralPath (Join-Path $ReleaseDir $LegacyAsset)' in script
    assert '@("Siming.exe", "update.json", "sha256.txt")' in script
    assert "portable bridge" not in script.lower()


def test_installer_update_is_signed_and_verified_separately():
    signer = (ROOT / "scripts" / "sign-windows-installer.ps1").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify-windows-installer.ps1").read_text(encoding="utf-8")

    assert '"/fd", "SHA256"' in signer
    assert '"/tr", $TimestampUrl' in signer
    assert "TimeStamperCertificate" in signer
    assert "Siming-Setup.sha256" in signer
    assert "RequireTrustedSignature" in verifier
    assert "AllowUnsignedManualRelease" in verifier
    assert "Get-AuthenticodeSignature" in verifier
