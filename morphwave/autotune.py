"""Pick sensible settings for this PC: read the hardware, time a few test frames, choose.

Runs once on the first launch, and again from Settings > Display > "Detect best settings".
It sets the frame-rate cap, level of detail, render scale, anti-aliasing and GPU budget.
The automatic quality then keeps fine-tuning while the visualiser runs.
"""

import ctypes
import os
import winreg
from ctypes import wintypes

import moderngl

from . import config

# Scenes timed for the test: a typical mix of the heavier 3D worlds.
TEST_SCENES = ("journey", "kifs", "cubecascade", "cosmicocean")
LOD_NAMES = {-1.0: "Sharpest", -0.5: "Sharp", 0.0: "Balanced", 0.75: "Fast", 1.5: "Fastest"}
QUIET_FPS = 120          # the automatic setup never asks for more than this; the fans thank you


def lod_name(v):
    return min(LOD_NAMES.items(), key=lambda kv: abs(kv[0] - v))[1]


# ---------------- hardware ----------------
def gpu_memory_gb(name):
    """Dedicated video memory from the display driver's registry entry (WMI caps it at 4 GB)."""
    key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    best = 0.0
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as cls:
            for i in range(32):
                try:
                    sub = winreg.EnumKey(cls, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(cls, sub) as k:
                        desc = winreg.QueryValueEx(k, "DriverDesc")[0]
                        mem = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0]
                except OSError:
                    continue
                if isinstance(mem, bytes):
                    mem = int.from_bytes(mem, "little")
                if desc and name and (desc.lower() in name.lower() or name.lower() in desc.lower()):
                    return mem / 2 ** 30
                best = max(best, mem / 2 ** 30)
    except OSError:
        pass
    return best


def on_battery():
    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte), ("BatteryLifePercent", ctypes.c_byte),
                    ("SystemStatusFlag", ctypes.c_byte), ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
    s = SPS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return False
    return s.ACLineStatus == 0


def ram_gb():
    class MEMSTAT(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEMSTAT()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullTotalPhys / 2 ** 30


def is_integrated(name, vram):
    n = name.lower()
    if any(x in n for x in ("intel", "uhd", "iris", "vega 3", "vega 6", "vega 8", "microsoft basic")):
        return "arc" not in n
    if "radeon(tm) graphics" in n or n.strip() in ("amd radeon graphics", "radeon graphics"):
        return True
    return 0 < vram < 1.5


def specs(r):
    info = r.ctx.info
    name = str(info.get("GL_RENDERER", "unknown GPU")).replace("/PCIe/SSE2", "")
    vram = gpu_memory_gb(name)
    return {"gpu": name, "vram": vram, "integrated": is_integrated(name, vram), "cpus": os.cpu_count() or 4,
            "ram": ram_gb(), "battery": on_battery(), "width": r.mon_rect[2], "height": r.mon_rect[3],
            "refresh": r.current_refresh}


# ---------------- GPU test ----------------
def _time_scene(r, scene, lod, frames=10):
    """Median GPU milliseconds for one full-resolution frame of a scene."""
    p = r.scene_prog
    r.fbos[0][0].use()
    w, h = r.fbo_size
    if not hasattr(r, "_bench_query"):
        r._bench_query = r.ctx.query(time=True)
    q = r._bench_query
    s = r._set
    s(p, "resolution", (float(w), float(h)))
    s(p, "sceneA", config.SCENES.index(scene))
    s(p, "mixAB", 0.0)
    s(p, "lodBias", lod)
    s(p, "aa", 2)
    for k, v in (("fx_detail", 0.6), ("fx_glow", 0.7), ("fx_react", 1.0), ("fx_punch", 0.7), ("level", 0.6),
                 ("bass", 0.5), ("mid", 0.4), ("high", 0.3), ("energy", 0.5), ("vibeBright", 0.5), ("temporal", 0.1)):
        s(p, k, v)
    fa, qa, ha = config.JOURNEY[1][1:]
    s(p, "famA", fa)
    s(p, "parA", tuple(map(float, qa)))
    s(p, "switchZ", 1e9)
    s(p, "blendW", 5.0)
    for name, row in zip(("palA", "palB", "palC", "palD"), ((0.5,) * 3, (0.5,) * 3, (1.0,) * 3, (0.0, 0.33, 0.67))):
        s(p, name, row)
    times = []
    for i in range(frames + 2):
        for k, v in (("time", 3.0 + i * 0.016), ("atime", 3.0 + i * 0.016), ("travel", 8.0 + i * 0.03), ("sceneAge", 3.0)):
            s(p, k, v)
        with q:
            r.scene_vao.render(moderngl.TRIANGLE_STRIP)
        if i >= 2:                                   # the first frames include shader warm-up
            times.append(q.elapsed / 1e6)
    times.sort()
    return times[len(times) // 2]


def benchmark(r):
    old_scale = r.scale
    r.scale = 1.0
    r.fbo_size = None
    r._ensure_fbos(r.mon_rect[2], r.mon_rect[3])     # time at the monitor's full resolution
    out = {}
    for lod in (-1.0, 0.0, 1.0):
        out[lod] = sum(_time_scene(r, sc, lod) for sc in TEST_SCENES) / len(TEST_SCENES)
    r.scale = old_scale
    return out


# ---------------- choose ----------------
def choose(sp, ms):
    """ms: {lod: GPU ms per full-resolution frame}. Returns the settings to apply."""
    eco = sp["integrated"] or sp["battery"]
    budget = 0.35 if eco else 0.5
    refresh = sp["refresh"]
    # (lod bias, render scale, estimated ms), best looking first
    configs = [(-1.0, 1.0, ms[-1.0]), (-0.5, 1.0, 0.5 * (ms[-1.0] + ms[0.0])), (0.0, 1.0, ms[0.0]),
               (0.75, 1.0, 0.25 * ms[0.0] + 0.75 * ms[1.0]), (1.5, 1.0, ms[1.0] * 0.85),
               (1.5, 0.85, ms[1.0] * 0.85 * 0.72), (1.5, 0.7, ms[1.0] * 0.85 * 0.49)]

    def fits(cfg, fps):
        return cfg[2] * fps / 1000.0 <= budget * 0.8         # leave room for scene transitions

    # Rates that divide the refresh rate give perfectly even frame pacing (240 Hz: 60, 80, 120).
    even = [f for f in range(30, refresh + 1) if refresh % f == 0]
    low = [f for f in {60, 45, 30} | {f for f in even if 40 <= f <= 60} if f <= refresh] or [refresh]
    pick, fps = configs[-1], low[0]
    for f in sorted(set(low), reverse=True):
        hit = next((c for c in configs if fits(c, f)), None)
        if hit:
            pick, fps = hit, f
            break
    top = min(refresh, QUIET_FPS if not eco else 60)
    for f in even:                                           # smoother, if it costs no quality
        if fps < f <= top and fits(pick, f):
            fps = f
    lod, scale, est = pick
    aa = 3 if est < 2.0 and not eco else (2 if lod <= 0.0 else 1)
    return {"fps_cap": 0 if fps >= refresh else fps, "lod_bias": lod, "render_scale": scale, "antialias": aa,
            "gpu_budget": budget, "idle_fps": 30 if not eco else 20, "auto_quality": True,
            "auto_configured": True}, est


def run(r):
    sp = specs(r)
    try:
        ms = benchmark(r)
    except Exception as e:                           # timer queries unsupported: play it safe
        print("GPU test failed:", e)
        ms = {-1.0: 30.0, 0.0: 25.0, 1.0: 18.0}
    new, est = choose(sp, ms)
    r.s.update(new)
    config.save_settings(r.s)
    fps = new["fps_cap"] or sp["refresh"]
    vram = f", {sp['vram']:.0f} GB" if sp["vram"] else ""
    return (f"{sp['gpu']}{vram}{' (integrated)' if sp['integrated'] else ''}{' on battery' if sp['battery'] else ''}"
            f" · {sp['width']}×{sp['height']} @ {sp['refresh']} Hz · test frame {ms[0.0]:.1f} ms"
            f" -> {fps} fps cap, detail {lod_name(new['lod_bias'])}, scale {new['render_scale']:.2f},"
            f" GPU budget {new['gpu_budget']:.0%} (~{100 * est * fps / 1000:.0f}% busy)")
