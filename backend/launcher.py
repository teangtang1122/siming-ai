"""Packaged desktop launcher for 司命 (Siming)."""
# The trust backend must run before network-framework imports, so normal import
# sorting and top-of-file placement are intentionally disabled in this module.
# ruff: noqa: E402, I001
from __future__ import annotations

import os
import json
import hashlib
import math
import socket
import sys
import tempfile
import threading
import traceback
import ctypes
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from app.core.system_trust import configure_system_trust


SYSTEM_TRUST_STATUS = configure_system_trust()

import uvicorn  # noqa: E402,F401 - import only after native trust is configured

from app.core.desktop_instance import DesktopInstanceCoordinator
from app.core.uvicorn_controller import UvicornServerController
from app.version import APP_VERSION


APP_NAME = "Siming"
LEGACY_APP_NAMES = ("Moshu", "NovelWritingAgent")
DEFAULT_PORT = 8765
DESKTOP_PET_BASE_WIDTH = 340
DESKTOP_PET_BASE_HEIGHT = 300
DESKTOP_PET_MIN_WIDTH = 238
DESKTOP_PET_MIN_HEIGHT = 210
DESKTOP_PET_EDGE_MARGIN = 24
DESKTOP_PET_TRANSPARENCY_KEY = "#FF00FF"
_STDIO_LOG_HANDLES = []
_MCP_STDIO_HANDLES = []


class DesktopApi:
    """Small native bridge exposed only to the embedded desktop window."""

    def __init__(self) -> None:
        self._window = None
        self._pet_window = None
        self._folder_dialog_type = None
        self._gui_url = None
        self._pet_input_overlay = None
        self._position_lock = threading.Lock()
        self._position_timer: threading.Timer | None = None
        self._pending_position: tuple[int, int] | None = None
        self._drag_lock = threading.Lock()
        self._desktop_pet_drag: tuple[float, float, int, int] | None = None

    def bind(
        self,
        window,
        folder_dialog_type,
        *,
        pet_window=None,
        gui_url: str | None = None,
    ) -> None:
        self._window = window
        self._pet_window = pet_window
        self._folder_dialog_type = folder_dialog_type
        self._gui_url = gui_url

    def attach_desktop_pet_input_overlay(self, overlay) -> None:
        self._pet_input_overlay = overlay

    def select_export_directory(self) -> str:
        if self._window is None or self._folder_dialog_type is None:
            return ""
        selected = self._window.create_file_dialog(
            self._folder_dialog_type,
            allow_multiple=False,
        )
        if not selected:
            return ""
        if isinstance(selected, (list, tuple)):
            return str(selected[0]) if selected else ""
        return str(selected)

    @staticmethod
    def _safe_app_route(route: object) -> str:
        value = str(route or "/gui")
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            return "/gui"
        if parsed.path.startswith("//"):
            return "/gui"
        suffix = f"?{parsed.query}" if parsed.query else ""
        if parsed.fragment:
            suffix += f"#{parsed.fragment}"
        return f"{parsed.path}{suffix}"

    def show_main_window(self, route: str = "/gui") -> bool:
        if self._window is None:
            return False
        target = self._safe_app_route(route)
        for method_name in ("restore", "show"):
            try:
                getattr(self._window, method_name)()
            except Exception:
                continue
        try:
            self._window.evaluate_js(
                f"window.location.assign({json.dumps(target, ensure_ascii=False)});"
            )
        except Exception as exc:
            _log(f"Could not navigate the main window from desktop pet: {exc}")
        return True

    def show_desktop_pet(self) -> bool:
        if self._pet_window is None:
            return False
        if self._pet_input_overlay is not None:
            try:
                self._pet_input_overlay.show()
                return True
            except Exception as exc:
                _log(f"Could not show desktop pet: {exc}")
                return False
        for method_name in ("restore", "show"):
            try:
                getattr(self._pet_window, method_name)()
            except Exception:
                continue
        return True

    def hide_desktop_pet(self) -> bool:
        if self._pet_window is None:
            return False
        try:
            if self._pet_input_overlay is not None:
                self._pet_input_overlay.hide()
            else:
                self._pet_window.hide()
            return True
        except Exception as exc:
            _log(f"Could not hide desktop pet: {exc}")
            return False

    @staticmethod
    def _desktop_pet_pointer_position(
        screen_x: object,
        screen_y: object,
    ) -> tuple[float, float] | None:
        try:
            return float(screen_x), float(screen_y)
        except (TypeError, ValueError):
            return None

    def begin_desktop_pet_drag(self, screen_x: object, screen_y: object) -> bool:
        if self._pet_window is None:
            return False
        pointer = self._desktop_pet_pointer_position(screen_x, screen_y)
        if pointer is None:
            return False
        try:
            if self._pet_input_overlay is not None:
                self._pet_input_overlay.restore_inside()
                window_x, window_y, _width, _height = (
                    self._pet_input_overlay.bounds()
                )
                window_position = (window_x, window_y)
            else:
                window_position = (int(self._pet_window.x), int(self._pet_window.y))
        except (AttributeError, TypeError, ValueError):
            return False
        with self._drag_lock:
            self._desktop_pet_drag = (*pointer, *window_position)
        return True

    def move_desktop_pet_drag(self, screen_x: object, screen_y: object) -> bool:
        if self._pet_window is None:
            return False
        pointer = self._desktop_pet_pointer_position(screen_x, screen_y)
        if pointer is None:
            return False
        with self._drag_lock:
            drag = self._desktop_pet_drag
        if drag is None:
            return False
        pointer_x, pointer_y, window_x, window_y = drag
        target_x = window_x + int(round(pointer[0] - pointer_x))
        target_y = window_y + int(round(pointer[1] - pointer_y))
        try:
            if self._pet_input_overlay is not None:
                self._pet_input_overlay.move(target_x, target_y)
            else:
                self._pet_window.move(target_x, target_y)
            return True
        except Exception as exc:
            _log(f"Could not move desktop pet: {exc}")
            return False

    def end_desktop_pet_drag(self) -> bool:
        with self._drag_lock:
            was_dragging = self._desktop_pet_drag is not None
            self._desktop_pet_drag = None
        return was_dragging

    def get_desktop_pet_window_context(self) -> dict[str, int] | None:
        if self._pet_window is None or self._pet_input_overlay is None:
            return None
        try:
            return self._pet_input_overlay.window_context()
        except Exception as exc:
            _log(f"Could not read desktop-pet window context: {exc}")
            return None

    def move_desktop_pet_to_edge(self, side: object, compact: object) -> dict[str, int] | None:
        if side not in ("left", "right") or self._pet_input_overlay is None:
            return None
        if not _valid_desktop_pet_compact_request(compact):
            return None
        with self._drag_lock:
            if self._desktop_pet_drag is not None:
                return None
        try:
            if self._pet_input_overlay.move_to_edge(side, compact):
                return self._pet_input_overlay.window_context()
        except Exception as exc:
            _log(f"Could not move desktop pet to edge: {exc}")
        return None

    def cancel_desktop_pet_edge_move(self) -> bool:
        if self._pet_input_overlay is None:
            return False
        try:
            self._pet_input_overlay.cancel_edge_move()
            return True
        except Exception as exc:
            _log(f"Could not cancel desktop pet edge movement: {exc}")
            return False

    def update_desktop_pet_hit_region(self, region: object) -> bool:
        if self._pet_input_overlay is None or not _valid_desktop_pet_hit_region(region):
            return False
        try:
            self._pet_input_overlay.update_hit_region(region)
            return True
        except Exception as exc:
            _log(f"Could not update desktop-pet input region: {exc}")
            return False

    def apply_desktop_pet_preferences(self, preferences: object, notify_pet: object = True,
                                     resize_anchor: object = None) -> bool:
        if self._pet_window is None or not isinstance(preferences, dict) or not isinstance(notify_pet, bool):
            return False
        if resize_anchor is not None and (not isinstance(resize_anchor, dict)
                or set(resize_anchor) != {"x", "y"}
                or resize_anchor.get("x") not in ("left", "right")
                or resize_anchor.get("y") not in ("top", "bottom")):
            return False
        try:
            from app.services.application_settings import normalize_desktop_pet_settings

            normalized = normalize_desktop_pet_settings(preferences)
            new_width, new_height = _desktop_pet_window_size(normalized)
            preference_script = (
                "window.dispatchEvent(new CustomEvent('siming:desktop-pet-preferences',"
                f"{{detail:{json.dumps(normalized, ensure_ascii=False)}}}));"
            ) if notify_pet else ""
            if self._pet_input_overlay is not None:
                self._pet_input_overlay.apply_preferences(
                    new_width,
                    new_height,
                    normalized["desktop_pet_on_top"],
                    preference_script,
                    resize_anchor,
                )
            else:
                try:
                    old_width = int(self._pet_window.width)
                    old_height = int(self._pet_window.height)
                    old_x = int(self._pet_window.x)
                    old_y = int(self._pet_window.y)
                except Exception:
                    old_width, old_height = new_width, new_height
                    old_x = old_y = None
                self._pet_window.resize(new_width, new_height)
                if old_x is not None and old_y is not None:
                    self._pet_window.move(
                        old_x if resize_anchor and resize_anchor["x"] == "left" else old_x + old_width - new_width,
                        old_y if resize_anchor and resize_anchor["y"] == "top" else old_y + old_height - new_height,
                    )
                self._pet_window.on_top = normalized["desktop_pet_on_top"]
                if preference_script:
                    self._pet_window.evaluate_js(preference_script)
            return True
        except Exception as exc:
            _log(f"Could not apply desktop-pet preferences: {exc}")
            return False

    def _remember_desktop_pet_position(self, x: object, y: object) -> None:
        try:
            position = (int(round(float(x))), int(round(float(y))))
        except (TypeError, ValueError):
            return
        with self._position_lock:
            self._pending_position = position
            if self._position_timer is not None:
                self._position_timer.cancel()
            timer = threading.Timer(0.45, self._flush_desktop_pet_position)
            timer.daemon = True
            self._position_timer = timer
            timer.start()

    def _flush_desktop_pet_position(self) -> None:
        with self._position_lock:
            position = self._pending_position
            self._pending_position = None
            self._position_timer = None
        if position is None:
            return
        try:
            from app.services.application_settings import update_launcher_preferences

            update_launcher_preferences(
                {"desktop_pet_position": {"x": position[0], "y": position[1]}}
            )
        except Exception as exc:
            _log(f"Could not remember desktop-pet position: {exc}")

    def _close(self) -> None:
        self.end_desktop_pet_drag()
        if self._pet_input_overlay is not None:
            self._pet_input_overlay.close()
            self._pet_input_overlay = None
        with self._position_lock:
            timer = self._position_timer
            self._position_timer = None
        if timer is not None:
            timer.cancel()
        self._flush_desktop_pet_position()


def _valid_desktop_pet_hit_region(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    width, height, rects = value.get("viewport_width"), value.get("viewport_height"), value.get("rects")
    def finite(number):
        return isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number)
    if not all(finite(size) and 1 <= size <= 4096 for size in (width, height)):
        return False
    if not isinstance(rects, list) or not 1 <= len(rects) <= 2048:
        return False
    for rect in rects:
        if not isinstance(rect, (list, tuple)) or len(rect) != 4 or not all(finite(n) for n in rect):
            return False
        x, y, w, h = rect
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width + 0.01 or y + h > height + 0.01:
            return False
    return True


def _valid_desktop_pet_compact_request(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    keys = ("width", "height", "viewport_width", "viewport_height")
    if any(not isinstance(value.get(key), (int, float))
           or isinstance(value[key], bool) or not math.isfinite(value[key]) for key in keys):
        return False
    return (64 <= value["width"] <= value["viewport_width"] <= 4096
            and 64 <= value["height"] <= value["viewport_height"] <= 4096)


def _desktop_pet_window_size(preferences: dict) -> tuple[int, int]:
    scale = float(preferences.get("desktop_pet_scale", 0.8))
    return (
        max(DESKTOP_PET_MIN_WIDTH, round(DESKTOP_PET_BASE_WIDTH * scale)),
        max(DESKTOP_PET_MIN_HEIGHT, round(DESKTOP_PET_BASE_HEIGHT * scale)),
    )


def _desktop_pet_window_position(
    settings: dict,
    width: int,
    height: int,
) -> tuple[int | None, int | None]:
    if os.name != "nt":
        return None, None

    user32 = ctypes.windll.user32
    virtual_left = int(user32.GetSystemMetrics(76))
    virtual_top = int(user32.GetSystemMetrics(77))
    virtual_width = max(width, int(user32.GetSystemMetrics(78)))
    virtual_height = max(height, int(user32.GetSystemMetrics(79)))
    max_x = virtual_left + virtual_width - width
    max_y = virtual_top + virtual_height - height

    saved = settings.get("desktop_pet_position")
    if (
        isinstance(saved, dict)
        and isinstance(saved.get("x"), int)
        and isinstance(saved.get("y"), int)
    ):
        return (
            min(max_x, max(virtual_left, saved["x"])),
            min(max_y, max(virtual_top, saved["y"])),
        )

    class WorkArea(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    work_area = WorkArea()
    if user32.SystemParametersInfoW(48, 0, ctypes.byref(work_area), 0):
        return (
            max(work_area.left, work_area.right - width - DESKTOP_PET_EDGE_MARGIN),
            max(work_area.top, work_area.bottom - height - DESKTOP_PET_EDGE_MARGIN),
        )
    return (
        max(virtual_left, max_x - DESKTOP_PET_EDGE_MARGIN),
        max(virtual_top, max_y - DESKTOP_PET_EDGE_MARGIN),
    )


def _launcher_log_path() -> Path:
    try:
        home = _app_home()
        (home / "logs").mkdir(parents=True, exist_ok=True)
        return home / "logs" / "launcher.log"
    except Exception:
        return Path(tempfile.gettempdir()) / "siming-launcher.log"


def _log(message: str) -> None:
    from datetime import datetime
    try:
        path = _launcher_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        pass


def _set_windows_pet_display_passthrough(native) -> None:
    """Keep WebView2 unshaped; the separate input window owns hit testing.

    WS_EX_TRANSPARENT on a layered HWND passes pointer input to windows below
    it. A Region on the WebView2 host instead breaks its color-key composition
    and paints opaque black blocks around otherwise transparent content.
    """
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pointer_sized = ctypes.sizeof(ctypes.c_void_p) == 8
    get_style = user32.GetWindowLongPtrW if pointer_sized else user32.GetWindowLongW
    set_style = user32.SetWindowLongPtrW if pointer_sized else user32.SetWindowLongW
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_style.restype = ctypes.c_ssize_t
    hwnd = int(native.Handle.ToInt64())
    ctypes.set_last_error(0)
    style = get_style(hwnd, -20)  # GWL_EXSTYLE
    if not style and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    if not style & 0x00080000:  # WS_EX_LAYERED is required for cross-thread pass-through.
        raise RuntimeError("Desktop-pet display window is not layered")
    if not style & 0x00000020:  # WS_EX_TRANSPARENT
        ctypes.set_last_error(0)
        previous = set_style(hwnd, -20, style | 0x00000020)
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())


class _WindowsDesktopPetInputOverlay:
    """Receive input above a color-keyed WebView2 and forward it to Chromium."""

    def __init__(self, pet_window) -> None:
        _log("Initializing desktop-pet input overlay")
        import clr

        clr.AddReference("System.Windows.Forms")
        import System.Windows.Forms as WinForms
        from System import Action
        from System.Drawing import Color, Rectangle, Size, Region

        native = getattr(pet_window, "native", None)
        if native is None:
            raise RuntimeError("Desktop-pet native window is unavailable")

        self._native = native
        self._WinForms = WinForms
        self._Action = Action
        self._Color = Color
        self._Rectangle = Rectangle
        self._Size = Size
        self._Region = Region
        self._browser = None
        self._form = None
        self._handlers: list[object] = []
        self._dispatch_error_logged = False
        self._edge_motion = None
        self._expanded_geometry = None
        self._hit_region_request = None
        self._hit_region_size = None

        # Build the WinForms layer on its UI thread. CoreWebView2 is resolved
        # lazily by the mouse handlers on this same thread; reading it while
        # the page-load callback is still unwinding can deadlock WebView2.
        self._invoke_native(self._initialize_on_ui_thread)
        _log("Desktop-pet input overlay native layer initialized")
        if self._form is None or self._browser is None:
            raise RuntimeError("Desktop-pet input overlay did not initialize")

    def _invoke_control(self, control, callback, *, operation: str) -> None:
        if not bool(getattr(control, "InvokeRequired", False)):
            callback()
            return

        # WinForms.Control.Invoke is synchronous. Calling it from pywebview's
        # boot thread can keep the Python runtime occupied while the UI thread
        # is trying to enter a Python callback, intermittently freezing both
        # WebView2 and the local API server. BeginInvoke lets the current native
        # event unwind; Event.wait releases Python for the queued UI callback.
        completed = threading.Event()
        errors: list[BaseException] = []

        def run_on_ui_thread() -> None:
            try:
                callback()
            except BaseException as exc:
                errors.append(exc)
            finally:
                completed.set()

        control.BeginInvoke(self._Action(run_on_ui_thread))
        if not completed.wait(timeout=5.0):
            raise RuntimeError(f"Timed out while {operation} on the UI thread")
        if errors:
            raise errors[0]

    def _invoke_native(self, callback) -> None:
        self._invoke_control(
            self._native,
            callback,
            operation="initializing the desktop-pet input overlay",
        )

    def _invoke_form(self, callback) -> None:
        form = self._form
        if form is None or bool(getattr(form, "IsDisposed", False)):
            return
        self._invoke_control(
            form,
            callback,
            operation="updating the desktop-pet input overlay",
        )

    def _initialize_on_ui_thread(self) -> None:
        WinForms = self._WinForms
        native = self._native
        browser = getattr(getattr(native, "browser", None), "webview", None)
        if browser is None:
            raise RuntimeError("Desktop-pet WebView2 surface is not ready")

        native.AllowTransparency = True
        native.BackColor = self._Color.Magenta
        native.TransparencyKey = self._Color.Magenta
        browser.DefaultBackgroundColor = self._Color.Transparent
        _set_windows_pet_display_passthrough(native)

        form = WinForms.Form()
        form.Text = "司命桌宠输入层"
        form.FormBorderStyle = getattr(WinForms.FormBorderStyle, "None")
        form.StartPosition = WinForms.FormStartPosition.Manual
        form.Bounds = native.Bounds
        form.ShowInTaskbar = False
        form.TopMost = bool(native.TopMost)
        form.BackColor = self._Color.Black
        # A one-alpha layered form is visually imperceptible but still receives
        # Windows hit testing. The color-keyed WebView below remains fully clear.
        form.Opacity = 1.0 / 255.0

        self._browser = browser
        self._form = form

        handlers = (
            self._on_mouse_down,
            self._on_mouse_move,
            self._on_mouse_up,
            self._on_mouse_leave,
            self._on_mouse_wheel,
            self._on_native_bounds_changed,
            self._on_native_closed,
            self._on_native_handle_created,
        )
        self._handlers.extend(handlers)
        form.MouseDown += handlers[0]
        form.MouseMove += handlers[1]
        form.MouseUp += handlers[2]
        form.MouseLeave += handlers[3]
        form.MouseWheel += handlers[4]
        native.LocationChanged += handlers[5]
        native.SizeChanged += handlers[5]
        native.FormClosed += handlers[6]
        native.HandleCreated += handlers[7]

        native.Show()
        form.Show(native)
        form.BringToFront()

    def _on_native_handle_created(self, _sender, _event) -> None:
        # WinForms can replace its HWND; pointer transparency belongs to that
        # handle, not to the managed Form, and must be restored on recreation.
        _set_windows_pet_display_passthrough(self._native)

    def _button(self, event) -> str:
        buttons = self._WinForms.MouseButtons
        if event.Button == buttons.Right:
            return "right"
        if event.Button == buttons.Middle:
            return "middle"
        return "left"

    def _buttons(self, event) -> int:
        pressed = int(event.Button)
        buttons = self._WinForms.MouseButtons
        return (
            (1 if pressed & int(buttons.Left) else 0)
            | (2 if pressed & int(buttons.Right) else 0)
            | (4 if pressed & int(buttons.Middle) else 0)
        )

    def _dispatch(self, event_type: str, event, **extra: object) -> object | None:
        try:
            screen = self._form.PointToScreen(event.Location)
            payload: dict[str, object] = {
                "type": event_type,
                "x": int(event.X),
                "y": int(event.Y),
                "screenX": int(screen.X),
                "screenY": int(screen.Y),
                **extra,
            }
            return self._browser.CoreWebView2.CallDevToolsProtocolMethodAsync(
                "Input.dispatchMouseEvent",
                json.dumps(payload, separators=(",", ":")),
            )
        except Exception:
            if not self._dispatch_error_logged:
                self._dispatch_error_logged = True
                _log(
                    "Desktop-pet input forwarding failed:\n"
                    + traceback.format_exc()
                )
            return None

    def _on_mouse_down(self, _sender, event) -> None:
        # Stop on the native UI thread before WebView2 forwards the press, so
        # the input target cannot keep sliding while the bridge catches up.
        self._finish_edge_move_on_ui_thread(False)
        # Retain OS-level capture while a drag crosses gaps in the silhouette.
        self._form.Capture = True
        self._dispatch(
            "mousePressed",
            event,
            button=self._button(event),
            buttons=self._buttons(event),
            clickCount=max(int(event.Clicks), 1),
        )

    def _on_mouse_move(self, _sender, event) -> None:
        self._dispatch(
            "mouseMoved",
            event,
            button="none",
            buttons=self._buttons(event),
        )

    def _on_mouse_up(self, _sender, event) -> None:
        self._dispatch(
            "mouseReleased",
            event,
            button=self._button(event),
            buttons=0,
            clickCount=max(int(event.Clicks), 1),
        )
        self._form.Capture = False

    def _on_mouse_leave(self, _sender, _event) -> None:
        try:
            self._browser.CoreWebView2.CallDevToolsProtocolMethodAsync(
                "Input.dispatchMouseEvent",
                '{"type":"mouseMoved","x":-1,"y":-1,"button":"none","buttons":0}',
            )
        except Exception:
            pass

    def _on_mouse_wheel(self, _sender, event) -> None:
        self._dispatch(
            "mouseWheel",
            event,
            button="none",
            buttons=self._buttons(event),
            deltaX=0,
            deltaY=-int(event.Delta),
        )

    def _on_native_bounds_changed(self, _sender, _event) -> None:
        self.sync()

    def _on_native_closed(self, _sender, _event) -> None:
        self.close()

    def bounds(self) -> tuple[int, int, int, int]:
        captured: list[tuple[int, int, int, int]] = []

        def capture_on_ui_thread() -> None:
            bounds = self._native.Bounds
            captured.append(
                (
                    int(bounds.X),
                    int(bounds.Y),
                    int(bounds.Width),
                    int(bounds.Height),
                )
            )

        self._invoke_native(capture_on_ui_thread)
        if not captured:
            raise RuntimeError("Desktop-pet native bounds are unavailable")
        return captured[0]

    def window_context(self) -> dict[str, int]:
        captured: list[dict[str, int]] = []

        def capture_on_ui_thread() -> None:
            bounds = self._native.Bounds
            work = self._WinForms.Screen.FromControl(self._native).WorkingArea
            captured.append({
                "x": int(bounds.X),
                "y": int(bounds.Y),
                "width": int(bounds.Width),
                "height": int(bounds.Height),
                "work_x": int(work.X),
                "work_y": int(work.Y),
                "work_width": int(work.Width),
                "work_height": int(work.Height),
                "compact": self._expanded_geometry is not None,
            })

        self._invoke_native(capture_on_ui_thread)
        if not captured:
            raise RuntimeError("Desktop-pet screen context is unavailable")
        return captured[0]

    def restore_inside(self) -> None:
        def restore_on_ui_thread() -> None:
            self._finish_edge_move_on_ui_thread(False)
            self._restore_size_on_ui_thread()
            bounds = self._native.Bounds
            work = self._WinForms.Screen.FromControl(self._native).WorkingArea
            width = int(bounds.Width)
            height = int(bounds.Height)
            target_x = min(
                max(int(bounds.X), int(work.X)),
                int(work.X + work.Width - width),
            )
            target_y = min(
                max(int(bounds.Y), int(work.Y)),
                int(work.Y + work.Height - height),
            )
            if target_x == int(bounds.X) and target_y == int(bounds.Y):
                return
            self._native.SetBounds(target_x, target_y, width, height)
            self._form.Bounds = self._native.Bounds

        self._invoke_native(restore_on_ui_thread)

    def _restore_size_on_ui_thread(self) -> None:
        saved = self._expanded_geometry
        if saved is None:
            return
        width, height, side, minimum = saved
        bounds = self._native.Bounds
        work = self._WinForms.Screen.FromControl(self._native).WorkingArea
        x = int(bounds.X) if side == "left" else int(bounds.X + bounds.Width) - width
        x = min(max(x, int(work.X)), int(work.X + work.Width) - width)
        y = min(max(int(bounds.Y), int(work.Y)), int(work.Y + work.Height) - height)
        self._native.SetBounds(x, y, width, height)
        self._native.MinimumSize = minimum
        self._form.Bounds = self._native.Bounds
        self._expanded_geometry = None

    def _compact_on_ui_thread(self, side: str, width: int, height: int) -> None:
        bounds = self._native.Bounds
        work = self._WinForms.Screen.FromControl(self._native).WorkingArea
        target = int(work.X) if side == "left" else int(work.X + work.Width - bounds.Width)
        if abs(int(bounds.X) - target) > 1:
            raise RuntimeError("Cannot compact a desktop pet away from the monitor edge")
        if self._expanded_geometry is not None:
            raise RuntimeError("Desktop pet is already compact")
        self._expanded_geometry = (int(bounds.Width), int(bounds.Height), side, self._native.MinimumSize)
        # The normal user-scale minimum must not stop an intentional compact
        # presentation. Settings and normal drag restore that original minimum.
        try:
            self._native.MinimumSize = self._Size(1, 1)
            x = int(work.X) if side == "left" else int(work.X + work.Width) - width
            self._native.SetBounds(x, int(bounds.Y), width, height)
            self._form.Bounds = self._native.Bounds
        except Exception:
            self._restore_size_on_ui_thread()
            raise

    def move(self, x: object, y: object) -> None:
        target_x = int(round(float(x)))
        target_y = int(round(float(y)))

        def move_on_ui_thread() -> None:
            self._finish_edge_move_on_ui_thread(False)
            self._restore_size_on_ui_thread()
            bounds = self._native.Bounds
            width = int(bounds.Width)
            height = int(bounds.Height)
            target_screen = self._WinForms.Screen.FromRectangle(
                self._Rectangle(target_x, target_y, width, height)
            )
            work = target_screen.WorkingArea
            clamped_x = min(
                max(target_x, int(work.X)),
                int(work.X + work.Width - width),
            )
            clamped_y = min(
                max(target_y, int(work.Y)),
                int(work.Y + work.Height - height),
            )
            self._native.SetBounds(
                clamped_x,
                clamped_y,
                width,
                height,
            )
            self._form.Bounds = self._native.Bounds

        self._invoke_native(move_on_ui_thread)

    def _finish_edge_move_on_ui_thread(self, success: bool) -> None:
        motion = self._edge_motion
        if motion is None:
            return
        self._edge_motion = None
        timer, finished, result, compact = motion
        timer.Stop()
        timer.Dispose()
        if success:
            try:
                self._compact_on_ui_thread(*compact)
            except Exception as exc:
                _log(f"Desktop-pet compact layout failed: {exc}")
                self._restore_size_on_ui_thread()
                success = False
        result.append(success)
        finished.set()

    def cancel_edge_move(self) -> None:
        def cancel_on_ui_thread() -> None:
            self._finish_edge_move_on_ui_thread(False)
            self._restore_size_on_ui_thread()
        self._invoke_native(cancel_on_ui_thread)

    def _start_edge_move_on_ui_thread(self, side: str, compact: dict, finished, result: list[bool]) -> None:
        self._finish_edge_move_on_ui_thread(False)
        self._restore_size_on_ui_thread()
        bounds = self._native.Bounds
        work = self._WinForms.Screen.FromControl(self._native).WorkingArea
        width, height = int(bounds.Width), int(bounds.Height)
        # Request dimensions are CSS pixels; native bounds can be device pixels.
        compact_width = min(width, max(64, math.ceil(width * compact["width"] / compact["viewport_width"])))
        compact_height = min(height, max(64, math.ceil(height * compact["height"] / compact["viewport_height"])))
        # Both native windows stay wholly in this monitor. Only the rendered
        # character leans beyond its edge; neighbouring monitors cannot expose it.
        if width > int(work.Width) or height > int(work.Height):
            result.append(False)
            finished.set()
            return
        start_x = min(max(int(bounds.X), int(work.X)), int(work.X + work.Width - width))
        target_x = int(work.X) if side == "left" else int(work.X + work.Width - width)
        target_y = min(max(int(bounds.Y), int(work.Y)), int(work.Y + work.Height - height))
        distance = abs(target_x - start_x)
        if distance <= 1:
            self._native.SetBounds(target_x, target_y, width, height)
            self._form.Bounds = self._native.Bounds
            self._compact_on_ui_thread(side, compact_width, compact_height)
            result.append(True)
            finished.set()
            return
        duration = min(1.1, max(0.3, distance / 1500))
        started = time.monotonic()
        timer = self._WinForms.Timer()
        timer.Interval = 16
        motion = (timer, finished, result, (side, compact_width, compact_height))
        self._edge_motion = motion

        def tick(_sender, _event) -> None:
            if self._edge_motion is not motion:
                return
            try:
                progress = min(1.0, (time.monotonic() - started) / duration)
                eased = progress * progress * (3 - 2 * progress)
                x = round(start_x + (target_x - start_x) * eased)
                self._native.SetBounds(x, target_y, width, height)
                self._form.Bounds = self._native.Bounds
                if progress >= 1:
                    self._finish_edge_move_on_ui_thread(True)
            except Exception as exc:
                _log(f"Desktop-pet edge animation failed: {exc}")
                self._finish_edge_move_on_ui_thread(False)

        timer.Tick += tick
        timer.Start()

    def move_to_edge(self, side: str, compact: dict) -> bool:
        if side not in ("left", "right") or not _valid_desktop_pet_compact_request(compact):
            return False
        finished = threading.Event()
        result: list[bool] = []
        self._invoke_native(lambda: self._start_edge_move_on_ui_thread(side, compact, finished, result))
        # Bridge calls run outside the UI thread. The WinForms timer continues
        # painting and receiving input while this wait releases the Python GIL.
        if not finished.wait(timeout=2.0):
            def cancel_if_current() -> None:
                if self._edge_motion is not None and self._edge_motion[1] is finished:
                    self._finish_edge_move_on_ui_thread(False)
            self._invoke_native(cancel_if_current)
        return bool(result and result[0])

    def apply_preferences(
        self,
        width: object,
        height: object,
        on_top: object,
        preference_script: str,
        resize_anchor: dict | None = None,
    ) -> None:
        new_width = int(round(float(width)))
        new_height = int(round(float(height)))
        keep_on_top = bool(on_top)

        def apply_on_ui_thread() -> None:
            self._finish_edge_move_on_ui_thread(False)
            self._restore_size_on_ui_thread()
            bounds = self._native.Bounds
            work = self._WinForms.Screen.FromControl(self._native).WorkingArea
            target_x = min(
                max(
                    int(bounds.X) if resize_anchor and resize_anchor["x"] == "left"
                    else int(bounds.X) + int(bounds.Width) - new_width,
                    int(work.X),
                ),
                int(work.X + work.Width - new_width),
            )
            target_y = min(
                max(
                    int(bounds.Y) if resize_anchor and resize_anchor["y"] == "top"
                    else int(bounds.Y) + int(bounds.Height) - new_height,
                    int(work.Y),
                ),
                int(work.Y + work.Height - new_height),
            )
            if (int(bounds.X), int(bounds.Y), int(bounds.Width), int(bounds.Height)) != (
                target_x, target_y, new_width, new_height,
            ):
                self._native.SetBounds(target_x, target_y, new_width, new_height)
            if bool(self._native.TopMost) != keep_on_top:
                self._native.TopMost = keep_on_top
            self._form.Bounds = self._native.Bounds
            if bool(self._form.TopMost) != keep_on_top:
                self._form.TopMost = keep_on_top
                if self._form.Visible:
                    self._form.BringToFront()
            core = getattr(self._browser, "CoreWebView2", None)
            if core is not None and preference_script:
                core.ExecuteScriptAsync(preference_script)

        self._invoke_native(apply_on_ui_thread)

    def _apply_hit_region_on_ui_thread(self) -> None:
        request = self._hit_region_request
        if request is None:
            return
        bounds = self._native.Bounds
        size = (int(bounds.Width), int(bounds.Height))
        if size == self._hit_region_size:
            return
        sx, sy = size[0] / request["viewport_width"], size[1] / request["viewport_height"]
        region = self._Region()
        region.MakeEmpty()
        for x, y, width, height in request["rects"]:
            left, top = math.floor(x * sx), math.floor(y * sy)
            right, bottom = math.ceil((x + width) * sx), math.ceil((y + height) * sy)
            region.Union(self._Rectangle(left, top, right - left, bottom - top))
        # Only the one-alpha input HWND is shaped. The WebView display stays
        # rectangular and mouse-transparent, preserving its alpha composition.
        old_input = self._form.Region
        self._form.Region = region
        if old_input is not None:
            old_input.Dispose()
        self._hit_region_size = size

    def update_hit_region(self, request: dict) -> None:
        if not _valid_desktop_pet_hit_region(request):
            raise ValueError("Invalid desktop-pet input region")
        def apply_on_ui_thread() -> None:
            self._hit_region_request = request
            self._hit_region_size = None
            self._apply_hit_region_on_ui_thread()
        self._invoke_native(apply_on_ui_thread)

    def sync(self) -> None:
        def sync_on_ui_thread() -> None:
            self._form.Bounds = self._native.Bounds
            self._apply_hit_region_on_ui_thread()
        self._invoke_native(sync_on_ui_thread)

    def set_on_top(self, on_top: object) -> None:
        keep_on_top = bool(on_top)

        def set_on_ui_thread() -> None:
            self._native.TopMost = keep_on_top
            self._form.TopMost = keep_on_top
            if self._form.Visible:
                self._form.BringToFront()

        self._invoke_native(set_on_ui_thread)

    def show(self) -> None:
        def show_on_ui_thread() -> None:
            if not self._native.Visible:
                self._native.Show()
            _set_windows_pet_display_passthrough(self._native)
            if not self._form.Visible:
                self._form.Show(self._native)
            self._form.Bounds = self._native.Bounds
            self._form.TopMost = bool(self._native.TopMost)
            self._form.BringToFront()

        self._invoke_native(show_on_ui_thread)

    def hide(self) -> None:
        def hide_on_ui_thread() -> None:
            self._finish_edge_move_on_ui_thread(False)
            self._form.Hide()
            self._native.Hide()

        self._invoke_native(hide_on_ui_thread)

    def close(self) -> None:
        form = self._form
        if form is None:
            return
        def close_on_ui_thread() -> None:
            self._finish_edge_move_on_ui_thread(False)
            if not form.IsDisposed:
                form.Close()

        self._invoke_form(close_on_ui_thread)
        self._form = None


def _install_windows_desktop_pet_input_overlay(pet_window):
    if os.name != "nt":
        return None
    try:
        return _WindowsDesktopPetInputOverlay(pet_window)
    except Exception:
        _log(
            "Could not install desktop-pet input overlay:\n"
            + traceback.format_exc()
        )
        return None


def _show_error(title: str, message: str) -> None:
    _log(f"{title}: {message}")
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        pass


def _close_failed_desktop_window(window, *, status_text: str, message: str) -> None:
    """Report an unrecoverable desktop boot error and release the singleton."""
    try:
        encoded_status = json.dumps(status_text, ensure_ascii=False)
        window.evaluate_js(
            "const status=document.getElementById('status');"
            f"if(status){{status.textContent={encoded_status};"
            "status.style.color='#b84233';}"
            "const dots=document.getElementById('dots');"
            "if(dots){dots.style.display='none';}"
        )
    except Exception as exc:
        _log(f"Could not render startup failure in WebView: {exc}")

    _show_error(f"{APP_NAME} 启动失败", message)
    try:
        window.destroy()
    except Exception as exc:
        _log(f"Could not close failed desktop window: {exc}")


def _safe_print(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if stream is None:
        _log(message)
        return
    try:
        print(message, file=stream)
    except Exception:
        _log(message)


def _redirect_missing_stdio_to_log() -> None:
    path = _launcher_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        stdout_log = path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = stdout_log
        _STDIO_LOG_HANDLES.append(stdout_log)
    if sys.stderr is None:
        stderr_log = path.open("a", encoding="utf-8", buffering=1)
        sys.stderr = stderr_log
        _STDIO_LOG_HANDLES.append(stderr_log)


def _configure_stdio_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _open_inherited_windows_stream(std_handle: int, mode: str):
    """Rebuild a Python text stream from an inherited Windows stdio pipe.

    PyInstaller's windowed bootloader deliberately does not initialize Python's
    standard streams. MCP clients still start the same executable with real
    stdin/stdout pipes, so recover those OS handles before serving JSON-RPC.
    The handle is duplicated because replacing ``sys.stdout`` must not let the
    discarded PyInstaller wrapper close the pipe behind the new stream.
    """

    import msvcrt

    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.argtypes = [ctypes.c_ulong]
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.DuplicateHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    kernel32.DuplicateHandle.restype = ctypes.c_int

    source = kernel32.GetStdHandle(ctypes.c_ulong(std_handle).value)
    invalid_handle = ctypes.c_void_p(-1).value
    if source in (None, 0, invalid_handle):
        raise RuntimeError(f"MCP stdio handle {std_handle} is unavailable")

    process = kernel32.GetCurrentProcess()
    duplicate = ctypes.c_void_p()
    duplicate_same_access = 0x00000002
    if not kernel32.DuplicateHandle(
        process,
        source,
        process,
        ctypes.byref(duplicate),
        0,
        True,
        duplicate_same_access,
    ):
        raise ctypes.WinError()

    flags = os.O_RDONLY if "r" in mode else os.O_WRONLY
    try:
        fd = msvcrt.open_osfhandle(int(duplicate.value), flags)
    except Exception:
        kernel32.CloseHandle(duplicate)
        raise
    return os.fdopen(
        fd,
        mode,
        buffering=1,
        encoding="utf-8",
        errors="replace",
        newline="",
    )


def _ensure_mcp_stdio() -> None:
    """Make the packaged windowed executable usable as an stdio MCP server."""

    if os.name != "nt":
        if sys.stdin is None or sys.stdout is None:
            raise RuntimeError("MCP requires inherited stdin and stdout pipes")
        _configure_stdio_utf8()
        return

    stdin = _open_inherited_windows_stream(-10, "r")
    stdout = _open_inherited_windows_stream(-11, "w")
    try:
        stderr = _open_inherited_windows_stream(-12, "w")
    except (OSError, RuntimeError):
        stderr = None

    # Keep explicit references for the lifetime of the process. They own
    # duplicated OS handles and must remain open until the MCP client exits.
    _MCP_STDIO_HANDLES.extend(stream for stream in (stdin, stdout, stderr) if stream is not None)
    sys.stdin = stdin
    sys.stdout = stdout
    if stderr is not None:
        sys.stderr = stderr
    _configure_stdio_utf8()


def _app_home() -> Path:
    env_home = os.environ.get("SIMING_HOME") or os.environ.get("MOSHU_HOME") or os.environ.get("NOVEL_AGENT_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home()
    current = base / APP_NAME
    legacy_dirs = [base / name for name in LEGACY_APP_NAMES]
    legacy_dirs.extend(Path.home() / f".{name}" for name in LEGACY_APP_NAMES)
    for legacy_dir in legacy_dirs:
        if not legacy_dir.exists():
            continue
        legacy_db = legacy_dir / "novel_agent.db"
        current_db = current / "novel_agent.db"
        if legacy_db.exists() and legacy_db.stat().st_size > 0:
            if not current_db.exists() or current_db.stat().st_size < legacy_db.stat().st_size:
                return legacy_dir
    return current


def _launcher_settings_path(home: Path) -> Path:
    return home / "launcher-settings.json"


def _load_launcher_settings(home: Path) -> dict:
    path = _launcher_settings_path(home)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_launcher_settings(home: Path, settings: dict) -> None:
    path = _launcher_settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_launch_mode(value: object) -> str:
    return "browser" if str(value or "").strip().lower() == "browser" else "desktop"


def _saved_launch_mode(home: Path) -> str:
    return _normalize_launch_mode(_load_launcher_settings(home).get("launch_mode"))


def _use_browser_mode(home: Path, *, force_browser: bool = False, force_desktop: bool = False) -> bool:
    """Resolve one explicit launch mode before importing the WebView runtime."""
    return force_browser or (not force_desktop and _saved_launch_mode(home) == "browser")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower()


def _wait_for_process_exit(pid: int, timeout: float = 60.0) -> bool:
    """Wait for the old executable before replacing it from the new one."""
    if pid <= 0 or pid == os.getpid():
        return True
    if os.name == "nt":
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            try:
                result = ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
                return result == 0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.25)
    return False


def _apply_staged_update() -> None:
    """Replace the old binary from a previously verified staged executable."""
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--apply-staged-update", action="store_true")
    parser.add_argument("--update-target", required=True)
    parser.add_argument("--wait-pid", required=True, type=int)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--update-metadata")
    args, _ = parser.parse_known_args()
    update_exe = Path(sys.executable).resolve()
    target_exe = Path(args.update_target).expanduser().resolve()
    if update_exe == target_exe:
        raise RuntimeError("The staged update cannot replace itself.")
    if not update_exe.is_file():
        raise RuntimeError("The staged update executable no longer exists.")
    expected_sha256 = str(args.expected_sha256 or "").strip().lower()
    if len(expected_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_sha256):
        raise RuntimeError("The staged update does not include a valid SHA-256 checksum.")
    if _sha256_file(update_exe) != expected_sha256:
        raise RuntimeError("The staged update no longer matches its verified SHA-256 checksum.")
    if not _wait_for_process_exit(args.wait_pid):
        raise RuntimeError("Timed out waiting for the previous Siming process to close.")

    replacement = target_exe.with_name(f"{target_exe.name}.updating")
    replacement.unlink(missing_ok=True)
    try:
        shutil.copy2(update_exe, replacement)
        os.replace(replacement, target_exe)
        if args.update_metadata:
            Path(args.update_metadata).expanduser().unlink(missing_ok=True)
        subprocess.Popen(
            [str(target_exe)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(target_exe.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        replacement.unlink(missing_ok=True)


def _pick_content_root(home: Path) -> Path | None:
    if (
        "--mcp-server" in sys.argv
        or os.environ.get("MOSHU_NO_GUI_FOLDER_PICKER")
        or "pytest" in sys.modules
    ):
        return None
    try:
        import tkinter
        from tkinter import filedialog, messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Siming 2.5",
            "请选择一个空文件夹作为小说文件镜像目录。\n数据库仍是权威数据源，旧数据会导出为可读镜像，方便 Claude/Codex 读取。",
        )
        while True:
            selected = filedialog.askdirectory(title="选择 Siming 小说数据目录")
            if not selected:
                root.destroy()
                return None
            path = Path(selected).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
            existing = [item for item in path.iterdir() if item.name not in {".DS_Store", "Thumbs.db"}]
            if not existing:
                root.destroy()
                return path
            messagebox.showwarning(
                "Siming 2.5",
                "请选择空目录，避免和已有文件混在一起。\n\n可以新建一个空文件夹后再选择。",
            )
    except Exception as exc:
        _log(f"content root picker skipped: {exc}")
        return None


def _find_free_port(start: int = DEFAULT_PORT, attempts: int = 50) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"Could not find a free local port from {start} to {start + attempts - 1}.")


def _prepare_data_environment() -> Path:
    home = _app_home()
    home.mkdir(parents=True, exist_ok=True)
    launcher_settings = _load_launcher_settings(home)
    content_root = (
        os.environ.get("SIMING_CONTENT_ROOT")
        or os.environ.get("MOSHU_CONTENT_ROOT")
        or launcher_settings.get("content_root")
    )
    if not content_root:
        content_root = str(home / "projects")
    os.environ.setdefault("SIMING_HOME", str(home))
    os.environ.setdefault("MOSHU_HOME", str(home))
    os.environ.setdefault("SIMING_CONTENT_ROOT", str(Path(content_root).expanduser().resolve()))
    os.environ.setdefault("MOSHU_CONTENT_ROOT", str(Path(content_root).expanduser().resolve()))
    model_root = os.environ.get("SIMING_MODEL_ROOT") or os.environ.get("MOSHU_MODEL_ROOT") or launcher_settings.get("model_root")
    if not model_root:
        model_root = str(home / "models")
    os.environ.setdefault("SIMING_MODEL_ROOT", str(Path(model_root).expanduser().resolve()))
    os.environ.setdefault("MOSHU_MODEL_ROOT", str(Path(model_root).expanduser().resolve()))
    os.environ.setdefault("SIMING_KEY_FILE", str(home / ".crypto_key"))
    os.environ.setdefault("MOSHU_KEY_FILE", str(home / ".crypto_key"))
    os.environ.setdefault("NOVEL_AGENT_HOME", str(home))
    os.environ.setdefault("NOVEL_AGENT_KEY_FILE", str(home / ".crypto_key"))
    os.environ["DATABASE_URL"] = f"sqlite:///{(home / 'novel_agent.db').as_posix()}"
    return home


def _prepare_environment(port: int) -> Path:
    home = _prepare_data_environment()
    launcher_settings = _load_launcher_settings(home)
    gateway_enabled = bool(launcher_settings.get("gateway_enabled"))
    os.environ.setdefault(
        "SIMING_RUNTIME_PROFILE",
        "gateway" if gateway_enabled else "desktop-standalone",
    )
    advertised_url = str(launcher_settings.get("gateway_advertised_url") or "").strip()
    if advertised_url:
        os.environ.setdefault("SIMING_GATEWAY_ADVERTISED_URL", advertised_url)
    allowed_hosts = str(launcher_settings.get("gateway_allowed_hosts") or "").strip()
    if allowed_hosts:
        os.environ.setdefault("SIMING_GATEWAY_ALLOWED_HOSTS", allowed_hosts)
    os.environ["CORS_ORIGINS"] = ",".join([
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    ])
    return home


# ─── Splash HTML (pure local, zero network) ───

SPLASH_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>司命</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #f6f2ea;
    display: flex; align-items: center; justify-content: center;
    height: 100vh;
    font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
    overflow: hidden; -webkit-font-smoothing: antialiased;
    user-select: none;
  }
  .splash { text-align: center; animation: fadeIn 0.5s ease both; }
  .splash-icon {
    width: 80px; height: 80px; margin: 0 auto 24px;
    animation: floatIn 0.7s cubic-bezier(0.22,1,0.36,1) 0.1s both;
  }
  .splash-icon svg { width: 100%; height: 100%; filter: drop-shadow(0 4px 12px rgba(44,36,23,0.1)); }
  .splash-title {
    font-family: 'SimSun', serif;
    font-size: 42px; font-weight: 700; letter-spacing: 0.12em;
    color: #2c2417; margin-bottom: 8px;
    animation: inkReveal 0.6s cubic-bezier(0.22,1,0.36,1) 0.15s both;
  }
  .splash-sub {
    font-size: 15px; color: #a89c88; letter-spacing: 0.15em;
    font-weight: 300; margin-bottom: 48px;
    animation: fadeIn 0.4s ease 0.35s both;
  }
  .splash-divider {
    width: 64px; height: 2px; margin: 0 auto 40px;
    background: linear-gradient(90deg, transparent, #7c5e2a 20%, #7c5e2a 60%, transparent);
    opacity: 0.4; animation: brushReveal 0.7s cubic-bezier(0.22,1,0.36,1) 0.4s both;
  }
  .splash-status {
    font-size: 14px; color: #a89c88; letter-spacing: 0.04em;
    min-height: 22px; transition: opacity 0.3s ease;
  }
  .splash-dots {
    display: inline-flex; gap: 6px; margin-top: 20px;
    animation: fadeIn 0.3s ease 0.5s both;
  }
  .splash-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: #7c5e2a; opacity: 0.3;
    animation: dotPulse 1.4s ease-in-out infinite;
  }
  .splash-dot:nth-child(2) { animation-delay: 0.2s; }
  .splash-dot:nth-child(3) { animation-delay: 0.4s; }
  body::before {
    content: ''; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    opacity: 0.018; pointer-events: none; z-index: 9999;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
    background-repeat: repeat; background-size: 256px 256px; mix-blend-mode: multiply;
  }
  .splash-glow {
    position: fixed; width: 400px; height: 400px; border-radius: 50%;
    background: radial-gradient(circle, rgba(124,94,42,0.04) 0%, transparent 70%);
    pointer-events: none; animation: floatGlow 6s ease-in-out infinite;
  }
  .splash-glow-1 { top: 10%; left: 15%; }
  .splash-glow-2 { bottom: 10%; right: 15%; animation-delay: 3s; }
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
  @keyframes floatIn { from { opacity: 0; transform: translateY(16px) scale(0.95); } to { opacity: 1; transform: translateY(0) scale(1); } }
  @keyframes inkReveal { 0% { opacity: 0; transform: scale(0.92); filter: blur(4px); } 60% { opacity: 1; filter: blur(0); } 100% { opacity: 1; transform: scale(1); filter: blur(0); } }
  @keyframes brushReveal { from { clip-path: inset(0 100% 0 0); opacity: 0.2; } to { clip-path: inset(0 0 0 0); opacity: 0.4; } }
  @keyframes dotPulse { 0%, 100% { opacity: 0.2; transform: scale(0.8); } 50% { opacity: 0.8; transform: scale(1.2); } }
  @keyframes floatGlow { 0%, 100% { transform: translate(0, 0); } 50% { transform: translate(10px, -10px); } }
</style>
</head>
<body>
  <div class="splash-glow splash-glow-1"></div>
  <div class="splash-glow splash-glow-2"></div>
  <div class="splash">
    <div class="splash-icon">
      <svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
        <defs><linearGradient id="ink" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#2c2417"/><stop offset="50%" stop-color="#4a3c28"/><stop offset="100%" stop-color="#7c5e2a"/></linearGradient></defs>
        <rect width="64" height="64" rx="14" fill="#f6f2ea" stroke="#e4ddd0" stroke-width="1"/>
        <path d="M18 14 Q20 12 22 14 L22 50 Q20 52 18 50 Z" fill="url(#ink)" opacity="0.9"/>
        <path d="M26 20 Q28 18 44 20 Q46 22 44 24 L28 24 Q26 22 26 20 Z" fill="url(#ink)" opacity="0.8"/>
        <path d="M26 30 Q28 28 40 30 Q42 32 40 34 L28 34 Q26 32 26 30 Z" fill="url(#ink)" opacity="0.7"/>
        <path d="M26 40 Q28 38 36 40 Q38 42 36 44 L28 44 Q26 42 26 40 Z" fill="url(#ink)" opacity="0.6"/>
        <circle cx="48" cy="48" r="4" fill="#7c5e2a" opacity="0.25"/>
      </svg>
    </div>
    <div class="splash-title">司命</div>
    <div class="splash-sub">长篇小说的命运织机</div>
    <div class="splash-divider"></div>
    <div class="splash-status" id="status">正在启动</div>
    <div class="splash-dots" id="dots"><div class="splash-dot"></div><div class="splash-dot"></div><div class="splash-dot"></div></div>
  </div>
</body>
</html>"""


DESKTOP_PET_SPLASH_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>司命桌宠</title></head>
<body style="margin:0;background:transparent;overflow:hidden"></body>
</html>"""


def _run_mcp_server() -> None:
    """Run the MCP server over stdio."""
    _ensure_mcp_stdio()
    import argparse
    parser = argparse.ArgumentParser(prog="mcp-server")
    parser.add_argument("--mcp-server", action="store_true", help="Run MCP server over stdio")
    parser.add_argument("--project-id", default="", help="Optional default project ID.")
    parser.add_argument(
        "--creation-session-id",
        default="",
        help="Required one-session boundary for the creation_session permission pack.",
    )
    parser.add_argument("--tool-category-state-file", default="")
    parser.add_argument("--direct-mcp-lease-token", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "--permission-pack",
        default=os.environ.get("MOSHU_MCP_PERMISSION_PACK", "auto"),
        choices=["auto", "readonly_collaboration", "draft_generation", "project_writing",
                 "project_management", "internal_llm", "trusted_local_maintenance",
                 "cataloging_worker", "creation_session"],
    )
    args, _ = parser.parse_known_args()
    _prepare_data_environment()
    from app.database.bootstrap import bootstrap_database
    from app.database.session import SessionLocal, engine

    # The desktop server has already migrated the database before it launches
    # a packaged MCP child. Avoid a redundant metadata write here: it can race
    # with the active project-assistant transaction and make SQLite report
    # ``database is locked`` even though the schema is already current.
    bootstrap = bootstrap_database(engine, refresh_current_metadata=False)
    if bootstrap.read_only:
        raise RuntimeError(
            "MCP cannot start while the database is in read-only recovery mode: "
            + bootstrap.message
        )
    from app.bootstrap.composition import configure_application_services
    from app.mcp.server import serve_stdio

    configure_application_services()
    db = SessionLocal()
    try:
        serve_stdio(
            db=db,
            project_id=args.project_id,
            permission_pack=args.permission_pack,
            creation_session_id=args.creation_session_id,
            tool_category_state_file=args.tool_category_state_file,
            direct_mcp_lease_token=args.direct_mcp_lease_token,
        )
    finally:
        db.close()
        from app.services.local_runtime import get_runtime_manager

        get_runtime_manager().stop()


def _wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    """Poll until the server accepts TCP connections."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> None:
    _log(f"{APP_NAME} {APP_VERSION} launcher entered with argv={sys.argv!r}")
    _log(
        "HTTPS trust backend: "
        f"{SYSTEM_TRUST_STATUS.backend}; enabled={SYSTEM_TRUST_STATUS.enabled}"
    )
    if "--mcp-server" in sys.argv:
        _run_mcp_server()
        return

    _redirect_missing_stdio_to_log()

    if "--apply-staged-update" in sys.argv:
        try:
            _apply_staged_update()
        except Exception as exc:
            _show_error(f"{APP_NAME} 更新失败", str(exc))
            _log("Staged update failed:\n" + traceback.format_exc())
        return

    force_browser = "--browser" in sys.argv
    force_desktop = "--desktop" in sys.argv
    if force_browser:
        sys.argv.remove("--browser")
    if force_desktop:
        sys.argv.remove("--desktop")

    # Claim the data directory before selecting a port. A repeated launch must
    # activate the existing process instead of creating another server on 8766
    # that writes to the same SQLite database.
    home = _app_home()
    instance = DesktopInstanceCoordinator(home, app_name=APP_NAME)
    if not instance.acquire():
        activated = instance.activate_existing(timeout=3.0)
        _log(f"Existing desktop instance detected; activation={activated}")
        instance.close()
        if not activated:
            _show_error(
                f"{APP_NAME} 已在运行",
                "司命已在启动或运行，但暂时无法唤醒窗口。请稍候再试；"
                "若旧进程已无响应，请在任务管理器中结束 Siming.exe。",
            )
        return

    server_controller: UvicornServerController | None = None
    desktop_api: DesktopApi | None = None
    try:
        port = _find_free_port()
        home = _prepare_environment(port)
        use_browser = _use_browser_mode(
            home,
            force_browser=force_browser,
            force_desktop=force_desktop,
        )
        os.environ["SIMING_DESKTOP_WEBVIEW"] = "0" if use_browser else "1"
        server_host = (
            "0.0.0.0"
            if os.environ.get("SIMING_RUNTIME_PROFILE") == "gateway"
            else "127.0.0.1"
        )
        gui_url = f"http://127.0.0.1:{port}/gui"
        launch_mode = "browser" if use_browser else "desktop"
        _log(
            f"Port: {port}; Data: {home}; Launch mode: {launch_mode}; "
            f"Gateway: {server_host == '0.0.0.0'}"
        )

        server_controller = UvicornServerController(
            host=server_host,
            port=port,
            log_level="info",
            access_log=False,
        )
        instance.start_activation_listener(
            metadata={
                "app_version": APP_VERSION,
                "api_port": port,
                "launch_mode": launch_mode,
                "gui_url": gui_url,
                "status": "starting",
            }
        )

        if use_browser:
            import webbrowser

            instance.set_activation_handler(lambda: webbrowser.open(gui_url))
            from app.main import app

            if not server_controller.start(app):
                return
            if not _wait_for_server("127.0.0.1", port, timeout=30):
                _show_error(
                    f"{APP_NAME} 启动失败",
                    f"后端启动超时。\n日志：{_launcher_log_path()}",
                )
                return
            instance.update_metadata(status="ready")
            webbrowser.open(gui_url)
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                _log("Browser-mode shutdown requested from console")
            return

        # Display the splash immediately; import and migrate in the background
        # while retaining a controllable Uvicorn server handle.
        try:
            import webview
        except Exception:
            _log("pywebview not available:\n" + traceback.format_exc())
            _show_error(
                f"{APP_NAME} 启动失败",
                f"pywebview 不可用。\n日志：{_launcher_log_path()}",
            )
            return

        # Blob-backed exports use the browser download pipeline. Pywebview
        # cancels those downloads by default; enabling them keeps the response
        # streamed and lets WebView2 present its native Save As dialog.
        webview.settings["ALLOW_DOWNLOADS"] = True

        from app.services.application_settings import normalize_desktop_pet_settings

        launcher_settings = _load_launcher_settings(home)
        desktop_pet_preferences = normalize_desktop_pet_settings(launcher_settings)
        desktop_api = DesktopApi()
        window = webview.create_window(
            title=f"{APP_NAME}",
            html=SPLASH_HTML,
            width=1400,
            height=900,
            min_size=(800, 600),
            text_select=True,
            js_api=desktop_api,
        )
        pet_window = None
        if desktop_pet_preferences["desktop_pet_enabled"]:
            pet_width, pet_height = _desktop_pet_window_size(desktop_pet_preferences)
            pet_x, pet_y = _desktop_pet_window_position(
                launcher_settings,
                pet_width,
                pet_height,
            )
            pet_window = webview.create_window(
                title="司命桌宠",
                html=DESKTOP_PET_SPLASH_HTML,
                width=pet_width,
                height=pet_height,
                x=pet_x,
                y=pet_y,
                min_size=(DESKTOP_PET_MIN_WIDTH, DESKTOP_PET_MIN_HEIGHT),
                resizable=False,
                hidden=True,
                frameless=True,
                easy_drag=False,
                shadow=False,
                focus=False,
                on_top=desktop_pet_preferences["desktop_pet_on_top"],
                background_color=DESKTOP_PET_TRANSPARENCY_KEY,
                transparent=True,
                text_select=False,
                zoomable=False,
                js_api=desktop_api,
            )
        desktop_api.bind(
            window,
            webview.FileDialog.FOLDER,
            pet_window=pet_window,
            gui_url=gui_url,
        )
        if pet_window is not None:
            pet_window.events.moved += desktop_api._remember_desktop_pet_position

        def _activate_window() -> None:
            # Restore handles minimized windows; show handles native backends
            # that have hidden the window without minimizing it.
            for method_name in ("restore", "show"):
                try:
                    getattr(window, method_name)()
                except Exception:
                    continue

        instance.set_activation_handler(_activate_window)

        def _boot() -> None:
            """Run application startup after the native splash is visible."""
            try:
                _log("Importing app.main...")
                from app.main import app

                _log("app.main imported")
                if not server_controller.start(app):
                    _log("Server start skipped because shutdown was requested")
                    return

                if _wait_for_server("127.0.0.1", port, timeout=30):
                    _log(f"Server ready -> {gui_url}")
                    instance.update_metadata(status="ready")
                    time.sleep(0.3)
                    window.load_url(gui_url)
                    if pet_window is not None:
                        try:
                            pet_window.events.loaded.clear()
                            pet_window.load_url(f"http://127.0.0.1:{port}/desktop-pet")
                            if pet_window.events.loaded.wait(timeout=10):
                                _log("Desktop pet page loaded; installing input overlay")
                                input_overlay = (
                                    _install_windows_desktop_pet_input_overlay(
                                        pet_window
                                    )
                                    if os.name == "nt"
                                    else None
                                )
                                if os.name != "nt" or input_overlay is not None:
                                    desktop_api.attach_desktop_pet_input_overlay(
                                        input_overlay
                                    )
                                    # On Windows the overlay constructor shows the
                                    # native WebView and its input form together on
                                    # the WinForms UI thread. Calling pywebview.show
                                    # again from this boot thread can deadlock while
                                    # NavigationCompleted is still unwinding.
                                    if input_overlay is None:
                                        pet_window.show()
                                    _log("Desktop pet window is ready")
                                else:
                                    _log(
                                        "Desktop pet input overlay is unavailable; "
                                        "window remains hidden"
                                    )
                            else:
                                _log(
                                    "Desktop pet page did not finish loading; "
                                    "window remains hidden"
                                )
                        except Exception:
                            _log("Desktop pet boot failed:\n" + traceback.format_exc())
                else:
                    _log("Server timeout (30s)")
                    _close_failed_desktop_window(
                        window,
                        status_text="启动超时，请检查日志后重试",
                        message=f"后端启动超时。\n日志：{_launcher_log_path()}",
                    )
            except Exception:
                _log("Boot failed:\n" + traceback.format_exc())
                _close_failed_desktop_window(
                    window,
                    status_text="启动失败",
                    message=f"应用初始化失败。\n日志：{_launcher_log_path()}",
                )

        _log("Window visible, boot callback scheduled")
        webview.start(_boot)
        _log("pywebview closed; graceful server shutdown requested")
    finally:
        if desktop_api is not None:
            desktop_api._close()
        if server_controller is not None:
            stopped = server_controller.stop(timeout=20.0)
            _log(f"Embedded server stopped cleanly={stopped}")
        instance.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _log("Fatal:\n" + traceback.format_exc())
        if "--mcp-server" not in sys.argv:
            _show_error(f"{APP_NAME} 启动失败", f"{exc}\n\n日志：{_launcher_log_path()}")
        _safe_print(f"Startup failed: {exc}", error=True)
        if getattr(sys.stdin, "isatty", lambda: False)():
            input("Press Enter to exit...")
        raise
