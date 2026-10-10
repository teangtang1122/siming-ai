"""Exercise the compiled installer without touching a real Siming installation."""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer integration")


@pytest.fixture(scope="module")
def compiled_installer(tmp_path_factory):
    compiler = os.environ.get("SIMING_INNO_ISCC") or shutil.which("ISCC.exe")
    if not compiler:
        for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
            candidate = Path(os.environ.get(env_name, "")) / "Inno Setup 6" / "ISCC.exe"
            if candidate.is_file():
                compiler = str(candidate)
                break
    if not compiler:
        pytest.skip("Inno Setup compiler is not installed")

    root = tmp_path_factory.mktemp("installer-runtime")
    payload = root / "payload"
    (payload / "_internal").mkdir(parents=True)
    (payload / "Siming.exe").write_bytes(b"new executable")
    (payload / "_internal" / "ucrtbase.dll").write_bytes(b"new runtime")

    # Use the production cleanup and [Code] verbatim. Only isolate installation
    # identity/registration/shortcuts and substitute a tiny inert payload.
    script = (ROOT / "installer" / "Siming.iss").read_text(encoding="utf-8-sig")
    script = script.replace(
        "AppId={{9D10D4A4-29F8-4F11-A88A-534A50F96D55}",
        "AppId=SimingInstallerRuntimeTest\nUninstallable=no\nCreateUninstallRegKey=no",
    ).replace("UsePreviousAppDir=yes", "UsePreviousAppDir=no")
    script = script.replace("UsePreviousTasks=yes", "UsePreviousTasks=no")
    script = script.replace(
        "SetupIconFile=..\\backend\\Siming.ico", f"SetupIconFile={ROOT / 'backend' / 'Siming.ico'}"
    ).replace('Source: "installed.marker"', f'Source: "{ROOT / "installer" / "installed.marker"}"')
    start, end = script.index("[Icons]"), script.index("[Run]")
    script = script[:start] + script[end:]
    source = root / "installer-test.iss"
    source.write_text(script, encoding="utf-8-sig")
    result = subprocess.run(
        [compiler, "/Qp", "/DMyAppVersion=0.0.0-test", f"/DSourceDir={payload}",
         f"/DOutputDir={root}", str(source)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return root / "Siming-Setup.exe"


def install(installer: Path, destination: Path, log: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(installer), "/SP-", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
         "/NOCLOSEAPPLICATIONS", "/SIMINGNOLAUNCH=1", "/MERGETASKS=!desktopicon",
         f"/DIR={destination}", f"/LOG={log}"],
        capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def tree_contents(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("locked_relative", [
    "Siming.exe", "_internal/ucrtbase.dll", "_internal/native/worker.pyd", "_internal/removed.dat",
])
def test_locked_upgrade_preserves_old_runtime_then_retries(compiled_installer, tmp_path, locked_relative):
    destination = tmp_path / "安装目录"
    (destination / "_internal" / "prompts").mkdir(parents=True)
    (destination / "Siming.exe").write_bytes(b"old executable")
    (destination / "_internal" / "ucrtbase.dll").write_bytes(b"old runtime")
    (destination / "_internal" / "prompts" / "obsolete.md").write_text("old prompt")
    # User-created files outside the immutable runtime must survive both paths.
    (destination / "author-notes.txt").write_text("keep author data")
    locked = destination / locked_relative
    locked.parent.mkdir(parents=True, exist_ok=True)
    locked.write_bytes(b"locked old content")
    before = tree_contents(destination)

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    # Allow reading, deny modification/deletion just like a running native module.
    handle = kernel.CreateFileW(str(locked), 0x80000000, 1, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    log = tmp_path / "locked.log"
    try:
        result = install(compiled_installer, destination, log)
        assert result.returncode != 0
        assert tree_contents(destination) == before, "Blocked update changed the old installation"
        assert "Runtime replacement blocked:" in log.read_text(encoding="utf-8-sig")
    finally:
        kernel.CloseHandle(handle)

    result = install(compiled_installer, destination, tmp_path / "retry.log")
    assert result.returncode == 0
    assert (destination / "Siming.exe").read_bytes() == b"new executable"
    assert (destination / "_internal" / "ucrtbase.dll").read_bytes() == b"new runtime"
    assert not (destination / "_internal" / "prompts" / "obsolete.md").exists()
    assert (destination / "author-notes.txt").read_text() == "keep author data"


def test_clean_install_works_with_no_existing_runtime(compiled_installer, tmp_path):
    destination = tmp_path / "new-install"
    result = install(compiled_installer, destination, tmp_path / "fresh.log")
    assert result.returncode == 0
    assert (destination / "Siming.exe").read_bytes() == b"new executable"
    assert (destination / ".siming-installed").is_file()
