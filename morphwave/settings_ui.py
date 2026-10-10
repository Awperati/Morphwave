"""The settings window (Ctrl+Alt+S, or S in the visualiser window).

Changes apply live: playback device, vibe, theme, scenes, effects, HUD and
quality. Display mode switches live between fullscreen and wallpaper;
windowed mode and the monitor apply on the next start.

"Save settings" writes settings.json. "Save theme" writes the theme's file in themes/.
"""

import copy
import ctypes
import threading
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk

from . import config

FX_RANGES = {  # key: (label, min, max)
    "speed": ("Speed", 0.1, 3.0),
    "detail": ("Detail (GPU cost)", 0.0, 1.0),
    "glow": ("Glow", 0.0, 2.0),
    "kaleido": ("Kaleidoscope, 2D (0 = off)", 0, 12),
    "trails": ("Afterglow", 0.0, 0.95),
    "hue_drift": ("Colour drift", 0.0, 1.0),
    "punch": ("Beat punch", 0.0, 1.5),
    "react": ("Music bends the scene", 0.0, 2.0),
    "scope": ("Waveform ring brightness", 0.0, 1.0),
    "aberration": ("RGB split", 0.0, 1.5),
    "saturation": ("Saturation", 0.0, 2.0),
}


def list_monitors():
    """[label] with the primary monitor first, matching the visualiser's numbering."""
    mons = []

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

    Proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)

    def cb(hmon, hdc, rect, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        mons.append((bool(mi.dwFlags & 1), r.r - r.l, r.b - r.t))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, Proc(cb), 0)
    mons.sort(key=lambda m: not m[0])
    return [f"{i}: {w}×{h}{'  (primary)' if p else ''}" for i, (p, w, h) in enumerate(mons)]


class SettingsWindow:
    def __init__(self, settings, themes, analyzer=None, renderer=None):
        self.s, self.themes = settings, themes
        self.analyzer, self.renderer = analyzer, renderer
        self.live = renderer is not None
        self.root = None
        self._thread = None
        self._loading = False

    # ---------- opening ----------
    def open_async(self):
        """Open from the visualiser (Tk runs on its own thread)."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run, daemon=True, name="settings")
        self._thread.start()

    def run(self):
        self.root = tk.Tk()
        self.root.title("Morphwave settings")
        try:
            self.root.iconbitmap(default=str(config.ASSETS / "morphwave.ico"))
        except tk.TclError:
            pass
        h = min(860, int(self.root.winfo_screenheight() * 0.85))
        self.root.geometry(f"680x{h}")
        self.root.minsize(560, 420)
        self.root.attributes("-topmost", True)
        self.root.after(400, lambda: self.root.attributes("-topmost", False))
        self._build()
        self._tick()
        self.root.mainloop()
        self.root = None

    # ---------- layout helpers ----------
    def _build(self):
        # The buttons are packed first, along the bottom, so they always stay visible;
        # each tab scrolls if the window is too short for it.
        self.msg = ttk.Label(self.root, text="", foreground="#2a7")
        self.msg.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        bar = ttk.Frame(self.root, padding=(8, 4, 8, 4))
        bar.pack(side="bottom", fill="x")
        ttk.Button(bar, text="Save settings", command=self._save_settings).pack(side="left")
        ttk.Button(bar, text="Save theme", command=self._save_theme).pack(side="left", padx=6)
        if self.live:
            ttk.Button(bar, text="Next scene", command=self.renderer.next_scene).pack(side="left")
        ttk.Button(bar, text="Close", command=self.root.destroy).pack(side="right")
        nb = ttk.Notebook(self.root)
        nb.pack(side="top", fill="both", expand=True, padx=8, pady=(8, 0))
        for title, builder in (("Audio & vibe", self._tab_audio), ("Visuals", self._tab_visuals),
                               ("Display", self._tab_display)):
            outer = ttk.Frame(nb)
            nb.add(outer, text=title)
            canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
            sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            frame = ttk.Frame(canvas, padding=10)
            win = canvas.create_window((0, 0), window=frame, anchor="nw")
            frame.bind("<Configure>", lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>", lambda e, c=canvas, w=win: c.itemconfigure(w, width=e.width))

            def wheel(e, c=canvas):
                c.yview_scroll(int(-e.delta / 120), "units")
            canvas.bind("<Enter>", lambda e, c=canvas, w=wheel: c.bind_all("<MouseWheel>", w))
            canvas.bind("<Leave>", lambda e, c=canvas: c.unbind_all("<MouseWheel>"))
            builder(frame)

    def _row(self, parent, label, widget_fn, r):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 8))
        w = widget_fn(parent)
        w.grid(row=r, column=1, sticky="ew", pady=3)
        parent.columnconfigure(1, weight=1)
        return w

    def _check(self, parent, text, key, r, on_change=None):
        var = tk.BooleanVar(value=bool(self.s[key]))

        def changed():
            self.s[key] = var.get()
            if on_change:
                on_change()
        ttk.Checkbutton(parent, text=text, variable=var, command=changed).grid(row=r, column=0, columnspan=2, sticky="w", pady=2)
        return var

    # ---------- tabs ----------
    def _tab_audio(self, f):
        devices = ["Windows default"]
        try:
            from .audio import AudioAnalyzer
            devices += AudioAnalyzer.list_devices()
        except Exception:
            pass
        self.dev_var = tk.StringVar(value=self.s["audio_device"] or "Windows default")

        def dev_changed(*_):
            name = "" if self.dev_var.get() == "Windows default" else self.dev_var.get()
            self.s["audio_device"] = name
            if self.analyzer:
                self.analyzer.set_device(name)
        cb = self._row(f, "Playback device", lambda p: ttk.Combobox(p, values=devices, textvariable=self.dev_var, state="readonly"), 0)
        cb.bind("<<ComboboxSelected>>", dev_changed)

        self.level_bars = {}
        for i, k in enumerate(("bass", "mid", "high")):
            self.level_bars[k] = self._row(f, k.capitalize(), lambda p: ttk.Progressbar(p, maximum=1.0), 1 + i)
        self.listen_lbl = ttk.Label(f, text="")
        self.listen_lbl.grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 10))

        ttk.Separator(f).grid(row=5, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(f, text="Vibe sets the colours and how fast you fly. Energetic music also favours\n"
                          "the 3D tunnels; calm music favours the 2D lace.", foreground="#555").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.vibe_var = tk.StringVar(value=self.s["vibe_mode"])
        ttk.Radiobutton(f, text="From the music (automatic)", value="auto", variable=self.vibe_var,
                        command=self._vibe_changed).grid(row=7, column=0, columnspan=2, sticky="w")
        for i, name in enumerate(config.VIBES):
            ttk.Radiobutton(f, text=name, value=name, variable=self.vibe_var,
                            command=self._vibe_changed).grid(row=8 + i, column=0, columnspan=2, sticky="w", padx=(18, 0))
        self.vibe_lbl = ttk.Label(f, text="")
        self.vibe_lbl.grid(row=8 + len(config.VIBES), column=0, columnspan=2, sticky="w", pady=(6, 10))
        self._check(f, "Change scene when the song changes", "scene_change_on_track", 9 + len(config.VIBES))
        self._check(f, "Fade to grey and slow down when nothing is playing", "silence_fade", 10 + len(config.VIBES))

    def _vibe_changed(self):
        self.s["vibe_mode"] = self.vibe_var.get()

    def _tab_visuals(self, f):
        names = [t["name"] for t in self.themes]
        idx = self.renderer.theme_index if self.live else next(
            (i for i, t in enumerate(self.themes) if t["id"] == self.s["start_theme"]), 0)
        self.theme_var = tk.StringVar(value=names[idx])
        self.theme_cb = self._row(f, "Theme", lambda p: ttk.Combobox(p, values=names, textvariable=self.theme_var, state="readonly"), 0)
        self.theme_cb.bind("<<ComboboxSelected>>", lambda *_: self._select_theme(self.theme_cb.current()))
        ttk.Button(f, text="Copy as new theme", command=self._new_theme).grid(row=1, column=1, sticky="e")
        self.t_name = tk.StringVar()
        self._row(f, "Name", lambda p: ttk.Entry(p, textvariable=self.t_name), 2)

        sc = ttk.LabelFrame(f, text="Fractal scenes in this theme", padding=6)
        sc.grid(row=3, column=0, columnspan=2, sticky="ew", pady=6)
        self.scene_vars = {}
        for i, s in enumerate(config.SCENES):
            var = tk.BooleanVar()
            self.scene_vars[s] = var
            ttk.Checkbutton(sc, text=config.SCENE_LABELS[s], variable=var,
                            command=self._scenes_changed).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 20))
        self.t_beats = tk.IntVar()
        self._row(f, "Change scene every (beats)", lambda p: ttk.Spinbox(p, from_=0, to=512, increment=4, textvariable=self.t_beats, width=8), 4)
        self.t_trans = tk.DoubleVar()
        self._row(f, "Transition (seconds)", lambda p: ttk.Spinbox(p, from_=1, to=120, increment=5, textvariable=self.t_trans, width=8), 5)

        fxf = ttk.LabelFrame(f, text="Effects (live)", padding=6)
        fxf.grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)
        self.fx_vars = {}
        for i, (k, (label, lo, hi)) in enumerate(FX_RANGES.items()):
            var = tk.DoubleVar()
            self.fx_vars[k] = var
            self._row(fxf, label, lambda p, lo=lo, hi=hi, var=var: ttk.Scale(p, from_=lo, to=hi, variable=var), i)
            var.trace_add("write", lambda *_, k=k: self._fx_changed(k))
        for v in (self.t_name, self.t_beats, self.t_trans):
            v.trace_add("write", lambda *_: self._theme_fields_changed())
        self._load_theme_fields(idx)

    def _tab_display(self, f):
        self.mode_var = tk.StringVar(value=self.renderer.mode if self.renderer else self.s["mode"])

        def mode_changed(*_):
            m = self.mode_var.get()
            self.s["mode"] = m
            if self.renderer and m in ("fullscreen", "wallpaper") and self.renderer.mode in ("fullscreen", "wallpaper"):
                self.renderer._pending.append(("mode", m))
            elif self.renderer:
                self._say("Windowed mode applies after Save settings and a restart.")
        cb = self._row(f, "Mode", lambda p: ttk.Combobox(p, values=["fullscreen", "wallpaper", "windowed"],
                                                         textvariable=self.mode_var, state="readonly"), 0)
        cb.bind("<<ComboboxSelected>>", mode_changed)

        labels = list_monitors()
        cur = self.s["monitor"] if 0 <= self.s["monitor"] < len(labels) else 0
        self.mon_var = tk.StringVar(value=labels[cur] if labels else "0")
        mcb = self._row(f, "Monitor (on restart)", lambda p: ttk.Combobox(p, values=labels, textvariable=self.mon_var, state="readonly"), 1)
        mcb.bind("<<ComboboxSelected>>", lambda *_: self.s.__setitem__("monitor", mcb.current()))

        self.scale_var = tk.DoubleVar(value=self.s["render_scale"])
        self.scale_var.trace_add("write", lambda *_: self.s.__setitem__("render_scale", round(self.scale_var.get(), 2)))
        self._row(f, "Max render scale", lambda p: ttk.Scale(p, from_=0.4, to=1.0, variable=self.scale_var), 2)
        self._check(f, "Lower the render scale automatically if frames get slow", "auto_quality", 3)
        top = getattr(self.renderer, "refresh", 0) or 240
        self.fps_var = tk.IntVar(value=int(self.s["fps_cap"]) or top)
        fps_row = ttk.Frame(f)
        fps_row.grid(row=9, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(fps_row, text="Frame rate cap").pack(side="left")
        self.fps_cap_lbl = ttk.Label(fps_row, text="", width=16)
        self.fps_cap_lbl.pack(side="right")
        sc = ttk.Scale(fps_row, from_=30, to=top, variable=self.fps_var)
        sc.pack(side="left", fill="x", expand=True, padx=8)

        def fps_changed(*_):
            v = int(round(self.fps_var.get()))
            self.s["fps_cap"] = 0 if v >= top - 1 else v           # the top of the slider = match the display
            self.fps_cap_lbl.config(text=f"{v} fps" + ("  (display)" if self.s["fps_cap"] == 0 else ""))
        self.fps_var.trace_add("write", fps_changed)
        fps_changed()
        self.aa_var = tk.StringVar(value={1: "Off", 2: "4x", 3: "9x"}.get(self.s["antialias"], "4x"))
        acb = self._row(f, "Anti-aliasing (2D fractals)", lambda p: ttk.Combobox(p, values=["Off", "4x", "9x"],
                                                                                 textvariable=self.aa_var, state="readonly"), 4)
        acb.bind("<<ComboboxSelected>>", lambda *_: self.s.__setitem__("antialias", {"Off": 1, "4x": 2, "9x": 3}[self.aa_var.get()]))

        def hud_changed():
            if self.renderer:
                self.renderer.show_hud = self.s["show_hud"]
        self._check(f, "Show info overlay (HUD)", "show_hud", 5, hud_changed)
        self._check(f, "Show the waveform ring in the middle", "waveform_ring", 8)
        self.smooth_var = tk.DoubleVar(value=self.s["smoothness"])
        self.smooth_var.trace_add("write", lambda *_: self.s.__setitem__("smoothness", round(self.smooth_var.get(), 2)))
        self._row(f, "Smoothness (snappy ↔ silky)", lambda p: ttk.Scale(p, from_=0.0, to=1.0, variable=self.smooth_var), 9)
        self.fps_lbl = ttk.Label(f, text="")
        self.fps_lbl.grid(row=6, column=0, columnspan=2, sticky="w", pady=6)

        ttk.Label(f, text=(
            "Shortcuts (work everywhere, even as wallpaper):\n"
            "  Ctrl+Alt+S  settings\n  Ctrl+Alt+→ / ←  next / previous theme\n  Ctrl+Alt+Space  next scene\n"
            "  Ctrl+Alt+H  info overlay\n  Ctrl+Alt+W  switch fullscreen / wallpaper\n  Ctrl+Alt+Q  quit\n\n"
            "As wallpaper, it pauses while a game or video fills the screen."),
            justify="left", foreground="#444").grid(row=7, column=0, columnspan=2, sticky="w", pady=12)

    # ---------- theme editing ----------
    def _theme(self):
        return self.themes[self.theme_cb.current()]

    def _load_theme_fields(self, i):
        t = self.themes[i]
        self._loading = True
        self.t_name.set(t["name"])
        for s, var in self.scene_vars.items():
            var.set(s in t["scenes"])
        self.t_beats.set(t["scene_beats"])
        self.t_trans.set(t["transition_seconds"])
        for k, var in self.fx_vars.items():
            var.set(t["fx"][k])
        self._loading = False

    def _select_theme(self, i):
        if self.live:
            self.renderer.set_theme(i)
        else:
            self.s["start_theme"] = self.themes[i]["id"]
        self._load_theme_fields(i)

    def _scenes_changed(self):
        chosen = [s for s in config.SCENES if self.scene_vars[s].get()]
        if not chosen:
            self._say("A theme needs at least one scene.")
            self.scene_vars[self._theme()["scenes"][0]].set(True)
            return
        self._theme()["scenes"] = chosen

    def _theme_fields_changed(self):
        if self._loading:
            return
        t = self._theme()
        try:
            t["name"] = self.t_name.get()
            t["scene_beats"] = max(0, int(self.t_beats.get()))
            t["transition_seconds"] = max(0.5, float(self.t_trans.get()))
        except (tk.TclError, ValueError):
            pass

    def _fx_changed(self, k):
        if self._loading:
            return
        v = float(self.fx_vars[k].get())
        self._theme()["fx"][k] = int(round(v)) if k == "kaleido" else round(v, 3)

    def _new_theme(self):
        t = copy.deepcopy(self._theme())
        ids = {x["id"] for x in self.themes}
        n = 1
        while f"custom-{n}" in ids:
            n += 1
        t["id"], t["name"] = f"custom-{n}", t["name"] + " (copy)"
        self.themes.append(t)
        self.theme_cb["values"] = [x["name"] for x in self.themes]
        self.theme_cb.current(len(self.themes) - 1)
        self._select_theme(len(self.themes) - 1)
        self._say("New theme created. Adjust it, then press Save theme.")

    # ---------- actions ----------
    def _save_settings(self):
        if self.live:
            self.s["start_theme"] = self.themes[self.renderer.theme_index]["id"]
        config.save_settings(self.s)
        self._say(f"Saved {config.SETTINGS_PATH.name}")

    def _save_theme(self):
        path = config.save_theme(self._theme())
        self.theme_cb["values"] = [t["name"] for t in self.themes]
        self._say(f"Saved themes/{path.name}")

    def _say(self, text):
        self.msg.config(text=text)

    # ---------- live readouts ----------
    def _tick(self):
        if not self.root:
            return
        if self.analyzer:
            a = self.analyzer.snapshot()
            for k, bar in self.level_bars.items():
                bar["value"] = a[k]
            bpm = f" · {a['bpm']:.0f} BPM" if a["bpm"] else ""
            err = f"  ({self.analyzer.error})" if self.analyzer.error else ""
            self.listen_lbl.config(text=f"Listening to: {self.analyzer.device_name}{bpm}{err}")
        else:
            self.listen_lbl.config(text="Live levels show while the visualiser is running.")
        if self.renderer:
            e, b = self.renderer.vibe
            self.vibe_lbl.config(text=f"Current vibe: energy {e:.2f} · brightness {b:.2f}")
            self.fps_lbl.config(text=f"{self.renderer.fps:.0f} fps · render scale {self.renderer.scale:.2f}")
        self.root.after(150, self._tick)
