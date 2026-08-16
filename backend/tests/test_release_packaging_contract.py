"""Release packaging must include modules loaded only by migration scripts."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_packager_includes_dynamic_database_migration_module():
    script = (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")

    assert '"--hidden-import", "app.database.migrations"' in script
    assert '"--add-data", "$(Join-Path $BackendDir \'alembic\')' in script
    assert "PackagerPythonVersion -ne $BuildPythonVersion" in script
    assert 'import tkinter' in script


def test_packager_uses_an_explicit_runtime_instead_of_the_backend_test_venv():
    script = (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")

    system_python = script.index('$Python = Get-Command "python"')
    backend_venv = script.index('$BackendPython = Join-Path $BackendDir')
    assert system_python < backend_venv
    assert "SIMING_BUILD_PYTHON" in script
    assert "Test-PackagingPython" in script
    assert "import sys,tkinter" in script
    assert '$ErrorActionPreference = "SilentlyContinue"' in script
    assert "base_executable" in script
    assert "$RuntimeChanged" in script


def test_publisher_stops_when_repository_verification_is_unavailable():
    script = (ROOT / "scripts" / "publish-github.ps1").read_text(encoding="utf-8")

    assert "gh repo create" not in script
    assert "Publishing stopped without changing repository state" in script
    assert "$ExistingTagExitCode" in script


def test_publisher_keeps_new_release_draft_until_all_assets_verify():
    script = (ROOT / "scripts" / "publish-github.ps1").read_text(encoding="utf-8")

    assert '"--draft"' in script
    assert "MissingUploadedAssets" in script
    assert "ForbiddenUploadedAssets" in script
    assert "--draft=false" in script
    assert "Assert-NativeSuccess \"upload release assets" in script


def test_gateway_smokes_every_published_architecture():
    workflow = (ROOT / ".github" / "workflows" / "gateway-image.yml").read_text(encoding="utf-8")

    assert "platforms: linux/amd64" in workflow
    assert "platforms: linux/arm64" in workflow
    assert "smoke_arch amd64" in workflow
    assert "smoke_arch arm64" in workflow


def test_android_release_verifies_version_code_and_trusted_certificate():
    script = (ROOT / "scripts" / "verify-android-release.ps1").read_text(encoding="utf-8")

    assert "ExpectedCertificateSha256" in script
    assert "certificate SHA-256 digest" in script
    assert "versionCode" in script
    assert "expectedVersionCode" in script


def test_windows_release_publishes_only_the_installer_and_verifies_signature():
    signer = (ROOT / "scripts" / "sign-windows-installer.ps1").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify-windows-installer.ps1").read_text(encoding="utf-8")
    publisher = (ROOT / "scripts" / "publish-github.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "release-gate.yml").read_text(encoding="utf-8")

    assert '"/fd", "SHA256"' in signer
    assert '"/tr", $TimestampUrl' in signer
    assert "$Signature.Status -ne \"Valid\"" in signer
    assert "TimeStamperCertificate" in signer
    assert "RequireTrustedSignature" in verifier
    assert "AllowUnsignedManualRelease" in verifier
    assert "Get-AuthenticodeSignature" in verifier
    assert "-RequireTrustedSignature" in publisher
    assert "ManualDownloadOnly" in publisher
    assert '$ReleaseAssets = @($InstallerPath, $InstallerShaPath)' in publisher
    assert '"Siming.exe", "update.json", "sha256.txt"' in publisher
    assert "SIMING_WINDOWS_CODESIGN_PFX_BASE64" in workflow
    assert "SIMING_WINDOWS_CODESIGN_PASSWORD" in workflow
    assert "manual-download-only" in workflow
    assert "github.event_name == 'push' && github.ref_type == 'tag'" in workflow
    assert workflow.index("Sign Windows installer") < workflow.index("Verify package assets")


def test_release_workflow_publishes_installer_and_signed_android_assets_only():
    publisher = (ROOT / "scripts" / "publish-github.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "release-gate.yml").read_text(encoding="utf-8")

    assert "IncludeAndroid" in publisher
    assert '$ReleaseAssets = @($InstallerPath, $InstallerShaPath)' in publisher
    assert "SIMING_ANDROID_KEYSTORE_BASE64" in workflow
    assert "Build signed Android APK" in workflow
    assert "verify-android-release.ps1" in workflow
    assert "release/Siming-Setup.exe" in workflow
    assert "release/Siming-Setup.sha256" in workflow
    assert "release/Siming.apk" in workflow
    assert "release/Siming-apk-sha256.txt" in workflow
    upload_block = workflow[workflow.index("Upload verified release assets"):workflow.index("Publish verified GitHub release")]
    assert "release/Siming.exe" not in upload_block
    assert "release/update.json" not in upload_block
    assert "release/sha256.txt" not in upload_block


def test_release_smoke_matches_runtime_to_update_manifest():
    script = (ROOT / "scripts" / "smoke-test-release.ps1").read_text(encoding="utf-8")

    assert '"$serverBaseUrl/health"' in script
    assert "$health.version -ne $expectedVersion" in script
