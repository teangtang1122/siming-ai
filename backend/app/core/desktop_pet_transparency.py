"""Per-window transparency policy for the Windows desktop pet."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


def disable_pet_system_backdrop(native) -> None:
    # DWMWA_SYSTEMBACKDROP_TYPE is supported starting with Windows 11 22621.
    # Older Windows cannot enable this backdrop and must not call this API.
    if sys.getwindowsversion().build < 22621:
        return
    dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    set_attribute = dwm.DwmSetWindowAttribute
    set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    set_attribute.restype = ctypes.c_long
    value = ctypes.c_int(1)  # DWMSBT_NONE; 0 allows DWM to choose a backdrop.
    result = set_attribute(int(native.Handle.ToInt64()), 38, ctypes.byref(value), ctypes.sizeof(value))
    if result != 0:
        raise OSError(f"Could not disable desktop-pet system backdrop: HRESULT 0x{result & 0xFFFFFFFF:08x}")


class WindowsPetTransparency:
    """Keep PyWebView theme updates from adding Mica to this one window.

    PyWebView's existing SystemEvents handler calls update_title_bar_theme on
    the instance. Wrapping that method preserves its theme handling and makes
    the pet's no-backdrop policy the final step, without changing OS settings.
    """

    def __init__(self, native) -> None:
        self.native = native
        self._closed = False
        self._original = native.update_title_bar_theme
        self._handler = self._update_title_bar_theme
        native.update_title_bar_theme = self._handler
        try:
            self._handler()
        except Exception:
            self.close()
            raise

    def _update_title_bar_theme(self) -> None:
        if self._closed or bool(getattr(self.native, "IsDisposed", False)):
            return
        self._original()
        self.apply()

    def apply(self) -> None:
        if not self._closed and not bool(getattr(self.native, "IsDisposed", False)):
            disable_pet_system_backdrop(self.native)

    def close(self) -> None:
        if not self._closed and self.native.update_title_bar_theme is self._handler:
            self.native.update_title_bar_theme = self._original
        self._closed = True
