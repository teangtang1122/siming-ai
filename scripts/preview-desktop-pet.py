"""Start the real desktop launcher with isolated data and a hidden main window.

This is a local preview harness, not an alternative desktop-pet renderer or API.
The installed Siming process, data and preferences are never modified.
"""
from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PREVIEW_HOME = ROOT / "output" / "siming-desktop-pose-preview-20260927" / "home"


def inspect_page(window):
    return window.evaluate_js("""(() => {
      const root = document.querySelector('main');
      const canvas = document.querySelector('canvas[data-pose]');
      const speech = document.querySelector('.desktop-pet__speech');
      if (!root || !canvas) return null;
      const bubble = speech?.getBoundingClientRect();
      return {status:root.dataset.petStatus,pose:canvas.dataset.pose,
        hitRegion:root.dataset.petHitRegion,
        ambient:root.dataset.desktopPetAmbient,version:canvas.dataset.poseVersion,
        background:getComputedStyle(document.body).backgroundColor,
        width:innerWidth,height:innerHeight,
        speechPhase:speech?.dataset.speechPhase || 'hidden',speechText:speech?.innerText || '',
        bubble:bubble ? {x:bubble.x,y:bubble.y,right:bubble.right,bottom:bubble.bottom} : null};
    })()""")


def check_native_composition(window, api, label):
    """Capture only our preview over a temporary, app-owned solid test surface.

    Browser screenshots cannot detect DWM / WinForms composition defects. No
    desktop input is generated, and the opaque test surface excludes unrelated
    desktop content from the captured rectangle.
    """
    import ctypes
    from ctypes import wintypes
    from System.Drawing import Bitmap, Color, Graphics, Point, Size
    from System.Drawing.Imaging import ImageFormat

    overlay = api._pet_input_overlay
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    backdrop = []
    result = []
    path = PREVIEW_HOME.parent / f"native-composition-{label}.png"

    def prepare():
        form = overlay._WinForms.Form()
        form.Text = "司命独立预览 · 透明合成测试底板"
        form.FormBorderStyle = getattr(overlay._WinForms.FormBorderStyle, "None")
        form.StartPosition = overlay._WinForms.FormStartPosition.Manual
        form.ShowInTaskbar = False
        form.TopMost = True
        form.BackColor = Color.FromArgb(203, 221, 196)
        form.Bounds = overlay._native.Bounds
        backdrop.append(form)
        # SW_SHOWNOACTIVATE, followed by nonactivating z-order changes. Never
        # move the user's pointer, type keys, or capture the rest of the screen.
        user32.ShowWindow(int(form.Handle.ToInt64()), 4)
        for target in (form, overlay._native, overlay._form):
            if not user32.SetWindowPos(int(target.Handle.ToInt64()), -1, 0, 0, 0, 0, 0x0013):
                raise ctypes.WinError(ctypes.get_last_error())

    try:
        overlay._invoke_native(prepare)
        time.sleep(0.3)
        # Blank samples are outside opaque art and the bubble (including its
        # shadow), but include the padding inside the shaped input region.
        blanks = window.evaluate_js("""(() => {
          const c=document.querySelector('canvas[data-pose]'), ctx=c.getContext('2d');
          const p=ctx.getImageData(0,0,c.width,c.height).data;
          const b=document.querySelector('.desktop-pet__speech')?.getBoundingClientRect(), points=[];
          for(let y=1;y<innerHeight;y+=2) for(let x=1;x<innerWidth;x+=2) {
            if(b && x>b.left-16 && x<b.right+16 && y>b.top-16 && y<b.bottom+16) continue;
            const px=Math.floor(x*c.width/innerWidth), py=Math.floor(y*c.height/innerHeight);
            if(p[(py*c.width+px)*4+3]===0) points.push([x,y]);
          }
          return {width:innerWidth,height:innerHeight,points};
        })()""")

        def capture():
            bounds = overlay._native.Bounds
            assert backdrop[0].Bounds == bounds, "Preview moved during composition capture"
            bitmap = Bitmap(int(bounds.Width), int(bounds.Height))
            graphics = Graphics.FromImage(bitmap)
            try:
                graphics.CopyFromScreen(Point(int(bounds.X), int(bounds.Y)), Point(0, 0),
                                        Size(int(bounds.Width), int(bounds.Height)))
                bitmap.Save(str(path), ImageFormat.Png)
                sx, sy = int(bounds.Width) / blanks["width"], int(bounds.Height) / blanks["height"]
                dark = []
                for x, y in blanks["points"]:
                    color = bitmap.GetPixel(min(int(bounds.Width)-1, int(x*sx)),
                                            min(int(bounds.Height)-1, int(y*sy)))
                    if max(int(color.R), int(color.G), int(color.B)) < 24:
                        dark.append([x, y])
                result.append({"path": str(path), "blank_samples": len(blanks["points"]),
                               "black_blank_samples": len(dark), "first_black_points": dark[:10]})
            finally:
                graphics.Dispose()
                bitmap.Dispose()
        overlay._invoke_native(capture)
    finally:
        def close_backdrop():
            for form in backdrop:
                form.Close()
                form.Dispose()
        overlay._invoke_native(close_backdrop)
    assert result[0]["blank_samples"] > 100, result
    assert result[0]["black_blank_samples"] == 0, result
    return result[0]


def check_compact_preview(window, api, *, check_composition=False):
    """Exercise the real page and native bridge, without desktop input automation."""
    initial = api.get_desktop_pet_window_context()
    normal = inspect_page(window)
    samples = []

    def wait_page(predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = inspect_page(window)
            if status and predicate(status):
                return status
            time.sleep(0.1)
        raise AssertionError(f"Preview did not reach expected state: {status}")

    def wake():
        window.evaluate_js("""document.querySelector('.desktop-pet__character')
          .dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));""")
        return wait_page(lambda page: page["ambient"] == "awake"
                         and (page["width"], page["height"]) == (normal["width"], normal["height"]))

    def choose(side):
        window.evaluate_js("""document.querySelector('main').dispatchEvent(
          new MouseEvent('contextmenu',{bubbles:true,cancelable:true}));""")
        for _ in range(30):
            if window.evaluate_js("Boolean(document.querySelector('select[aria-label=\"桌宠小动作\"]'))"):
                break
            time.sleep(0.05)
        value = json.dumps(f"peek-{side}")
        window.evaluate_js(f"""(() => {{
          const select=document.querySelector('select[aria-label="桌宠小动作"]');
          if (!select || select.disabled) throw new Error('Pose menu unavailable');
          select.value={value}; select.dispatchEvent(new Event('change',{{bubbles:true}}));
        }})()""")
        return wait_page(lambda page: page["ambient"] == f"peek-{side}"
                         and page["width"] < normal["width"])

    def input_bounds():
        overlay = api._pet_input_overlay
        result = []

        def capture():
            bounds = overlay._form.Bounds
            result.append({"x": int(bounds.X), "y": int(bounds.Y),
                           "width": int(bounds.Width), "height": int(bounds.Height)})

        overlay._invoke_native(capture)
        return result[0]

    def check_hit_region():
        # Inspect only this preview's pixels and native regions. WindowFromPoint
        # is a read-only hit test, not a click or desktop screenshot.
        time.sleep(0.25)
        pixels = window.evaluate_js("""(() => {
          const c=document.querySelector('canvas[data-pose]'), points=[];
          const p=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
          for(let y=0;y<c.height;y+=4) for(let x=0;x<c.width;x+=4)
            if(p[(y*c.width+x)*4+3]>32) points.push([x*innerWidth/c.width,y*innerHeight/c.height]);
          return {width:innerWidth,height:innerHeight,points};
        })()""")
        overlay = api._pet_input_overlay
        result = []

        def capture():
            import ctypes
            from ctypes import wintypes
            bounds = overlay._native.Bounds
            assert overlay._native.Region is None, "WebView2 display must not be shaped"
            assert overlay._form.Region is not None
            sx, sy = int(bounds.Width) / pixels["width"], int(bounds.Height) / pixels["height"]
            missed = sum(not overlay._form.Region.IsVisible(float(x * sx), float(y * sy))
                         for x, y in pixels["points"])
            assert missed == 0, f"{missed} visible character samples were outside the native region"
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.WindowFromPoint.argtypes = [wintypes.POINT]
            user32.WindowFromPoint.restype = wintypes.HWND
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            handles = {int(overlay._native.Handle.ToInt64()), int(overlay._form.Handle.ToInt64())}
            input_handle = int(overlay._form.Handle.ToInt64())
            def root_at(x, y):
                target = user32.WindowFromPoint(wintypes.POINT(int(bounds.X) + x, int(bounds.Y) + y))
                return user32.GetAncestor(target, 2) if target else None  # GA_ROOT includes WebView children.
            hits = 0
            for x, y in pixels["points"][::8]:
                assert root_at(int(x * sx), int(y * sy)) == input_handle, "Visible character lost input"
                hits += 1
            blank = 0
            for y in range(2, int(bounds.Height), 12):
                for x in range(2, int(bounds.Width), 12):
                    if overlay._form.Region.IsVisible(x, y):
                        continue
                    assert not overlay._form.Region.IsVisible(x, y)
                    assert root_at(x, y) not in handles, f"Blank point still intercepts input: {(x, y)}"
                    blank += 1
            assert blank > 10, "Expected substantial pass-through space"
            result.append({"visible_samples": len(pixels["points"]), "missed": missed,
                           "native_character_input_hits": hits,
                           "blank_points_passed_through": blank,
                           "rectangles": len(overlay._hit_region_request["rects"])})
        overlay._invoke_native(capture)
        return result[0]

    report = {"kind": "real WinForms / WebView2 / production bridge", "normal": normal, "samples": samples}
    try:
        wait_page(lambda page: page.get("hitRegion") == "ready")
        assert normal["bubble"] is None, "Speech must be hidden on startup"
        report["standing_input"] = check_hit_region()
        if check_composition:
            report["standing_composition"] = check_native_composition(window, api, "standing")
        # Exercise the production tap handler through app-local pointer events,
        # not system mouse injection. Then verify the real native region shrinks.
        def tap():
            window.evaluate_js("""(() => {
              const c=document.querySelector('.desktop-pet__character');
              for(const type of ['pointerdown','pointerup']) c.dispatchEvent(new PointerEvent(type,
                {bubbles:true,button:0,pointerId:91,screenX:100,screenY:100}));
            })()""")
            return wait_page(lambda page: page["speechPhase"] == "visible")
        spoken = tap()
        report["speech_shown_input"] = check_hit_region()
        if check_composition:
            report["speech_shown_composition"] = check_native_composition(window, api, "speech-shown")
        time.sleep(2)
        second = tap()
        assert spoken["speechText"] != second["speechText"], "Repeated tap did not change the line"
        time.sleep(3.2)  # Beyond the first tap's deadline, still inside the second.
        assert inspect_page(window)["speechPhase"] == "visible", "Old timeout hid a fresh bubble"
        wait_page(lambda page: page["bubble"] is None, timeout=6)
        report["speech_hidden_input"] = check_hit_region()
        assert report["speech_hidden_input"]["rectangles"] < report["speech_shown_input"]["rectangles"], "Bubble kept an invisible input rectangle"
        if check_composition:
            report["speech_hidden_composition"] = check_native_composition(window, api, "speech-hidden")
        report["speech_lifecycle_passed"] = True
        # New pickup pose: use this page's pointer handlers and production native
        # bridge without moving the system cursor or stealing keyboard input.
        def pickup_event(kind, *, start=False):
            window.evaluate_js(f"""document.querySelector('.desktop-pet__character')
              .dispatchEvent(new PointerEvent({json.dumps(kind)},{{bubbles:true,button:0,
                pointerId:92,screenX:{100 if start else 125},screenY:{100 if start else 110},
                clientX:180,clientY:80}}));""")
        pickup_event('pointerdown', start=True)
        pickup_event('pointermove')
        lifted = wait_page(lambda page: page["pose"] == "picked_up")
        assert lifted["bubble"] is None, "Dragging must not reveal speech"
        report["pickup_input"] = check_hit_region()
        if check_composition:
            report["pickup_composition"] = check_native_composition(window, api, "picked-up")
        seen = set()
        deadline = time.monotonic() + 9
        while time.monotonic() < deadline and seen != {0, 1, 2}:
            frame = window.evaluate_js("""(() => {
              const c=document.querySelector('canvas[data-pose]');
              return {frame:Number(c.dataset.eyeFrame),png:c.toDataURL('image/png')};
            })()""")
            if frame["frame"] not in seen:
                seen.add(frame["frame"])
                (PREVIEW_HOME.parent / f"pickup-eye-{frame['frame']}.png").write_bytes(
                    base64.b64decode(frame["png"].split(',', 1)[1]))
            time.sleep(0.025)
        assert seen == {0, 1, 2}, f"Pickup blink frames missing: {seen}"
        pickup_event('pointerup')
        wait_page(lambda page: page["pose"] == "standing")
        assert inspect_page(window)["bubble"] is None, "Drag release was misclassified as a tap"
        pickup_event('pointerdown', start=True)
        pickup_event('pointermove')
        wait_page(lambda page: page["pose"] == "picked_up")
        pickup_event('pointercancel')
        wait_page(lambda page: page["pose"] == "standing")
        report["pickup_lifecycle_passed"] = True
        report["pickup_blink_frames"] = sorted(seen)
        api._pet_input_overlay.move(initial["x"], initial["y"])
        for side in ("left", "right", "left", "right"):
            page = choose(side)
            context = api.get_desktop_pet_window_context()
            overlay = input_bounds()
            assert context["compact"] is True, context
            assert page["width"] * page["height"] < normal["width"] * normal["height"] * 0.8, page
            assert all(overlay[key] == context[key] for key in overlay), (overlay, context)
            edge = context["work_x"] if side == "left" else context["work_x"] + context["work_width"] - context["width"]
            assert context["x"] == edge, context
            bubble = page["bubble"]
            if bubble:
                assert 0 <= bubble["x"] < bubble["right"] <= page["width"], page
                assert 0 <= bubble["y"] < bubble["bottom"] <= page["height"], page
            samples.append({"side": side, "page": page, "window": context, "input": overlay,
                            "hit_region": check_hit_region()})
            if check_composition:
                samples[-1]["composition"] = check_native_composition(window, api, f"{side}-{len(samples)}")
            wake()
            restored = api.get_desktop_pet_window_context()
            assert restored["compact"] is False, restored
            assert all(restored[key] == initial[key] for key in ("width", "height")), restored
        # Leave the independent preview showing the requested compact behavior.
        report["final"] = choose("right")
        report["final"] = wait_page(lambda page: page["bubble"] is None, timeout=6)
        report["passed"] = True
    except Exception as exc:
        report["passed"] = False
        report["error"] = str(exc)
        api.cancel_desktop_pet_edge_move()
        wake()
        api._pet_input_overlay.move(initial["x"], initial["y"])
    finally:
        path = PREVIEW_HOME.parent / "native-compact-qa.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # Captured Windows terminals may still use GBK; kaomoji must not turn a
        # completed QA run into an encoding exception and restart the checks.
        print(json.dumps(report, ensure_ascii=True), flush=True)

def check_resize_preview(window, api, label, start_scale):
    """Drag the real Chromium range via the production input forwarder.

    Coordinates follow a fixed screen-space trajectory, just like a hand-held
    mouse. No OS pointer/capture/focus is changed; only this WebView receives it.
    """
    from types import SimpleNamespace
    from System.Drawing import Point
    overlay = api._pet_input_overlay
    window.evaluate_js("""(() => {
      document.querySelector('.desktop-pet__character').dispatchEvent(
        new MouseEvent('contextmenu',{bubbles:true,cancelable:true}));return true;
    })()""")
    time.sleep(0.3)
    original_scale = window.evaluate_js('Number(document.querySelector(\'input[aria-label="桌宠大小"]\').value)')
    # Setup only: exercise each initial track width, not just the smallest pet.
    # The actual check below never assigns the input value.
    window.evaluate_js(f"""(() => {{
      const c=document.querySelector('input[aria-label="桌宠大小"]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(c,{start_scale});
      c.dispatchEvent(new Event('input',{{bubbles:true}}));
      c.dispatchEvent(new KeyboardEvent('keyup',{{key:'ArrowRight',bubbles:true}}));return true;
    }})()""")
    time.sleep(0.4)
    window.evaluate_js("""(() => {
      const c=document.querySelector('input[aria-label="桌宠大小"]');
      c.scrollIntoView({block:'center'});
      window.__petDragQA={inputs:[],moves:[],events:[],errors:[],draws:[],nativeCalls:0,saves:0,
        modules:performance.getEntriesByType('resource').filter(e=>e.name.includes('DesktopPetApp')).map(e=>e.name)};
      const report=window.__petDragQA;
      const listeners=[];
      const listen=(target,type,fn,capture=false)=>{target.addEventListener(type,fn,capture);listeners.push([target,type,fn,capture]);};
      listen(window,'error',e=>report.errors.push(e.message));
      listen(c,'input',e=>report.inputs.push({at:performance.now(),value:Number(c.value),trusted:e.isTrusted}));
      listen(window,'pointermove',e=>report.moves.push({at:performance.now(),x:e.clientX,y:e.clientY,buttons:e.buttons,trusted:e.isTrusted}),true);
      for(const type of ['pointerdown','pointerup','pointercancel','gotpointercapture','lostpointercapture'])
        listen(c,type,e=>report.events.push({type,at:performance.now(),id:e.pointerId,x:e.screenX,y:e.screenY}));
      const apply=window.pywebview.api.apply_desktop_pet_preferences;
      window.pywebview.api.apply_desktop_pet_preferences=function(...args){
        report.nativeCalls++;return apply.apply(this,args);
      };
      const open=XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open=function(method,url,...args){
        if(method.toUpperCase()==='PUT' && String(url).endsWith('/config/launcher'))report.saves++;
        return open.call(this,method,url,...args);
      };
      const draw=CanvasRenderingContext2D.prototype.drawImage;
      CanvasRenderingContext2D.prototype.drawImage=function(...args){
        if(this.canvas.dataset.pose)report.draws.push({at:performance.now(),scale:this.getTransform().a,width:innerWidth});
        return draw.apply(this,args);
      };
      window.__finishPetDragQA=()=>{
        listeners.forEach(([target,type,fn,capture])=>target.removeEventListener(type,fn,capture));
        window.pywebview.api.apply_desktop_pet_preferences=apply;
        XMLHttpRequest.prototype.open=open;CanvasRenderingContext2D.prototype.drawImage=draw;
        return report;
      };
      return true;
    })()""")

    def snapshot():
        value = window.evaluate_js("""(() => {
          const c=document.querySelector('input[aria-label="桌宠大小"]'), b=c.getBoundingClientRect();
          return {value:Number(c.value),x:b.x,y:b.y,width:b.width,height:b.height,
            viewportWidth:innerWidth,viewportHeight:innerHeight};
        })()""")
        value["window"] = api.get_desktop_pet_window_context()
        value["screenX"] = value["window"]["x"] + value["x"]
        value["screenY"] = value["window"]["y"] + value["y"]
        return value

    initial = snapshot()
    x = initial["screenX"] + 8 + (initial["value"] - 0.7) / 0.65 * (initial["width"] - 16)
    y = initial["screenY"] + initial["height"] / 2

    def pointer(kind, screen_x, screen_y):
        tasks = []
        def dispatch():
            bounds = overlay._native.Bounds
            local_x, local_y = int(round(screen_x - int(bounds.X))), int(round(screen_y - int(bounds.Y)))
            payload = {"button": "none" if kind == "mouseMoved" else "left",
                       "buttons": 0 if kind == "mouseReleased" else 1}
            if kind != "mouseMoved":
                payload["clickCount"] = 1
            event = SimpleNamespace(X=local_x, Y=local_y, Location=Point(local_x, local_y))
            tasks.append(overlay._dispatch(kind, event, **payload))
        overlay._invoke_native(dispatch)
        if tasks[0] is None:
            raise RuntimeError("Native input forwarding did not return a WebView2 task")
        deadline = time.monotonic() + 2
        while not tasks[0].IsCompleted and time.monotonic() < deadline:
            time.sleep(0.001)
        if not tasks[0].IsCompleted:
            raise RuntimeError("Native input forwarding timed out")
        if tasks[0].IsFaulted:
            raise RuntimeError(str(tasks[0].Exception))

    samples = []
    report = {}
    try:
        pointer("mousePressed", x, y)
        # Grow then shrink with a constant screen-space Y. A moving track must
        # not force the user to chase it vertically or reverse the value.
        for direction, target in (("grow", initial["screenX"] + initial["width"] - 8),
                                  ("shrink", initial["screenX"] + 8)):
            start_x = x
            for step in range(1, 25):
                x = start_x + (target - start_x) * step / 24
                pointer("mouseMoved", x, y)
                time.sleep(0.016)
                sample = snapshot()
                samples.append({"direction": direction, "pointerX": x, "pointerY": y, **sample})
        pointer("mouseReleased", x, y)
        time.sleep(0.4)
        report = window.evaluate_js("window.__finishPetDragQA()")
        report.update({"initial": initial, "samples": samples, "final": snapshot()})
        report["track_drift_x"] = max(s["screenX"] for s in samples) - min(s["screenX"] for s in samples)
        report["track_drift_y"] = max(s["screenY"] for s in samples) - min(s["screenY"] for s in samples)
        report["backwards_steps"] = sum(
            a["direction"] == b["direction"] and (
                b["value"] < a["value"] if b["direction"] == "grow" else b["value"] > a["value"])
            for a, b in zip(samples, samples[1:]))
        report["max_scale"] = max(s["value"] for s in samples)
        report["final_scale"] = report["final"]["value"]
        report["max_draw_gap_ms"] = max((b["at"] - a["at"]
                                         for a, b in zip(report["draws"], report["draws"][1:])), default=0)
        report["passed"] = (not report["errors"] and report["max_scale"] == 1.35
                            and report["final_scale"] == 0.7 and report["backwards_steps"] == 0
                            and report["saves"] <= 1 and len(report["moves"]) >= 48)
        (PREVIEW_HOME.parent / f"native-resize-drag-{label}-qa.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items()
                          if key not in ("samples", "inputs", "moves", "draws")}, ensure_ascii=True), flush=True)
    finally:
        pointer("mouseReleased", x, y)
        window.evaluate_js("window.__finishPetDragQA()")
        window.evaluate_js(f"""(() => {{
          const c=document.querySelector('input[aria-label="桌宠大小"]');
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(c,{original_scale});
          c.dispatchEvent(new Event('input',{{bubbles:true}}));
          c.dispatchEvent(new KeyboardEvent('keyup',{{key:'ArrowRight',bubbles:true}}));
          document.dispatchEvent(new KeyboardEvent('keydown',{{key:'Escape',bubbles:true}}));return true;
        }})()""")
        time.sleep(0.5)
    assert report["passed"], report
    return report


def main() -> None:
    check_compact = "--check-compact" in sys.argv[1:]
    check_composition = "--check-composition" in sys.argv[1:]
    check_resize = "--check-resize" in sys.argv[1:]
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("Run npm --prefix frontend run build before opening the preview.")
    # Set every path before importing launcher / database / settings modules.
    for name in ("SIMING_HOME", "MOSHU_HOME", "NOVEL_AGENT_HOME"):
        os.environ[name] = str(PREVIEW_HOME)
    for name in ("SIMING_CONTENT_ROOT", "MOSHU_CONTENT_ROOT"):
        os.environ[name] = str(PREVIEW_HOME / "projects")
    for name in ("SIMING_MODEL_ROOT", "MOSHU_MODEL_ROOT"):
        os.environ[name] = str(PREVIEW_HOME / "models")
    for name in ("SIMING_KEY_FILE", "MOSHU_KEY_FILE", "NOVEL_AGENT_KEY_FILE"):
        os.environ[name] = str(PREVIEW_HOME / ".crypto_key")
    os.environ["DATABASE_URL"] = f"sqlite:///{(PREVIEW_HOME / 'novel_agent.db').as_posix()}"
    os.environ["SIMING_RUNTIME_PROFILE"] = "desktop-standalone"
    sys.path.insert(0, str(ROOT / "backend"))
    import launcher
    import webview

    create_window = webview.create_window
    start = webview.start
    pet = []

    def preview_window(*args, **kwargs):
        if kwargs.get("title") == launcher.APP_NAME:
            kwargs["title"] = "司命 · 独立桌宠预览（不含现有作品）"
            kwargs["hidden"] = True
        elif kwargs.get("title") == "司命桌宠":
            kwargs["title"] = "司命桌宠 · 新姿势预览"
            # Place beside, not on top of, the installed pet's usual corner.
            kwargs["x"] = max(0, int(kwargs.get("x", 680)) - 340)
            window = create_window(*args, **kwargs)
            pet.append((window, kwargs.get("js_api")))
            return window
        return create_window(*args, **kwargs)

    def start_preview(boot, *args, **kwargs):
        def boot_and_inspect():
            boot()
            if not pet:
                return
            window, api = pet[0]
            # Access only our own app's DOM and bridge, never the user's desktop.
            for _ in range(60):
                try:
                    status = inspect_page(window)
                    if status and status.get("status") in {"ready", "error"}:
                        status["window"] = api.get_desktop_pet_window_context()
                        status["isolated_home"] = str(PREVIEW_HOME)
                        report = PREVIEW_HOME.parent / "native-status.json"
                        report.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
                        print(json.dumps(status, ensure_ascii=True), flush=True)
                        try:
                            if check_resize and status["status"] == "ready":
                                context = api.get_desktop_pet_window_context()
                                resize_reports = {}
                                for label, x, y in (
                                    ("left-top", context["work_x"], context["work_y"]),
                                    ("center", context["work_x"] + context["work_width"] // 2,
                                     context["work_y"] + context["work_height"] // 2),
                                    ("right-bottom", context["work_x"] + context["work_width"] - context["width"],
                                     context["work_y"] + context["work_height"] - context["height"]),
                                ):
                                    for start_scale in (0.7, 1.0, 1.35):
                                        api._pet_input_overlay.move(x, y)
                                        window.evaluate_js("window.dispatchEvent(new Event('resize'));true")
                                        time.sleep(0.1)
                                        case = f"{label}-{start_scale}"
                                        resize_reports[case] = check_resize_preview(window, api, case, start_scale)
                                api._pet_input_overlay.move(context["x"], context["y"])
                                window.evaluate_js("window.dispatchEvent(new Event('resize'));true")
                                (PREVIEW_HOME.parent / "native-resize-drag-qa.json").write_text(
                                    json.dumps({"passed": True, "cases": resize_reports}, ensure_ascii=False, indent=2), encoding="utf-8")
                            if (check_compact or check_composition) and status["status"] == "ready":
                                check_compact_preview(window, api, check_composition=check_composition)
                        except Exception as exc:
                            # A failed interaction check is not a readiness
                            # delay. Leave its evidence, do not replay it 60 times.
                            print(f"Preview QA failed: {exc!a}", flush=True)
                        return
                except Exception as exc:
                    print(f"Preview inspection pending: {exc!a}", flush=True)
                time.sleep(0.25)
            print("Preview page readiness could not be confirmed.", flush=True)
        return start(boot_and_inspect, *args, **kwargs)

    webview.create_window = preview_window
    webview.start = start_preview
    sys.argv = [str(ROOT / "backend/launcher.py"), "--desktop"]
    launcher.main()


if __name__ == "__main__":
    main()
