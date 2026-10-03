"""Tests for packaged launcher data-directory compatibility."""

import io
import math
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import launcher
from app.routers import config


class LauncherDataDirectoryTestCase(unittest.TestCase):
    def test_windows_pet_display_is_pointer_transparent_without_changing_other_styles(self):
        hwnd = 0x123456789 if launcher.ctypes.sizeof(launcher.ctypes.c_void_p) == 8 else 0x123456
        native = types.SimpleNamespace(Handle=types.SimpleNamespace(ToInt64=lambda: hwnd))
        for current in (0x080D0008, 0x080D0028):
            user32 = MagicMock()
            get_style = user32.GetWindowLongPtrW if launcher.ctypes.sizeof(launcher.ctypes.c_void_p) == 8 else user32.GetWindowLongW
            set_style = user32.SetWindowLongPtrW if launcher.ctypes.sizeof(launcher.ctypes.c_void_p) == 8 else user32.SetWindowLongW
            get_style.return_value = current
            set_style.return_value = current
            with patch.object(launcher.ctypes, "WinDLL", return_value=user32, create=True), patch.object(
                launcher.ctypes, "set_last_error", create=True
            ):
                launcher._set_windows_pet_display_passthrough(native)
            get_style.assert_called_once_with(hwnd, -20)
            if current & 0x20:
                set_style.assert_not_called()
            else:
                set_style.assert_called_once_with(hwnd, -20, current | 0x20)

    def test_windows_pet_display_rejects_a_nonlayered_window(self):
        user32 = MagicMock()
        user32.GetWindowLongPtrW.return_value = 0x08010008
        user32.GetWindowLongW.return_value = 0x08010008
        native = types.SimpleNamespace(Handle=types.SimpleNamespace(ToInt64=lambda: 123))
        with patch.object(launcher.ctypes, "WinDLL", return_value=user32, create=True), patch.object(
            launcher.ctypes, "set_last_error", create=True
        ), self.assertRaisesRegex(RuntimeError, "not layered"):
            launcher._set_windows_pet_display_passthrough(native)
        user32.SetWindowLongPtrW.assert_not_called()
        user32.SetWindowLongW.assert_not_called()

    def test_mcp_stdio_replaces_windowed_streams_with_inherited_pipes(self):
        stdin = io.StringIO()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.object(launcher.os, "name", "nt"), patch.object(
            launcher, "_open_inherited_windows_stream", side_effect=[stdin, stdout, stderr]
        ) as open_stream, patch.object(launcher, "_configure_stdio_utf8"):
            with patch.object(launcher.sys, "stdin", None), patch.object(
                launcher.sys, "stdout", None
            ), patch.object(launcher.sys, "stderr", None):
                launcher._ensure_mcp_stdio()

                self.assertIs(launcher.sys.stdin, stdin)
                self.assertIs(launcher.sys.stdout, stdout)
                self.assertIs(launcher.sys.stderr, stderr)

        self.assertEqual(
            [call.args for call in open_stream.call_args_list],
            [(-10, "r"), (-11, "w"), (-12, "w")],
        )

    def test_desktop_api_returns_selected_export_directory(self):
        class FakeWindow:
            def create_file_dialog(self, dialog_type, allow_multiple=False):
                self.call = (dialog_type, allow_multiple)
                return (r"C:\exports",)

        window = FakeWindow()
        api = launcher.DesktopApi()
        api.bind(window, "folder")

        self.assertEqual(api.select_export_directory(), r"C:\exports")
        self.assertEqual(window.call, ("folder", False))

    def test_desktop_api_opens_only_local_routes_and_controls_pet_visibility(self):
        class FakeWindow:
            def __init__(self):
                self.scripts = []
                self.show_count = 0
                self.restore_count = 0
                self.hide_count = 0

            def evaluate_js(self, script):
                self.scripts.append(script)

            def show(self):
                self.show_count += 1

            def restore(self):
                self.restore_count += 1

            def hide(self):
                self.hide_count += 1

        main_window = FakeWindow()
        pet_window = FakeWindow()
        input_overlay = MagicMock()
        api = launcher.DesktopApi()
        api.bind(main_window, "folder", pet_window=pet_window)
        api.attach_desktop_pet_input_overlay(input_overlay)

        self.assertTrue(api.show_main_window("https://example.com/steal"))
        self.assertIn('window.location.assign("/gui")', main_window.scripts[-1])
        self.assertTrue(api.show_main_window("/settings?section=app"))
        self.assertIn("/settings?section=app", main_window.scripts[-1])
        self.assertTrue(api.hide_desktop_pet())
        self.assertTrue(api.show_desktop_pet())
        self.assertEqual(pet_window.hide_count, 0)
        self.assertEqual(pet_window.show_count, 0)
        input_overlay.hide.assert_called_once_with()
        input_overlay.show.assert_called_once_with()
        api._close()
        input_overlay.close.assert_called_once_with()

    def test_desktop_api_moves_pet_from_pointer_drag_origin(self):
        class FakePetWindow:
            x = 120
            y = 80

            def __init__(self):
                self.positions = []

            def move(self, x, y):
                self.positions.append((x, y))

        pet_window = FakePetWindow()
        api = launcher.DesktopApi()
        api.bind(object(), "folder", pet_window=pet_window)

        self.assertFalse(api.move_desktop_pet_drag(510, 330))
        self.assertTrue(api.begin_desktop_pet_drag(500, 300))
        self.assertTrue(api.move_desktop_pet_drag(527, 318))
        self.assertEqual(pet_window.positions, [(147, 98)])
        self.assertTrue(api.end_desktop_pet_drag())
        self.assertFalse(api.move_desktop_pet_drag(530, 320))
        self.assertFalse(api.begin_desktop_pet_drag("invalid", 300))

    def test_desktop_api_routes_windows_pet_geometry_through_native_overlay(self):
        pet_window = MagicMock()
        input_overlay = MagicMock()
        input_overlay.bounds.return_value = (120, 80, 360, 520)
        input_overlay.window_context.return_value = {
            "x": 120,
            "y": 80,
            "width": 360,
            "height": 520,
            "work_x": 0,
            "work_y": 0,
            "work_width": 1920,
            "work_height": 1040,
        }
        api = launcher.DesktopApi()
        api.bind(object(), "folder", pet_window=pet_window)
        api.attach_desktop_pet_input_overlay(input_overlay)

        self.assertTrue(api.begin_desktop_pet_drag(500, 300))
        self.assertTrue(api.move_desktop_pet_drag(527, 318))
        input_overlay.move.assert_called_once_with(147, 98)
        pet_window.move.assert_not_called()

        self.assertTrue(api.apply_desktop_pet_preferences({
            "desktop_pet_scale": 0.8,
            "desktop_pet_on_top": False,
        }))
        apply_args = input_overlay.apply_preferences.call_args.args
        self.assertEqual(apply_args[:3], (272, 240, False))
        self.assertIn("siming:desktop-pet-preferences", apply_args[3])
        self.assertTrue(api.apply_desktop_pet_preferences({"desktop_pet_scale": 1.2}, False))
        self.assertEqual(input_overlay.apply_preferences.call_args.args, (408, 360, True, "", None))
        anchor = {"x": "left", "y": "top"}
        self.assertTrue(api.apply_desktop_pet_preferences({"desktop_pet_scale": 1.2}, False, anchor))
        self.assertEqual(input_overlay.apply_preferences.call_args.args, (408, 360, True, "", anchor))
        for invalid in ({}, True, {"x": "center", "y": "top"}, {**anchor, "z": "left"}):
            self.assertFalse(api.apply_desktop_pet_preferences({}, False, invalid))
        self.assertFalse(api.apply_desktop_pet_preferences({}, "false"))
        pet_window.resize.assert_not_called()
        self.assertEqual(pet_window.method_calls, [])

        self.assertEqual(
            api.get_desktop_pet_window_context(),
            input_overlay.window_context.return_value,
        )
        self.assertEqual(input_overlay.restore_inside.call_count, 1)
        compact = {"width": 200, "height": 200, "viewport_width": 272, "viewport_height": 240}
        self.assertIsNone(api.move_desktop_pet_to_edge("left", compact))  # Drag still owns movement.
        api.end_desktop_pet_drag()
        input_overlay.move_to_edge.return_value = True
        self.assertEqual(api.move_desktop_pet_to_edge("right", compact), input_overlay.window_context.return_value)
        input_overlay.move_to_edge.assert_called_once_with("right", compact)
        self.assertIsNone(api.move_desktop_pet_to_edge("top", compact))
        for invalid in ({}, None, {**compact, "width": float("nan")}, {**compact, "height": 9999}, {**compact, "width": True}):
            self.assertIsNone(api.move_desktop_pet_to_edge("right", invalid))
        self.assertTrue(api.cancel_desktop_pet_edge_move())
        input_overlay.cancel_edge_move.assert_called_once_with()
        region = {"viewport_width": 272, "viewport_height": 240, "rects": [[12, 10, 80, 100]]}
        self.assertTrue(api.update_desktop_pet_hit_region(region))
        input_overlay.update_hit_region.assert_called_once_with(region)
        for invalid in (None, {}, {**region, "rects": []}, {**region, "rects": [[-1, 0, 2, 2]]},
                        {**region, "rects": [[0, 0, 9999, 2]]}, {**region, "viewport_width": float("inf")},
                        {**region, "rects": [[0, 0, True, 2]]}, {**region, "rects": [[0, 0, 2, 2]] * 2049}):
            self.assertFalse(api.update_desktop_pet_hit_region(invalid))

    def test_windows_pet_input_overlay_defers_core_access_to_mouse_ui_thread(self):
        class FakeEvent:
            def __init__(self):
                self.handlers = []

            def __iadd__(self, handler):
                self.handlers.append(handler)
                return self

            def __isub__(self, handler):
                self.handlers.remove(handler)
                return self

        class FakeTimer:
            def __init__(self):
                self.Tick = FakeEvent()
                self.Interval = 0
                self.started = False
                self.stopped = False
                self.disposed = False

            def Start(self):
                self.started = True

            def Stop(self):
                self.stopped = True

            def Dispose(self):
                self.disposed = True

        class FakeForm:
            def __init__(self):
                self.Region = None
                self.MouseDown = FakeEvent()
                self.MouseMove = FakeEvent()
                self.MouseUp = FakeEvent()
                self.MouseLeave = FakeEvent()
                self.MouseWheel = FakeEvent()
                self.IsDisposed = False
                self.InvokeRequired = False
                self.Visible = False

            def Show(self, _owner):
                self.Visible = True

            def BringToFront(self):
                return None

            def PointToScreen(self, _location):
                return types.SimpleNamespace(X=100, Y=200)

        fake_forms = types.ModuleType("System.Windows.Forms")
        fake_forms.Form = FakeForm
        fake_forms.Timer = FakeTimer
        fake_forms.FormBorderStyle = types.SimpleNamespace(**{"None": "none"})
        fake_forms.FormStartPosition = types.SimpleNamespace(Manual="manual")
        fake_forms.MouseButtons = types.SimpleNamespace(Left=1, Right=2, Middle=4)
        fake_screen = types.SimpleNamespace(
            WorkingArea=types.SimpleNamespace(
                X=0,
                Y=0,
                Width=1920,
                Height=1040,
            )
        )
        fake_forms.Screen = types.SimpleNamespace(
            FromControl=lambda _control: fake_screen,
            FromRectangle=lambda _rectangle: fake_screen,
        )

        fake_windows = types.ModuleType("System.Windows")
        fake_windows.__path__ = []
        fake_windows.Forms = fake_forms
        fake_system = types.ModuleType("System")
        fake_system.__path__ = []
        fake_system.Windows = fake_windows
        fake_system.Action = lambda callback: callback
        fake_drawing = types.ModuleType("System.Drawing")
        fake_drawing.Color = types.SimpleNamespace(
            Magenta="magenta",
            Black="black",
            Transparent="transparent",
        )
        fake_drawing.Rectangle = lambda x, y, width, height: types.SimpleNamespace(
            X=x,
            Y=y,
            Width=width,
            Height=height,
        )
        fake_drawing.Size = lambda width, height: types.SimpleNamespace(Width=width, Height=height)
        class FakeRegion:
            def __init__(self):
                self.rects = []
                self.disposed = False

            def MakeEmpty(self):
                self.rects.clear()

            def Union(self, rect):
                self.rects.append(rect)

            def Clone(self):
                region = FakeRegion()
                region.rects = list(self.rects)
                return region

            def Dispose(self):
                self.disposed = True

            def IsVisible(self, x, y):
                return any(r.X <= x < r.X + r.Width and r.Y <= y < r.Y + r.Height for r in self.rects)
        fake_drawing.Region = FakeRegion
        fake_clr = types.ModuleType("clr")
        fake_clr.AddReference = lambda _name: None

        core_calls = []

        class FakeCore:
            def CallDevToolsProtocolMethodAsync(self, method, payload):
                core_calls.append((method, payload))

            def ExecuteScriptAsync(self, script):
                core_calls.append(("Runtime.evaluate", script))

        core = FakeCore()

        class FakeNative:
            def __init__(self):
                self.Region = None
                self.InvokeRequired = True
                self.on_ui_thread = False
                self.begin_invoke_count = 0
                self.Bounds = types.SimpleNamespace(
                    X=100,
                    Y=200,
                    Width=360,
                    Height=520,
                )
                self.TopMost = True
                self.MinimumSize = fake_drawing.Size(238, 210)
                self.Visible = False
                self.LocationChanged = FakeEvent()
                self.SizeChanged = FakeEvent()
                self.FormClosed = FakeEvent()
                self.HandleCreated = FakeEvent()
                self.show_count = 0
                self.set_bounds_calls = []

                native = self

                class FakeWebView:
                    DefaultBackgroundColor = "opaque"

                    def __init__(self):
                        self.core_access_count = 0

                    @property
                    def CoreWebView2(self):
                        if not native.on_ui_thread:
                            raise RuntimeError("CoreWebView2 accessed off UI thread")
                        self.core_access_count += 1
                        return core

                self.webview = FakeWebView()
                self.browser = types.SimpleNamespace(webview=self.webview)

            def BeginInvoke(self, action):
                self.begin_invoke_count += 1
                self.on_ui_thread = True
                try:
                    action()
                finally:
                    self.on_ui_thread = False

            def Show(self):
                self.show_count += 1
                self.Visible = True

            def Hide(self):
                self.Visible = False

            def SetBounds(self, x, y, width, height):
                self.set_bounds_calls.append((x, y, width, height))
                self.Bounds = types.SimpleNamespace(
                    X=x,
                    Y=y,
                    Width=width,
                    Height=height,
                )

        native = FakeNative()
        modules = {
            "clr": fake_clr,
            "System": fake_system,
            "System.Windows": fake_windows,
            "System.Windows.Forms": fake_forms,
            "System.Drawing": fake_drawing,
        }
        with patch.dict("sys.modules", modules), patch.object(
            launcher, "_set_windows_pet_display_passthrough"
        ) as passthrough:
            overlay = launcher._WindowsDesktopPetInputOverlay(
                types.SimpleNamespace(native=native)
            )
            passthrough.assert_called_once_with(native)
            native.HandleCreated.handlers[0](None, None)
            self.assertEqual(passthrough.call_count, 2)

        self.assertEqual(native.begin_invoke_count, 1)
        self.assertEqual(native.show_count, 1)
        self.assertEqual(native.webview.core_access_count, 0)
        self.assertEqual(native.TransparencyKey, "magenta")
        self.assertEqual(overlay._form.Opacity, 1.0 / 255.0)

        native.on_ui_thread = True
        try:
            overlay._on_mouse_down(
                None,
                types.SimpleNamespace(
                    Location=object(),
                    X=10,
                    Y=20,
                    Button=fake_forms.MouseButtons.Left,
                    Clicks=1,
                ),
            )
        finally:
            native.on_ui_thread = False

        self.assertEqual(native.webview.core_access_count, 1)
        self.assertEqual(core_calls[0][0], "Input.dispatchMouseEvent")

        overlay.apply_preferences(288, 416, False, "window.preferenceApplied=true")
        self.assertEqual(native.set_bounds_calls, [(172, 304, 288, 416)])
        self.assertFalse(native.TopMost)
        self.assertFalse(overlay._form.TopMost)
        call_count = len(core_calls)
        overlay.apply_preferences(288, 416, False, "")
        self.assertEqual(native.set_bounds_calls, [(172, 304, 288, 416)])
        self.assertEqual(len(core_calls), call_count)  # A local preview does not echo a stale draft.
        self.assertEqual(overlay.bounds(), (172, 304, 288, 416))
        self.assertEqual(overlay.window_context(), {
            "x": 172,
            "y": 304,
            "width": 288,
            "height": 416,
            "work_x": 0,
            "work_y": 0,
            "work_width": 1920,
            "work_height": 1040,
            "compact": False,
        })
        # Recovery and edge movement both keep the real window in the work area.
        native.Bounds.X = 1824
        overlay.restore_inside()
        self.assertEqual(native.set_bounds_calls[-1], (1632, 304, 288, 416))
        unchanged = {"width": 288, "height": 416, "viewport_width": 288, "viewport_height": 416}
        self.assertTrue(overlay.move_to_edge("right", unchanged))
        self.assertEqual(native.set_bounds_calls[-1], (1632, 304, 288, 416))
        overlay.move(1900, 900)
        self.assertEqual(native.set_bounds_calls[-1], (1632, 624, 288, 416))
        overlay.move(-80, -60)
        self.assertEqual(native.set_bounds_calls[-1], (0, 0, 288, 416))
        self.assertEqual(core_calls[-1], (
            "Runtime.evaluate",
            "window.preferenceApplied=true",
        ))

        # Drive the same UI timer used in Windows, including a negative-origin monitor.
        fake_screen.WorkingArea.X = -1920
        native.Bounds.X = -1200
        finished = launcher.threading.Event()
        result = []
        with patch.object(launcher.time, "monotonic", return_value=100):
            overlay._invoke_native(lambda: overlay._start_edge_move_on_ui_thread("left", unchanged, finished, result))
        timer = overlay._edge_motion[0]
        with patch.object(launcher.time, "monotonic", return_value=100.2):
            overlay._invoke_native(lambda: timer.Tick.handlers[0](None, None))
        self.assertGreater(native.Bounds.X, -1920)
        self.assertLess(native.Bounds.X, -1200)
        with patch.object(launcher.time, "monotonic", return_value=102):
            overlay._invoke_native(lambda: timer.Tick.handlers[0](None, None))
        self.assertEqual(native.Bounds.X, -1920)
        self.assertEqual(overlay._form.Bounds.X, -1920)
        self.assertEqual(result, [True])
        self.assertTrue(finished.is_set())
        self.assertTrue(timer.disposed)

        finished = launcher.threading.Event()
        result = []
        overlay._invoke_native(lambda: overlay._start_edge_move_on_ui_thread("right", unchanged, finished, result))
        timer = overlay._edge_motion[0]
        overlay.restore_inside()  # Starting a drag cancels autonomous travel.
        self.assertEqual(result, [False])
        self.assertTrue(finished.is_set())
        self.assertTrue(timer.disposed)
        self.assertEqual(native.Bounds.X, -1920)
        overlay.move(-9000, 9000)
        self.assertEqual(native.set_bounds_calls[-1], (-1920, 624, 288, 416))

        # Real compaction changes both the WebView and input bounds; restoration
        # is idempotent and anchors the same monitor edge on either side.
        compact = {"width": 196, "height": 188, "viewport_width": 272, "viewport_height": 240}
        for dpi in (1, 1.5, 2):
            for side in ("left", "right"):
                width, height = int(272 * dpi), int(240 * dpi)
                x = -1920 if side == "left" else -width
                native.SetBounds(x, 100, width, height)
                for _ in range(3):
                    self.assertTrue(overlay.move_to_edge(side, compact))
                    self.assertEqual(native.Bounds.Width, math.ceil(196 * dpi))
                    self.assertEqual(native.Bounds.Height, math.ceil(188 * dpi))
                    self.assertEqual(overlay._form.Bounds, native.Bounds)
                    self.assertTrue(overlay.window_context()["compact"])
                    self.assertEqual(native.MinimumSize.Width, 1)
                    self.assertEqual(native.Bounds.X if side == "left" else native.Bounds.X + native.Bounds.Width,
                                     -1920 if side == "left" else 0)
                    overlay.cancel_edge_move()
                    overlay.cancel_edge_move()
                    self.assertEqual(overlay.bounds(), (x, 100, width, height))
                    self.assertEqual(native.MinimumSize.Width, 238)
                    self.assertFalse(overlay.window_context()["compact"])
                self.assertTrue(overlay.move_to_edge(side, compact))
                overlay.restore_inside()  # Drag origin is captured AFTER expansion.
                self.assertEqual(overlay.bounds(), (x, 100, width, height))

        self.assertTrue(overlay.move_to_edge("right", compact))
        overlay.apply_preferences(340, 300, True, "window.preferenceApplied=true")
        self.assertEqual(overlay.bounds(), (-340, 280, 340, 300))
        self.assertFalse(overlay.window_context()["compact"])

        region = {"viewport_width": 200, "viewport_height": 200,
                  "rects": [[10, 20, 40, 120], [90, 30, 100, 50]]}
        for dpi in (1, 1.5, 2):
            native.SetBounds(0, 0, int(200 * dpi), int(200 * dpi))
            overlay.update_hit_region(region)
            self.assertIsNone(native.Region, "Never clip the WebView2 composition surface")
            self.assertTrue(overlay._form.Region.IsVisible(25 * dpi, 80 * dpi))
            self.assertTrue(overlay._form.Region.IsVisible(120 * dpi, 50 * dpi))
            self.assertFalse(overlay._form.Region.IsVisible(75 * dpi, 170 * dpi))
            self.assertFalse(overlay._form.Region.IsVisible(1, 1))
            old_input = overlay._form.Region
            overlay.update_hit_region(region)
            self.assertTrue(old_input.disposed)
        native.SetBounds(0, 0, 200, 200)
        overlay.sync()
        self.assertIsNone(native.Region)
        self.assertTrue(overlay._form.Region.IsVisible(25, 80))
        self.assertFalse(overlay._form.Region.IsVisible(75, 170))

        # A scale gesture keeps one chosen anchor through grow/shrink, even at
        # a monitor edge. It must not switch from clamped-left to bottom-right.
        for work_x in (0, -1920):
            fake_screen.WorkingArea.X = work_x
            native.SetBounds(work_x, 0, 238, 210)
            for width, height in ((459, 405), (340, 300), (238, 210)):
                overlay.apply_preferences(width, height, True, "", {"x": "left", "y": "top"})
                self.assertEqual(overlay.bounds(), (work_x, 0, width, height))
            native.SetBounds(work_x + 1682, 830, 238, 210)
            for width, height in ((459, 405), (340, 300), (238, 210)):
                overlay.apply_preferences(width, height, True, "", {"x": "right", "y": "bottom"})
                self.assertEqual(overlay.bounds(), (work_x + 1920 - width, 1040 - height, width, height))

    def test_uses_legacy_data_directory_when_current_database_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy = base / "NovelWritingAgent"
            legacy.mkdir()
            (legacy / "novel_agent.db").write_bytes(b"legacy database")

            with patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(base), "USERPROFILE": str(base)},
                clear=True,
            ):
                self.assertEqual(launcher._app_home(), legacy)

    def test_uses_moshu_home_when_explicitly_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "custom"
            with patch.dict("os.environ", {"MOSHU_HOME": str(home)}, clear=True):
                self.assertEqual(launcher._app_home(), home.resolve())

    def test_prepare_data_environment_sets_database_url_for_legacy_home(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy = base / "NovelWritingAgent"
            legacy.mkdir()
            (legacy / "novel_agent.db").write_bytes(b"legacy database")

            with patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(base), "USERPROFILE": str(base)},
                clear=True,
            ):
                home = launcher._prepare_data_environment()

                self.assertEqual(home, legacy)
                self.assertEqual(
                    os.environ["DATABASE_URL"],
                    f"sqlite:///{(legacy / 'novel_agent.db').as_posix()}",
                )
                self.assertEqual(os.environ["MOSHU_HOME"], str(legacy))
                self.assertEqual(os.environ["NOVEL_AGENT_HOME"], str(legacy))

    def test_browser_mode_is_persisted_and_can_be_overridden(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            launcher._save_launcher_settings(home, {"launch_mode": "browser"})

            self.assertEqual(launcher._saved_launch_mode(home), "browser")
            self.assertTrue(launcher._use_browser_mode(home))
            self.assertFalse(launcher._use_browser_mode(home, force_desktop=True))
            self.assertTrue(launcher._use_browser_mode(home, force_browser=True, force_desktop=True))

    def test_settings_api_and_launcher_share_the_same_launch_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.dict(os.environ, {"SIMING_HOME": str(home)}, clear=False):
                response = config.update_launcher_settings(
                    config.LauncherSettingsUpdateRequest(launch_mode="browser")
                )

            self.assertEqual(response.data["launch_mode"], "browser")
            self.assertIn(response.data["update_channel"], {"stable", "preview"})
            self.assertTrue(response.data["restart_required"])
            self.assertEqual(launcher._saved_launch_mode(home), "browser")

    def test_update_channel_is_saved_with_launcher_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.dict(os.environ, {"SIMING_HOME": str(home)}, clear=False):
                response = config.update_launcher_settings(
                    config.LauncherSettingsUpdateRequest(
                        update_channel="preview"
                    )
                )

            self.assertEqual(response.data["update_channel"], "preview")

    def test_desktop_pet_settings_use_launcher_preference_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.dict(
                os.environ,
                {"SIMING_HOME": str(home), "SIMING_DESKTOP_WEBVIEW": "1"},
                clear=False,
            ):
                response = config.update_launcher_settings(
                    config.LauncherSettingsUpdateRequest(
                        desktop_pet_enabled=True,
                        desktop_pet_scale=1.2,
                        desktop_pet_opacity=0.8,
                        desktop_pet_muted=False,
                        desktop_pet_on_top=False,
                    )
                )

            self.assertTrue(response.data["desktop_pet_enabled"])
            self.assertEqual(response.data["desktop_pet_scale"], 1.2)
            self.assertEqual(response.data["desktop_pet_opacity"], 0.8)
            self.assertFalse(response.data["desktop_pet_muted"])
            self.assertFalse(response.data["desktop_pet_on_top"])
            self.assertTrue(response.data["desktop_pet_runtime_active"])
            saved = launcher._load_launcher_settings(home)
            self.assertEqual(saved["desktop_pet_scale"], 1.2)

    def test_desktop_pet_window_size_has_a_bounded_minimum(self):
        self.assertEqual(
            launcher._desktop_pet_window_size({}),
            (272, 240),
        )
        self.assertEqual(
            launcher._desktop_pet_window_size({"desktop_pet_scale": 1}),
            (340, 300),
        )
        self.assertEqual(
            launcher._desktop_pet_window_size({"desktop_pet_scale": 0.1}),
            (238, 210),
        )

    def test_gateway_launcher_settings_are_normalized_without_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.dict(os.environ, {"SIMING_HOME": str(home)}, clear=False):
                response = config.update_launcher_settings(
                    config.LauncherSettingsUpdateRequest(
                        gateway_enabled=True,
                        gateway_advertised_url="https://Siming.Example.ts.net/",
                        gateway_allowed_hosts="Siming.Example.ts.net, 192.168.1.20",
                    )
                )

            self.assertTrue(response.data["gateway_enabled"])
            self.assertEqual(
                response.data["gateway_advertised_url"],
                "https://siming.example.ts.net",
            )
            self.assertEqual(
                response.data["gateway_allowed_hosts"],
                "siming.example.ts.net,192.168.1.20",
            )
            saved = launcher._load_launcher_settings(home)
            self.assertNotIn("gateway_bootstrap_key", saved)

    def test_browser_mode_starts_local_server_without_creating_a_webview_window(self):
        class FakeEvent:
            def wait(self):
                return None

        browser_app = types.ModuleType("app.main")
        browser_app.app = object()
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            instance = MagicMock()
            instance.acquire.return_value = True
            server = MagicMock()
            server.start.return_value = True
            server.stop.return_value = True
            with patch.object(launcher.sys, "argv", ["launcher.py", "--browser"]), patch(
                "launcher._find_free_port", return_value=9876
            ), patch("launcher._app_home", return_value=home), patch(
                "launcher.DesktopInstanceCoordinator", return_value=instance
            ), patch(
                "launcher.UvicornServerController", return_value=server
            ), patch("launcher._prepare_environment", return_value=home), patch(
                "launcher._wait_for_server", return_value=True
            ), patch("launcher._log"), patch(
                "launcher.threading.Event", FakeEvent
            ), patch("webbrowser.open") as open_browser, patch.dict(
                "sys.modules", {"app.main": browser_app}
            ):
                launcher.main()

        server.start.assert_called_once_with(browser_app.app)
        server.stop.assert_called_once_with(timeout=20.0)
        instance.start_activation_listener.assert_called_once()
        instance.close.assert_called_once()
        open_browser.assert_called_once_with("http://127.0.0.1:9876/gui")

    def test_repeated_launch_activates_existing_instance_before_port_selection(self):
        instance = MagicMock()
        instance.acquire.return_value = False
        instance.activate_existing.return_value = True

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.object(launcher.sys, "argv", ["launcher.py"]), patch(
                "launcher._app_home", return_value=home
            ), patch(
                "launcher.DesktopInstanceCoordinator", return_value=instance
            ), patch("launcher._find_free_port") as find_port, patch("launcher._log"):
                launcher.main()

        instance.activate_existing.assert_called_once_with(timeout=3.0)
        instance.close.assert_called_once()
        find_port.assert_not_called()

    def test_native_window_close_stops_embedded_server_and_activation_restores_window(self):
        class FakeWindowEvent:
            def __init__(self):
                self.handlers = []

            def __iadd__(self, handler):
                self.handlers.append(handler)
                return self

            def clear(self):
                return None

            def wait(self, timeout=None):
                return True

        class FakeWindow:
            def __init__(self):
                self.loaded_url = None
                self.restore_count = 0
                self.show_count = 0
                self.width = 360
                self.height = 520
                self.x = 100
                self.y = 200
                self.on_top = True
                self.scripts = []
                self.events = types.SimpleNamespace(
                    moved=FakeWindowEvent(),
                    loaded=FakeWindowEvent(),
                )

            def load_url(self, url):
                self.loaded_url = url

            def restore(self):
                self.restore_count += 1

            def show(self):
                self.show_count += 1

            def resize(self, width, height):
                self.width = width
                self.height = height

            def move(self, x, y):
                self.x = x
                self.y = y

            def evaluate_js(self, script):
                self.scripts.append(script)

        temporary_home = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_home.cleanup)
        home = Path(temporary_home.name)
        instance = MagicMock()
        instance.acquire.return_value = True
        server = MagicMock()
        server.start.return_value = True
        server.stop.return_value = True
        window = FakeWindow()
        pet_window = FakeWindow()
        input_overlay = MagicMock()
        webview = types.ModuleType("webview")
        webview.settings = {}
        webview.FileDialog = types.SimpleNamespace(FOLDER="folder")
        webview.create_window = MagicMock(side_effect=[window, pet_window])
        webview.start = MagicMock(side_effect=lambda callback: callback())
        browser_app = types.ModuleType("app.main")
        browser_app.app = object()

        with patch.object(launcher.sys, "argv", ["launcher.py", "--desktop"]), patch.object(
            launcher.os, "name", "nt"
        ), patch(
            "launcher._app_home", return_value=home
        ), patch(
            "launcher.DesktopInstanceCoordinator", return_value=instance
        ), patch(
            "launcher.UvicornServerController", return_value=server
        ), patch("launcher._find_free_port", return_value=9876), patch(
            "launcher._prepare_environment", return_value=home
        ), patch("launcher._wait_for_server", return_value=True), patch(
            "launcher.time.sleep"
        ), patch("launcher._desktop_pet_window_position", return_value=(100, 200)), patch(
            "launcher._install_windows_desktop_pet_input_overlay",
            return_value=input_overlay,
        ) as install_input_overlay, patch(
            "launcher._log"
        ), patch.dict(
            "sys.modules", {"app.main": browser_app, "webview": webview}
        ):
            launcher.main()

        server.start.assert_called_once_with(browser_app.app)
        server.stop.assert_called_once_with(timeout=20.0)
        self.assertTrue(webview.settings["ALLOW_DOWNLOADS"])
        self.assertEqual(window.loaded_url, "http://127.0.0.1:9876/gui")
        self.assertEqual(pet_window.loaded_url, "http://127.0.0.1:9876/desktop-pet")
        self.assertEqual(webview.create_window.call_count, 2)
        pet_window_call = webview.create_window.call_args_list[1]
        self.assertTrue(pet_window_call.kwargs["transparent"])
        self.assertTrue(pet_window_call.kwargs["frameless"])
        self.assertTrue(pet_window_call.kwargs["on_top"])
        self.assertTrue(pet_window_call.kwargs["hidden"])
        self.assertEqual(
            pet_window_call.kwargs["background_color"],
            launcher.DESKTOP_PET_TRANSPARENCY_KEY,
        )
        install_input_overlay.assert_called_once_with(pet_window)
        # The Windows overlay constructor reveals both native layers on the
        # WinForms UI thread. Boot must not synchronously show or resize them a
        # second time while WebView2's loaded callback is still unwinding.
        input_overlay.show.assert_not_called()
        input_overlay.set_on_top.assert_not_called()
        input_overlay.sync.assert_not_called()
        self.assertEqual(pet_window.show_count, 0)
        activation_handler = instance.set_activation_handler.call_args.args[0]
        activation_handler()
        self.assertEqual(window.restore_count, 1)
        self.assertEqual(window.show_count, 1)

    def test_native_boot_failure_closes_window_and_releases_instance(self):
        class FakeWindow:
            def __init__(self):
                self.evaluated_scripts = []
                self.destroy_count = 0

            def evaluate_js(self, script):
                self.evaluated_scripts.append(script)

            def destroy(self):
                self.destroy_count += 1

        temporary_home = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_home.cleanup)
        home = Path(temporary_home.name)
        launcher._save_launcher_settings(home, {"desktop_pet_enabled": False})
        instance = MagicMock()
        instance.acquire.return_value = True
        server = MagicMock()
        server.stop.return_value = True
        window = FakeWindow()
        webview = types.ModuleType("webview")
        webview.settings = {}
        webview.FileDialog = types.SimpleNamespace(FOLDER="folder")
        webview.create_window = MagicMock(return_value=window)
        webview.start = MagicMock(side_effect=lambda callback: callback())
        broken_app = types.ModuleType("app.main")

        with patch.object(launcher.sys, "argv", ["launcher.py", "--desktop"]), patch(
            "launcher._app_home", return_value=home
        ), patch(
            "launcher.DesktopInstanceCoordinator", return_value=instance
        ), patch(
            "launcher.UvicornServerController", return_value=server
        ), patch("launcher._find_free_port", return_value=9876), patch(
            "launcher._prepare_environment", return_value=home
        ), patch("launcher._show_error") as show_error, patch(
            "launcher._log"
        ), patch.dict(
            "sys.modules", {"app.main": broken_app, "webview": webview}
        ):
            launcher.main()

        show_error.assert_called_once()
        self.assertEqual(window.destroy_count, 1)
        self.assertTrue(any("启动失败" in script for script in window.evaluated_scripts))
        server.start.assert_not_called()
        server.stop.assert_called_once_with(timeout=20.0)
        instance.close.assert_called_once()

    def test_staged_update_helper_replaces_old_executable_and_restarts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            update_exe = root / "Siming-2.8.0.exe"
            target_exe = root / "Siming.exe"
            update_exe.write_bytes(b"verified update")
            target_exe.write_bytes(b"old release")
            args = [
                str(update_exe),
                "--apply-staged-update",
                "--update-target",
                str(target_exe),
                "--wait-pid",
                "1234",
                "--expected-sha256",
                "b" * 64,
            ]

            expected_sha256 = launcher._sha256_file(update_exe)
            args[args.index("--expected-sha256") + 1] = expected_sha256

            with patch.object(launcher.sys, "argv", args), patch.object(
                launcher.sys, "executable", str(update_exe)
            ), patch("launcher._wait_for_process_exit", return_value=True), patch("launcher.subprocess.Popen") as popen:
                launcher._apply_staged_update()

            self.assertEqual(target_exe.read_bytes(), b"verified update")
            self.assertTrue(Path(popen.call_args.args[0][0]).samefile(target_exe))

    def test_launcher_source_does_not_contain_execution_policy_bypass(self):
        source = Path(launcher.__file__).read_text(encoding="utf-8")
        banned = " ".join(("Execution" + "Policy", "By" + "pass"))
        self.assertNotIn(banned, source)

    def test_system_trust_is_configured_before_network_frameworks_are_imported(self):
        source = Path(launcher.__file__).read_text(encoding="utf-8")

        self.assertLess(
            source.index("SYSTEM_TRUST_STATUS = configure_system_trust()"),
            source.index("import uvicorn"),
        )


if __name__ == "__main__":
    unittest.main()
