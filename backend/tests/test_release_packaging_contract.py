"""Release packaging must include modules loaded only by migration scripts."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _canonical_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _exact_requirements(path: Path) -> dict[str, str]:
    requirements: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = line.split(";", 1)[0].strip()
        match = re.fullmatch(
            r"(?P<name>[A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==(?P<version>[^\s]+)",
            requirement,
        )
        assert match, f"dependency is not exactly pinned: {raw_line}"
        name = _canonical_package_name(match.group("name"))
        assert name not in requirements, f"duplicate dependency pin: {name}"
        requirements[name] = match.group("version")
    return requirements


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


def test_packager_embeds_and_verifies_windows_version_resource():
    script = (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")

    assert script.isascii()
    assert '"--version-file", $VersionInfoPath' in script
    assert "Write-WindowsVersionInfo" in script
    assert "Assert-WindowsVersionInfo" in script
    assert "from app.version import APP_VERSION" in script
    assert "StringStruct(u'CompanyName', u'teangtang1122')" in script
    assert "StringStruct(u'ProductName', u'$ProductDisplayName')" in script
    assert "StringStruct(u'FileDescription', u'$FileDescription')" in script
    assert "5Y+45ZG9IChTaW1pbmcp" in script
    assert "5Y+45ZG9IChTaW1pbmcpIOahjOmdouW6lOeUqA==" in script
    assert "StringStruct(u'OriginalFilename', u'Siming.exe')" in script
    assert "StringStruct(u'FileVersion', u'$FileVersion')" in script
    assert "StringStruct(u'ProductVersion', u'$Version')" in script


def test_windows_packaging_toolchain_and_python_environment_are_fully_pinned():
    toolchain = json.loads((ROOT / "build-toolchain.json").read_text(encoding="utf-8"))
    build_lock = _exact_requirements(ROOT / "backend" / "requirements-windows-build.lock")
    runtime_requirements = _exact_requirements(ROOT / "backend" / "requirements.txt")
    script = (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")

    assert toolchain == {
        "schema_version": 1,
        "python": "3.11.9",
        "python_implementation": "CPython",
        "python_architecture": "64bit",
        "pip": "26.2.1",
        "setuptools": "79.0.1",
        "pyinstaller": "6.21.0",
        "node": "24.14.1",
        "npm": "11.11.0",
        "inno_setup": "6.7.1",
    }
    assert len(build_lock) >= 70
    assert build_lock["pip"] == toolchain["pip"]
    assert build_lock["setuptools"] == toolchain["setuptools"]
    assert build_lock["pyinstaller"] == toolchain["pyinstaller"]
    for package, version in runtime_requirements.items():
        assert build_lock.get(package) == version

    assert "requirements-windows-build.lock" in script
    assert '"--no-deps"' in script
    assert '"--only-binary=:all:"' in script
    assert '"--no-binary=proxy_tools"' in script
    assert '"--no-build-isolation"' in script
    assert "verify-python-build-lock.py" in script
    assert '"pip==$($Toolchain.pip)"' in script
    assert '"setuptools==$($Toolchain.setuptools)"' in script
    assert "--trusted-host" not in script


def test_frontend_build_uses_the_exact_node_npm_and_package_lock():
    toolchain = json.loads((ROOT / "build-toolchain.json").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads(
        (ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )
    script = (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")

    assert package["packageManager"] == f"npm@{toolchain['npm']}"
    assert package["engines"] == {
        "node": toolchain["node"],
        "npm": toolchain["npm"],
    }
    assert package_lock["lockfileVersion"] == 3
    assert package_lock["packages"][""]["engines"] == package["engines"]
    assert 'Invoke-Native $NpmExe @("ci")' in script
    assert 'Invoke-Native "npm" @("install")' not in script


def test_windows_ci_reads_the_same_pinned_toolchain():
    for workflow_name in ("windows-installer-ci.yml", "release-gate.yml"):
        workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text(
            encoding="utf-8"
        )
        assert "Read pinned Windows toolchain" in workflow
        assert "steps.windows_toolchain.outputs.python" in workflow
        assert "steps.windows_toolchain.outputs.pip" in workflow
        assert "steps.windows_toolchain.outputs.node" in workflow
        assert "steps.windows_toolchain.outputs.npm" in workflow
        assert "steps.windows_toolchain.outputs.inno_setup" in workflow
        assert "--allow-downgrade" in workflow

    toolchain = json.loads((ROOT / "build-toolchain.json").read_text(encoding="utf-8"))
    for workflow_name in ("architecture-ci.yml", "frontend-ci.yml"):
        workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text(
            encoding="utf-8"
        )
        assert f'node-version: "{toolchain["node"]}"' in workflow


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
