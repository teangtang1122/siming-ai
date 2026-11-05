from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.desktop_pet_transparency import WindowsPetTransparency, disable_pet_system_backdrop


class Native:
    def __init__(self, dark=True):
        self.dark = dark
        self.IsDisposed = False
        self.Handle = SimpleNamespace(ToInt64=lambda: 0x123456789)
        self.backdrop = 0
        self.updates = 0

    def update_title_bar_theme(self):
        self.updates += 1
        self.backdrop = 2 if self.dark else 1

    def on_system_theme_changed(self):
        self.update_title_bar_theme()


def test_only_pet_stays_transparent_across_system_theme_updates():
    pet, main = Native(), Native()
    original = pet.update_title_bar_theme
    with patch("app.core.desktop_pet_transparency.disable_pet_system_backdrop",
               side_effect=lambda n: setattr(n, "backdrop", 1)) as disable:
        policy = WindowsPetTransparency(pet)
        assert pet.backdrop == 1
        for dark in [False, True, False, True]:
            pet.dark = main.dark = dark
            pet.on_system_theme_changed()
            main.on_system_theme_changed()
            assert pet.backdrop == 1
            assert main.backdrop == (2 if dark else 1)
        policy.apply()  # also used after an HWND recreation or showing the pet.
        assert disable.call_count == 6
        policy.close()
        policy.close()
        assert pet.update_title_bar_theme == original
        pet.on_system_theme_changed()
        assert pet.backdrop == 2


def test_failed_setup_restores_original_theme_method():
    native = Native()
    original = native.update_title_bar_theme
    with patch("app.core.desktop_pet_transparency.disable_pet_system_backdrop", side_effect=OSError("failure")):
        with pytest.raises(OSError):
            WindowsPetTransparency(native)
    assert native.update_title_bar_theme == original


def test_disposed_pet_does_not_reapply_a_backdrop():
    native = Native()
    with patch("app.core.desktop_pet_transparency.disable_pet_system_backdrop") as disable:
        policy = WindowsPetTransparency(native)
        native.IsDisposed = True
        native.on_system_theme_changed()
        policy.apply()
        assert native.updates == 1
        disable.assert_called_once_with(native)
        policy.close()


@pytest.mark.parametrize("result", [0, -2147024891])
def test_no_backdrop_uses_pointer_sized_handle_and_checks_hresult(result):
    dll = MagicMock()
    dll.DwmSetWindowAttribute.return_value = result
    with patch("app.core.desktop_pet_transparency.sys.getwindowsversion", return_value=SimpleNamespace(build=26300), create=True), patch(
        "app.core.desktop_pet_transparency.ctypes.WinDLL", return_value=dll, create=True,
    ):
        if result:
            with pytest.raises(OSError, match="80070005"):
                disable_pet_system_backdrop(Native())
        else:
            disable_pet_system_backdrop(Native())
    args = dll.DwmSetWindowAttribute.call_args.args
    assert args[0] == 0x123456789 and args[1] == 38
    assert args[2]._obj.value == 1 and args[3] == 4


def test_windows_10_does_not_call_unsupported_backdrop_attribute():
    with patch("app.core.desktop_pet_transparency.sys.getwindowsversion", return_value=SimpleNamespace(build=19045), create=True), patch(
        "app.core.desktop_pet_transparency.ctypes.WinDLL", create=True,
    ) as load:
        disable_pet_system_backdrop(Native())
        load.assert_not_called()
