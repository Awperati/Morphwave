"""The window and the frame loop: fullscreen, live wallpaper or a normal window.

Each frame: read the music, advance the "conductor" (scene changes on beats),
render the fractal on the GPU, then draw it to the screen with the HUD.
"""

import math
import random
import time

import glfw
import moderngl
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import config, shaders, winutil
from .audio import N_BANDS, WAVE_POINTS

FX_UNIFORMS = {"detail": "fx_detail", "glow": "fx_glow", "kaleido": "fx_kaleido", "trails": "fx_trails",
               "hue_drift": "fx_hue", "punch": "fx_punch", "react": "fx_react"}


def _icon_font(size):
    """Windows 11's Segoe Fluent Icons (falls back to the Windows 10 MDL2 set)."""
    for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return None


def _font(size, bold=False):
    for name in (("seguisb.ttf", "segoeuib.ttf") if bold else ()) + ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


class Renderer:
    BAR_BOTTOM = 36   # gap under the control bar, in 1080p pixels
    def __init__(self, settings, themes, analyzer, nowplaying, args):
        self.s, self.themes, self.analyzer, self.nowplaying = settings, themes, analyzer, nowplaying
        self.mode = args.mode or settings["mode"]
        self.monitor_index = args.monitor if args.monitor is not None else settings["monitor"]
        self.show_hud = args.hud or settings["show_hud"]
        self.run_seconds = args.seconds
        self.snapshot_path = args.snapshot
        self.force_scene = args.scene
        self.force_preset = getattr(args, "preset", None)
        self.assume_music = getattr(args, "assume_music", False)
        self._pending = []           # actions from other threads, run on the GL thread
        self.settings_window = None  # set by __main__
        self.quit = False

        self.theme_index = 0
        for i, t in enumerate(themes):
            if t["id"] == settings["start_theme"]:
                self.theme_index = i
        self.scene_a, self.scene_b = 0, 0
        self.seed_a = self.seed_b = (0.0, 0.0, 0.0, 0.0)
        self.mix = -1.0              # < 0 means no transition running
        self.beats_in_scene = 0
        self.scene_started = 0.0
        self.fps = 0.0
        self.scale = float(settings["render_scale"])
        self.vibe = (0.3, 0.5)
        self.speed = 0.0
        self.refresh = self.current_refresh = 60
        self.last_input = time.perf_counter() + 2.0   # show the controls a little longer at launch
        self._accent = (0.6, 0.8, 1.0)                # icon colour, taken from the live palette
        self._card_key = None
        # Infinite journey: preset behind and ahead of the switch point
        self.ja = self.jb = 0
        self.switch_z = 1e9
        self._card_alpha = 0.0
        self._buttons = []
        self._hover = -1
        self._fb_size = self._win_size = (1, 1)
        self.occluded = False
        self._cam_z = 0.0

    @property
    def theme(self):
        return self.themes[self.theme_index]

    # ---------- controls (any thread) ----------
    def next_theme(self, step=1):
        self._pending.append(("theme", step))

    def set_theme(self, index):
        self._pending.append(("theme_index", index))

    def next_scene(self):
        self._pending.append(("scene", None))

    # ---------- setup ----------
    def _create_window(self):
        if not glfw.init():
            raise SystemExit("Could not start GLFW")
        monitors = glfw.get_monitors()
        mon = monitors[self.monitor_index] if 0 <= self.monitor_index < len(monitors) else monitors[0]
        mx, my = glfw.get_monitor_pos(mon)
        vm = glfw.get_video_mode(mon)
        self.mon_rect = (mx, my, vm.size.width, vm.size.height)
        # the fastest refresh rate this monitor offers at its current resolution
        modes = [m.refresh_rate for m in glfw.get_video_modes(mon) if (m.size.width, m.size.height) == (vm.size.width, vm.size.height)]
        self.refresh = max(modes + [vm.refresh_rate])
        self.current_refresh = vm.refresh_rate

        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, True)
        if self.mode == "windowed":
            w, h = 1280, 720
            win = glfw.create_window(w, h, "Morphwave", None, None)
            glfw.set_window_pos(win, mx + (vm.size.width - w) // 2, my + (vm.size.height - h) // 2)
        else:
            glfw.window_hint(glfw.DECORATED, False)
            glfw.window_hint(glfw.VISIBLE, self.mode != "wallpaper")
            win = glfw.create_window(vm.size.width, vm.size.height, "Morphwave", None, None)
            glfw.set_window_pos(win, mx, my)
        if not win:
            raise SystemExit("Could not create an OpenGL 3.3 window")
        self.win = win
        self.hwnd = glfw.get_win32_window(win)
        glfw.make_context_current(win)
        icon = config.ASSETS / "morphwave.png"
        if icon.exists():
            im = Image.open(icon).convert("RGBA")
            glfw.set_window_icon(win, 2, [im.resize((32, 32), Image.LANCZOS), im.resize((256, 256), Image.LANCZOS)])
        self._vsync = None
        glfw.set_key_callback(win, self._on_key)
        glfw.set_cursor_pos_callback(win, self._on_mouse)
        glfw.set_mouse_button_callback(win, self._on_click)
        if self.mode == "wallpaper":
            if not winutil.embed_as_wallpaper(self.hwnd, *self.mon_rect):
                print("Could not attach to the desktop; running fullscreen instead.")
                self.mode = "fullscreen"
            glfw.show_window(win)

    def _setup_gl(self):
        ctx = self.ctx = moderngl.create_context()
        quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], np.float32)
        vbo = ctx.buffer(quad.tobytes())
        self.scene_prog = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.SCENE)
        self.present_prog = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=shaders.PRESENT)
        self.scene_vao = ctx.vertex_array(self.scene_prog, [(vbo, "2f", "in_pos")])
        self.present_vao = ctx.vertex_array(self.present_prog, [(vbo, "2f", "in_pos")])
        self.audio_tex = ctx.texture((WAVE_POINTS, 2), 1, dtype="f4")
        self.audio_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.audio_tex.repeat_x = True
        self.hud_tex = self._rgba_texture(np.zeros((2, 2, 4), np.uint8))
        self.hud_size = (2, 2)
        self.card_tex = self._rgba_texture(np.zeros((2, 2, 4), np.uint8))
        self.card_size = (2, 2)
        self.fbos, self.fbo_size = None, None

    def _rgba_texture(self, rgba):
        h, w = rgba.shape[:2]
        tex = self.ctx.texture((w, h), 4, np.ascontiguousarray(np.flipud(rgba)).tobytes())
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        return tex

    def _ensure_fbos(self, w, h):
        size = (max(16, int(w * self.scale)), max(16, int(h * self.scale)))
        if self.fbo_size == size:
            return
        for f, t in self.fbos or []:
            f.release()
            t.release()
        self.fbos = []
        for _ in range(2):
            t = self.ctx.texture(size, 4)
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.fbos.append((self.ctx.framebuffer([t]), t))
        self.fbo_size = size

    # ---------- input ----------
    def poke(self):
        """Any user input: bring the controls card back up."""
        self.last_input = time.perf_counter()

    def _on_key(self, win, key, scancode, action, mods):
        if action != glfw.PRESS:
            return
        self.poke()
        if key in (glfw.KEY_ESCAPE, glfw.KEY_Q):
            self.quit = True
        elif key == glfw.KEY_RIGHT:
            self.next_theme(1)
        elif key == glfw.KEY_LEFT:
            self.next_theme(-1)
        elif key == glfw.KEY_SPACE:
            self.next_scene()
        elif key == glfw.KEY_H:
            self.show_hud = not self.show_hud
        elif key == glfw.KEY_W:
            self._pending.append(("toggle_wallpaper", None))
        elif key == glfw.KEY_S and self.settings_window:
            self.settings_window.open_async()

    def _on_mouse(self, win, x, y):
        last = getattr(self, "_mouse", None)
        self._mouse = (x, y)
        if last and abs(x - last[0]) + abs(y - last[1]) > 6:   # ignore tiny jitters
            self.poke()
        self._set_hover(self._button_at(x, y))

    def hotkey_bindings(self):
        H = winutil.GlobalHotkeys
        ca = H.MOD_CONTROL | H.MOD_ALT

        def with_poke(fn):
            return lambda: (self.poke(), fn())
        return [(m, vk, with_poke(fn)) for m, vk, fn in [
            (ca, 0x27, lambda: self.next_theme(1)),                    # Right
            (ca, 0x25, lambda: self.next_theme(-1)),                   # Left
            (ca, 0x20, self.next_scene),                                # Space
            (ca, 0x48, lambda: self._pending.append(("hud", None))),    # H
            (ca, 0x57, lambda: self._pending.append(("toggle_wallpaper", None))),  # W
            (ca, 0x51, lambda: self._pending.append(("quit", None))),   # Q
            (ca, 0x53, lambda: self.settings_window and self.settings_window.open_async()),  # S
        ]]

    # ---------- control bar: pops up on input, dissolves when left alone ----------
    # (action, Segoe Fluent Icons glyph, label, key hint)
    BAR_BUTTONS = [
        ("theme", 0xE790, "Theme", "← →"),
        ("scene", 0xE893, "Next scene", "Space"),
        ("settings", 0xE713, "Settings", "S"),
        ("hud", 0xE946, "Info", "H"),
        ("wallpaper", 0xE740, "Wallpaper", "W"),
        ("quit", 0xE7E8, "Quit", "Esc"),
    ]

    def _draw_card(self, fb_h):
        """A slim dock of icon buttons along the bottom, with the theme and scene above it."""
        ui = fb_h / 1080.0
        icons = _icon_font(int(26 * ui))
        label_f = _font(int(15 * ui), bold=True)
        hint_f = _font(int(13 * ui))
        cap_f = _font(int(16 * ui))
        top_c = max(max(self._accent), 1e-3)
        accent = tuple(int(255 * (0.35 + 0.65 * max(0.0, v) / top_c)) for v in self._accent)

        btn_w, btn_h, gap, pad = int(96 * ui), int(74 * ui), int(6 * ui), int(8 * ui)
        cap_h = int(26 * ui)
        bar_w = pad * 2 + btn_w * len(self.BAR_BUTTONS) + gap * (len(self.BAR_BUTTONS) - 1)
        bar_h = pad * 2 + btn_h
        w, h = bar_w, cap_h + bar_h
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)

        caption = self.track_caption()
        tw = dr.textlength(caption, font=cap_f)
        dr.text(((w - tw) / 2 + 1, 2), caption, font=cap_f, fill=(0, 0, 0, 160))     # soft shadow
        dr.text(((w - tw) / 2, 1), caption, font=cap_f, fill=(235, 235, 245, 255))

        dr.rounded_rectangle((0, cap_h, w - 1, h - 1), radius=bar_h // 2 if bar_h < 2 * btn_w else int(22 * ui),
                             fill=(10, 10, 16, 150), outline=(255, 255, 255, 34), width=max(1, int(ui)))
        self._buttons = []
        for i, (act, glyph, label, hint) in enumerate(self.BAR_BUTTONS):
            x0 = pad + i * (btn_w + gap)
            y0 = cap_h + pad
            box = (x0, y0, x0 + btn_w, y0 + btn_h)
            self._buttons.append((box, act))
            if i == self._hover:
                dr.rounded_rectangle(box, radius=int(14 * ui), fill=(255, 255, 255, 34))
            cx = x0 + btn_w / 2
            if icons:
                g = chr(glyph)
                gw = dr.textlength(g, font=icons)
                dr.text((cx - gw / 2, y0 + int(9 * ui)), g, font=icons, fill=accent + (255,))
            lw = dr.textlength(label, font=label_f)
            dr.text((cx - lw / 2, y0 + int(40 * ui)), label, font=label_f, fill=(235, 235, 245, 255))
            hw = dr.textlength(hint, font=hint_f)
            dr.text((cx - hw / 2, y0 + int(57 * ui)), hint, font=hint_f, fill=(150, 150, 165, 255))

        arr = np.asarray(im, np.float32)
        arr[..., :3] *= arr[..., 3:4] / 255.0   # premultiplied alpha
        self.card_tex.release()
        self.card_tex = self._rgba_texture(arr.astype(np.uint8))
        self.card_size = (w, h)

    def _button_at(self, x, y):
        """Index of the bar button under window position (x, y), or -1.

        Safe from any thread: uses sizes cached by the frame loop.
        """
        if self._card_alpha < 0.05 or not self._buttons:
            return -1
        fw, fh = self._fb_size
        ww, wh = self._win_size
        sx, sy = fw / max(ww, 1), fh / max(wh, 1)
        ui = fh / 1080.0
        cw, ch = self.card_size
        left = (fw - cw) / 2
        top = fh - (self.BAR_BOTTOM * ui) - ch
        px, py = x * sx - left, y * sy - top
        for i, ((x0, y0, x1, y1), _) in enumerate(self._buttons):
            if x0 <= px <= x1 and y0 <= py <= y1:
                return i
        return -1

    def _on_click(self, win, button, action, mods):
        if action != glfw.PRESS:
            return
        x, y = glfw.get_cursor_pos(win)
        self.poke()
        self._press(self._button_at(x, y), right=button == glfw.MOUSE_BUTTON_RIGHT)

    def _set_hover(self, hover):
        if hover != self._hover:
            self._hover = hover
            self._card_key = None                              # redraw with the highlight

    def hook_wanted(self):
        """The desktop mouse hook runs only while we are the wallpaper and nothing covers it."""
        return self.mode == "wallpaper" and not self.occluded and not self.quit

    def desktop_mouse(self, kind, sx, sy):
        """Mouse events over the desktop while we are the wallpaper (from winutil.DesktopMouseHook).

        Windows sends these to the desktop icons, not to us, so a low-level hook
        forwards them. Returns True to swallow a click that pressed one of our buttons.
        """
        if self.mode != "wallpaper":
            return False
        mx, my, mw, mh = self.mon_rect
        if not (mx <= sx < mx + mw and my <= sy < my + mh):
            return False
        x, y = sx - mx, sy - my
        if kind == "move":
            if y > mh * 0.75:                                   # near the bottom: show the dock
                self.poke()
            self._set_hover(self._button_at(x, y))
            return False
        i = self._button_at(x, y)
        if i < 0:
            return False
        self.poke()
        self._press(i, right=kind == "right")
        return True

    def _press(self, i, right=False):
        if i < 0:
            return
        act = self._buttons[i][1]
        if act == "theme":
            self.next_theme(-1 if right else 1)
        elif act == "scene":
            self.next_scene()
        elif act == "settings" and self.settings_window:
            self.settings_window.open_async()
        elif act == "hud":
            self.show_hud = not self.show_hud
        elif act == "wallpaper":
            self._pending.append(("toggle_wallpaper", None))
        elif act == "quit":
            self.quit = True

    def _set_mode(self, mode):
        if mode == "fullscreen" and self.mode == "wallpaper":
            winutil.unembed_to_popup(self.hwnd, *self.mon_rect)
            self.mode = "fullscreen"
        elif mode == "wallpaper" and self.mode == "fullscreen":
            if winutil.embed_as_wallpaper(self.hwnd, *self.mon_rect):
                self.mode = "wallpaper"

    def _run_pending(self, now):
        while self._pending:
            act, arg = self._pending.pop(0)
            if act == "hud":
                self.show_hud = not self.show_hud
            elif act == "quit":
                self.quit = True
            elif act == "toggle_wallpaper":
                self._set_mode("fullscreen" if self.mode == "wallpaper" else "wallpaper")
            elif act == "mode":
                self._set_mode(arg)
            elif act == "theme":
                self.theme_index = (self.theme_index + arg) % len(self.themes)
                self._begin_scene(now, fresh=True)
            elif act == "theme_index":
                if 0 <= arg < len(self.themes):
                    self.theme_index = arg
                    self._begin_scene(now, fresh=True)
            elif act == "scene":
                if self.scene_a == self.JOURNEY_ID and self._journey_only():
                    self._queue_journey_switch(self._cam_z)
                else:
                    self._begin_scene(now)

    # ---------- the conductor ----------
    def _pick_scene(self):
        names = self.theme["scenes"]
        options = [config.SCENES.index(n) for n in names]
        if len(options) > 1 and self.scene_a in options:
            options.remove(self.scene_a)
        # energetic music leans towards the 3D fly-throughs, calm music towards 2D lace
        weights = [(0.5 + self.vibe[0]) if i >= 3 else (1.5 - self.vibe[0]) for i in options]
        return random.choices(options, weights)[0]

    JOURNEY_ID = 6
    FLY = 1.4   # must match FLY in the shader

    def _journey_only(self):
        return self.theme["scenes"] == ["journey"]

    def _next_preset(self):
        names = self.theme.get("journey_presets") or []
        allowed = [i for i, j in enumerate(config.JOURNEY) if not names or j[0] in names] or list(range(len(config.JOURNEY)))
        options = [i for i in allowed if i != self.ja] or allowed
        return random.choice(options)

    def _queue_journey_switch(self, cam_z):
        """Plant the next fractal a little way down the tunnel; we fly into it."""
        if self.switch_z < 1e8:
            return
        self.jb = self._next_preset()
        self.switch_z = cam_z + 9.0 + float(self.theme["blend_length"])
        self.beats_in_scene = 0

    def track_caption(self):
        """'Artist - Song - (Scene)', or just '(Scene)' when nothing is reported as playing."""
        np_ = self.nowplaying
        parts = [x for x in (np_.artist.strip(), np_.title.strip()) if x]
        return " - ".join(parts + [f"({self.scene_label()})"])

    def scene_label(self):
        if self.scene_a == self.JOURNEY_ID:
            name = config.JOURNEY[self.ja][0]
            if self.switch_z < 1e8:
                name += "  \u2192  " + config.JOURNEY[self.jb][0]
            return name
        return config.SCENE_LABELS[config.SCENES[self.scene_a]]

    def _begin_scene(self, now, fresh=False):
        """Start a transition to a new scene (or the same scene with new parameters)."""
        nxt = self._pick_scene()
        if self.force_scene is not None:
            nxt = self.force_scene
        if nxt == self.JOURNEY_ID:
            self.ja, self.switch_z = (self._next_preset() if fresh else self.ja), 1e9
            if self.force_preset is not None:
                self.ja = self.force_preset % len(config.JOURNEY)
        seed = tuple(random.random() for _ in range(4))
        if fresh or self.mix >= 0:
            self.scene_a, self.seed_a, self.mix = nxt, seed, -1.0
        else:
            self.scene_b, self.seed_b, self.mix = nxt, seed, 0.0
        self.beats_in_scene = 0
        self.scene_started = now

    # ---------- HUD ----------
    def _draw_hud(self, a, fb_h):
        ui = fb_h / 1080.0
        t = self.theme
        vibe_src = "from the music" if self.s["vibe_mode"] == "auto" else f"fixed: {self.s['vibe_mode']}"
        scene = self.scene_label()
        lines = [
            f"Morphwave  ·  {t['name']}  ({self.theme_index + 1}/{len(self.themes)})  ·  {scene}",
            f"Audio: {self.analyzer.device_name}" + (f"  ·  {a['bpm']:.0f} BPM" if a["bpm"] else ""),
            f"Vibe ({vibe_src}): energy {self.vibe[0]:.2f}  ·  brightness {self.vibe[1]:.2f}",
            f"{self.fps:.0f} fps  ·  render scale {self.scale:.2f}  ·  speed {self.speed:.2f}  ·  next scene in "
            f"{max(0, t['scene_beats'] - self.beats_in_scene)} beats",
        ]
        if self.nowplaying.describe():
            lines.insert(2, f"Now playing: {self.nowplaying.describe()}")
        if self.analyzer.error:
            lines.append("Audio error: " + self.analyzer.error[:140])
        lines.append("Ctrl+Alt: S settings · ←/→ theme · Space next scene · H HUD · W wallpaper · Q quit")

        size = max(12, int(19 * ui))
        font = _font(size)
        w, lh = int(1050 * ui), int(size * 1.35)
        h = lh * len(lines) + int(20 * ui)
        im = Image.new("RGBA", (w, h), (0, 0, 0, 150))
        dr = ImageDraw.Draw(im)
        for i, line in enumerate(lines):
            dr.text((int(12 * ui), int(10 * ui) + i * lh), line, font=font, fill=(255, 255, 255, 255))
        arr = np.asarray(im, np.float32)
        arr[..., :3] *= arr[..., 3:4] / 255.0   # premultiplied alpha
        self.hud_tex.release()
        self.hud_tex = self._rgba_texture(arr.astype(np.uint8))
        self.hud_size = (w, h)

    # ---------- main loop ----------
    @staticmethod
    def _set(prog, name, value):
        try:
            prog[name].value = value
        except KeyError:
            pass

    def run(self):
        winutil.make_dpi_aware()
        self._create_window()
        self._setup_gl()

        env = {"bass": 0.0, "mid": 0.0, "high": 0.0, "level": 0.0}
        beat, last_beats = 0.0, 0
        travel = spin = spin_rate = 0.0
        atime = 0.0
        beat_time = -10.0
        wave_s = np.zeros(WAVE_POINTS, np.float32)
        spec_s = np.zeros(WAVE_POINTS, np.float32)
        alive, bpm_s, loud = 1.0, 0.0, 0.5
        fx = {k: float(v) for k, v in self.theme["fx"].items()}
        pal = None
        ping = 0
        start = last = time.perf_counter()
        hud_t = occ_check = 0.0
        occluded = False
        seen_track = self.nowplaying.changes
        frame_times = []
        self._begin_scene(0.0, fresh=True)

        while not glfw.window_should_close(self.win) and not self.quit:
            glfw.poll_events()
            now = time.perf_counter()
            t = now - start
            dt = min(now - last, 0.1)
            last = now
            self._run_pending(t)
            if self.run_seconds and t > self.run_seconds:
                break

            # Save the GPU while a game or video covers the wallpaper.
            if now - occ_check > 1.0:
                occ_check = now
                if self.mode == "wallpaper":
                    winutil.restack(self.hwnd)           # Explorer can reorder its children; keep the icons on top
                occluded = self.mode == "wallpaper" and winutil.fullscreen_app_on(*self.mon_rect, self.hwnd)
                self.occluded = occluded
            if occluded:
                time.sleep(0.25)
                continue

            # ---- music ----
            a = self.analyzer.snapshot()
            sm = float(self.s["smoothness"])                # 0 = snappy .. 1 = very smooth
            for k in env:
                # rise fairly quickly, fall slowly; both slow down as smoothness goes up
                rate = (14.0 if a[k] > env[k] else 4.0) * (1.0 - 0.75 * sm)
                env[k] += (a[k] - env[k]) * (1 - math.exp(-dt * rate))
            new_beat = a["beat_count"] != last_beats
            if new_beat:
                last_beats = a["beat_count"]
                beat_time = t
                beat = max(beat, 1.0 - 0.5 * float(self.s["smoothness"]))
                self.beats_in_scene += 1
            beat *= math.exp(-dt * (8.0 - 4.0 * sm))     # beat flash fades more gently when smooth
            if self.s["vibe_mode"] in config.VIBES:
                target = config.VIBES[self.s["vibe_mode"]]
            else:
                target = (a["energy"] * 1.6, a["brightness"] * 1.4)
            target = (min(target[0], 1.0), min(target[1], 1.0))
            k = 1 - math.exp(-dt * 0.5)
            self.vibe = (self.vibe[0] + (target[0] - self.vibe[0]) * k, self.vibe[1] + (target[1] - self.vibe[1]) * k)

            theme = self.theme
            for key, v in theme["fx"].items():
                fx[key] = float(v) if key == "kaleido" else fx.get(key, 0.0) + (float(v) - fx.get(key, 0.0)) * (1 - math.exp(-dt * 2.0))
            # Silence: ease towards a near-standstill, near-greyscale idle; music wakes it up.
            playing = a["silent_for"] < 1.5 or self.assume_music or not self.s["silence_fade"]
            alive += ((1.0 if playing else 0.0) - alive) * (1 - math.exp(-dt * (2.5 if playing else 0.6)))

            # Fall / fly speed follows the tempo (BPM), the pace (how busy the beats are),
            # the long-term energy and, moment to moment, the bass.
            if a["bpm"]:
                bpm_s += (a["bpm"] - bpm_s) * (1 - math.exp(-dt * 0.8))
            tempo = (bpm_s / 120.0) if bpm_s else 0.8
            pace = min(a["beat_rate"] / 2.5, 1.5)
            speed = fx["speed"] * (0.15 + 0.7 * tempo * (0.6 + 0.6 * self.vibe[0]) + 0.35 * pace + 0.5 * env["bass"])
            # quiet passages crawl, loud ones fly; silence nearly stops
            lvl = 0.8 if self.assume_music else a["level"]
            loud += (lvl - loud) * (1 - math.exp(-dt * (2.0 if lvl > loud else 0.5)))
            speed *= (0.03 + 0.97 * alive) * (0.3 + 0.9 * loud)
            glide = 1 - math.exp(-dt * (6.0 - 5.0 * sm))   # speed and spin ease towards their targets
            self.speed += (speed - self.speed) * glide
            travel += dt * self.speed
            atime += dt * (0.03 + 0.97 * alive)
            spin_target = fx["speed"] * (0.15 + 0.4 * tempo + 1.2 * env["mid"]) * (0.05 + 0.95 * alive) \
                * (1.0 if self.seed_a[3] < 0.5 else -1.0)
            spin_rate += (spin_target - spin_rate) * glide
            spin += dt * spin_rate

            # ---- conductor: scene changes ----
            changed_track = self.nowplaying.changes != seen_track
            seen_track = self.nowplaying.changes
            beats_due = theme["scene_beats"] and self.beats_in_scene >= theme["scene_beats"]
            quiet_due = playing and a["bpm"] == 0 and t - self.scene_started > 60   # music without a clear beat
            self._cam_z = travel * self.FLY
            change_due = beats_due or quiet_due or (changed_track and self.s["scene_change_on_track"])
            if self.scene_a == self.JOURNEY_ID and self._journey_only():
                # no fades: the next fractal is planted ahead and the camera flies into it
                if change_due and self.force_preset is None:
                    self._queue_journey_switch(self._cam_z)
                if self.switch_z < 1e8 and self._cam_z > self.switch_z + float(theme["blend_length"]) + 0.5:
                    self.ja, self.switch_z = self.jb, 1e9
                    self.beats_in_scene = 0
            elif self.mix < 0 and self.force_scene is None and change_due:
                self._begin_scene(t)
            if self.mix >= 0:
                self.mix += dt / max(0.3, float(theme["transition_seconds"]))
                if self.mix >= 1.0:
                    self.scene_a, self.seed_a, self.mix = self.scene_b, self.seed_b, -1.0
                    self.beats_in_scene = 0          # count the next change from when this one finished

            # palette: blend calm -> intense by the vibe's energy
            e = self.vibe[0]
            def blend(key):
                out = []
                for c, i in zip(theme["palette_calm"][key], theme["palette_intense"][key]):
                    if key == "d":                      # hue offsets wrap at 1: go the short way round
                        i = c + ((i - c + 0.5) % 1.0) - 0.5
                    out.append((1 - e) * c + e * i)
                return out
            target_pal = [blend(key) for key in "abcd"]
            if pal is None:
                pal = target_pal
            pal = [[p + (q - p) * (1 - math.exp(-dt * 1.5)) for p, q in zip(row, trow)] for row, trow in zip(pal, target_pal)]

            # ---- audio texture: waveform row + spectrum row ----
            specrow = np.interp(np.linspace(0, N_BANDS - 1, WAVE_POINTS), np.arange(N_BANDS), a["bands"])
            # The raw waveform changes completely every frame, which makes shapes shimmer.
            # Blur it along its length, then let it drift towards each new reading.
            k_w = int(5 + 24 * sm)
            kern = np.hanning(k_w + 2)[1:-1]
            kern /= kern.sum()
            wav = np.convolve(np.concatenate([a["wave"][-k_w:], a["wave"], a["wave"][:k_w]]), kern, "same")[k_w:-k_w]
            wave_s += (wav - wave_s) * (1 - math.exp(-dt * (16.0 - 13.0 * sm)))
            spec_s += (specrow - spec_s) * (1 - math.exp(-dt * (18.0 - 12.0 * sm)))
            self.audio_tex.write(np.stack([wave_s, spec_s]).astype("f4").tobytes())

            # ---- render the fractal ----
            fw, fh = glfw.get_framebuffer_size(self.win)
            self._fb_size, self._win_size = (fw, fh), glfw.get_window_size(self.win)
            if fw == 0 or fh == 0:
                time.sleep(0.05)
                continue
            if not self.s["auto_quality"]:
                self.scale = float(self.s["render_scale"])
            self._ensure_fbos(fw, fh)
            src_tex = self.fbos[ping][1]
            dst_fbo, dst_tex = self.fbos[1 - ping]
            dst_fbo.use()
            p = self.scene_prog
            src_tex.use(1)
            self.audio_tex.use(2)
            self._set(p, "texPrev", 1)
            self._set(p, "texAudio", 2)
            self._set(p, "resolution", (float(self.fbo_size[0]), float(self.fbo_size[1])))
            self._set(p, "time", t)
            self._set(p, "atime", atime)
            self._set(p, "sceneAge", t - self.scene_started)
            self._set(p, "travel", travel)
            self._set(p, "spin", spin)
            for key in env:
                self._set(p, key, env[key])
            self._set(p, "beat", beat)
            self._set(p, "energy", self.vibe[0])
            self._set(p, "vibeBright", self.vibe[1])
            self._set(p, "sceneA", self.scene_a)
            self._set(p, "sceneB", self.scene_b)
            self._set(p, "mixAB", max(self.mix, 0.0))
            self._set(p, "seedA", self.seed_a)
            self._set(p, "seedB", self.seed_b)
            for name, row in zip(("palA", "palB", "palC", "palD"), pal):
                self._set(p, name, tuple(row))
            for key, uni in FX_UNIFORMS.items():
                self._set(p, uni, fx[key])
            self._set(p, "aa", int(max(1, min(3, self.s["antialias"]))))
            fa, qa, ha = config.JOURNEY[self.ja][1:]
            fb, qb, hb = config.JOURNEY[self.jb][1:]
            self._set(p, "famA", fa)
            self._set(p, "famB", fb)
            self._set(p, "parA", tuple(map(float, qa)))
            self._set(p, "parB", tuple(map(float, qb)))
            self._set(p, "hueA", float(ha))
            self._set(p, "hueB", float(hb))
            self._set(p, "switchZ", float(self.switch_z))
            self._set(p, "blendW", float(theme["blend_length"]))
            self._set(p, "temporal", 0.06 + 0.12 * float(self.s["smoothness"]))
            self._set(p, "fx_scope", fx["scope"] if self.s["waveform_ring"] else 0.0)
            self._set(p, "beatAge", min(t - beat_time, 10.0))
            self.scene_vao.render(moderngl.TRIANGLE_STRIP)

            # ---- present ----
            if self.show_hud and now - hud_t > 0.5:
                hud_t = now
                self._draw_hud(a, fh)
            self.ctx.screen.use()
            self.ctx.viewport = (0, 0, fw, fh)
            q = self.present_prog
            dst_tex.use(0)
            self.hud_tex.use(1)
            self._set(q, "scene", 0)
            self._set(q, "hud", 1)
            self._set(q, "resolution", (float(fw), float(fh)))
            self._set(q, "hudSize", tuple(map(float, self.hud_size)))
            self._set(q, "hudOn", 1.0 if self.show_hud else 0.0)

            # controls card: fully visible for 3 s after any input, then dissolves over 2.5 s
            idle = now - self.last_input
            card_alpha = 1.0 if idle < 3.0 else max(0.0, 1.0 - (idle - 3.0) / 2.5)
            card_alpha = card_alpha * card_alpha * (3 - 2 * card_alpha)        # smooth ease
            self._card_alpha = card_alpha
            if card_alpha > 0.0:
                self._accent = tuple(a0 + b0 * math.cos(2 * math.pi * (c0 * 0.3 + d0))
                                     for a0, b0, c0, d0 in zip(*pal))
                key = (self.theme_index, self.scene_a, fh, self.theme["name"], self._hover, self.track_caption())
                if key != self._card_key:
                    self._card_key = key
                    self._draw_card(fh)
            self.card_tex.use(2)
            self._set(q, "card", 2)
            self._set(q, "cardSize", tuple(map(float, self.card_size)))
            self._set(q, "cardAlpha", card_alpha)
            self._set(q, "uiScale", fh / 1080.0)
            for key in ("high", "level"):
                self._set(q, key, env[key])
            self._set(q, "beat", beat)
            self._set(q, "fx_aberr", fx["aberration"])
            self._set(q, "fx_sat", fx["saturation"])
            self._set(q, "alive", alive)
            self._set(q, "colorAmt", alive * (0.25 + 0.75 * min(1.0, loud * 1.3)))
            try:
                q["bands"].write(np.asarray(a["bands"], np.float32).tobytes())
            except KeyError:
                pass
            self.present_vao.render(moderngl.TRIANGLE_STRIP)
            if self.snapshot_path and self.run_seconds and t > self.run_seconds - 0.1:
                data = self.ctx.screen.read(viewport=(0, 0, fw, fh), components=3)
                Image.frombytes("RGB", (fw, fh), data).transpose(Image.FLIP_TOP_BOTTOM).save(self.snapshot_path)
                print("snapshot saved:", self.snapshot_path)
                self.snapshot_path = None
            # frame cap: 0 = match the display (vsync); otherwise pace frames to the chosen rate
            cap = int(self.s["fps_cap"]) or self.current_refresh
            want_vsync = cap >= self.current_refresh
            if want_vsync != self._vsync:
                glfw.swap_interval(1 if want_vsync else 0)
                self._vsync = want_vsync
            glfw.swap_buffers(self.win)
            if not want_vsync:
                target = 1.0 / max(cap, 10)
                while time.perf_counter() - now < target:
                    remaining = target - (time.perf_counter() - now)
                    time.sleep(remaining - 0.002 if remaining > 0.003 else 0)
            ping = 1 - ping

            # ---- frame rate and automatic quality ----
            frame_times.append(time.perf_counter() - now)
            if len(frame_times) >= 30:
                avg = sum(frame_times) / len(frame_times)
                frame_times.clear()
                self.fps = 1.0 / max(avg, 1e-4)
                if self.s["auto_quality"]:
                    cap = float(self.s["render_scale"])
                    goal = (int(self.s["fps_cap"]) or self.current_refresh) * 0.9
                    if self.fps < goal * 0.85 and self.scale > 0.4:
                        self.scale = max(0.4, self.scale - 0.1)
                    elif self.fps > goal and self.scale < cap:
                        self.scale = min(cap, self.scale + 0.05)

        self.quit = True                                 # also tells the mouse hook to unhook
        if self.mode == "wallpaper":
            winutil.release_wallpaper(self.hwnd)
        glfw.terminate()
