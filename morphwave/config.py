"""Settings and themes.

settings.json holds app-wide options. Each .json file in themes/ is one visual
theme: which fractal scenes it uses, its colour palettes and effect strengths.
"""

import copy
import json
import shutil
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # Built as Morphwave.exe: settings and themes live next to the exe so they can be edited;
    # the bundled copies are only defaults.
    ROOT = Path(sys.executable).resolve().parent
    BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parent.parent
    BUNDLE = ROOT
ASSETS = BUNDLE / "assets"
SETTINGS_PATH = ROOT / "settings.json"
THEMES_DIR = ROOT / "themes"

SCENES = ["julia", "mandelbrot", "apollonian", "kifs", "spheres", "kleinian", "journey",
          "netofbeing", "cosmicocean", "watchingwoods", "cubecascade"]
SCENE_LABELS = {
    "julia": "Julia set (2D)",
    "mandelbrot": "Mandelbrot dive (2D)",
    "apollonian": "Apollonian lace (2D)",
    "kifs": "Menger tunnel (3D)",
    "spheres": "Sphere-lace tunnel (3D)",
    "kleinian": "Kleinian caverns (3D)",
    "journey": "Infinite journey (3D)",
    "netofbeing": "Net of Being (3D)",
    "cosmicocean": "Cosmic Ocean (3D)",
    "watchingwoods": "Watching Woods (3D)",
    "cubecascade": "Cube Cascade (3D)",
}

# The Infinite Journey's fractals: (name, family, four shape numbers, colour offset).
# Families: 0 Menger, 1 sphere packing, 2 Kleinian, 3 Sierpinski tetrahedron,
# 5 layered gyroid, 6 sphere-eaten rock, 7 octahedron-eaten crystal, 8 spiderweb.
JOURNEY = [
    ("Stone Labyrinth", 0, (0.0, 0.0, 0.0, 0.35), 0.00),
    ("Twisted Spire", 0, (0.25, 0.1, 0.0, 0.35), 0.10),
    ("Spiral Catacombs", 0, (0.5, -0.2, 0.0, 0.3), 0.55),
    ("Shattered Lattice", 0, (0.9, 0.4, 0.0, 0.35), 0.80),
    ("Clockwork Halls", 0, (-0.35, 0.6, 0.0, 0.35), 0.30),
    ("Obsidian Maze", 0, (0.15, -0.35, 0.0, 0.32), 0.65),
    ("Cathedral Sponge", 0, (0.08, 0.0, 0.25, 0.35), 0.18),
    ("Iron Cage", 0, (0.0, 0.0, -0.3, 0.35), 0.62),
    ("Ribbon Vault", 0, (0.6, 0.15, -0.2, 0.35), 0.40),
    ("Crystal Sponge", 0, (0.3, 0.45, 0.3, 0.33), 0.75),
    ("Echo Corridors", 0, (-0.15, 0.0, 0.15, 0.4), 0.05),
    ("Gilded Maze", 0, (0.4, -0.5, -0.1, 0.35), 0.12),
    ("Bubble Reef", 1, (1.05, 0.5, 0.3, 0.0), 0.15),
    ("Foam Cathedral", 1, (1.15, 0.5, 0.3, 0.0), 0.45),
    ("Pearl Caverns", 1, (1.25, 0.45, 0.3, 0.0), 0.70),
    ("Coral Bloom", 1, (1.33, 0.5, 0.3, 0.0), 0.05),
    ("Glass Hive", 1, (1.42, 0.55, 0.28, 0.0), 0.60),
    ("Kleinian Caverns", 2, (0.0, 0.70968, 0.55, 0.25), 0.25),
    ("Bone Orchard", 2, (-0.05, 0.69, 0.55, 0.25), 0.90),
    ("Sea Lace", 2, (0.04, 0.72, 0.6, 0.25), 0.50),
    ("Lichen Vault", 2, (0.02, 0.68, 0.5, 0.25), 0.35),
    ("Cathedral Ribs", 2, (-0.03, 0.74, 0.55, 0.25), 0.75),
    ("Sierpinski Spires", 3, (0.0, 0.0, 2.0, 0.35), 0.20),
    ("Crystal Thorns", 3, (0.2, 0.1, 2.0, 0.35), 0.65),
    ("Spider Web", 8, (14.0, 0.18, 0.37, 0.32), 0.55),
    ("Tetra Galaxy", 3, (-0.15, 0.08, 2.0, 0.35), 0.12),
    ("Foam Rock", 6, (0.85, 0.3, 0.0, 2.7), 0.58),
    ("Bone Marrow", 6, (0.95, 0.6, 0.31, 2.4), 0.33),
    ("Pumice Spiral", 6, (0.8, 1.1, 0.53, 2.9), 0.85),
    ("Diamond Catacombs", 7, (1.1, 0.25, 3.0, 0.35), 0.48),
    ("Faceted Hive", 7, (1.25, 0.7, 2.6, 0.35), 0.02),
    ("Gyroid Flow", 5, (3.0, 0.15, 3.1, 0.0), 0.62),
    ("Membrane", 5, (4.0, 0.3, 2.5, 0.1), 0.27),
    ("Silk Tunnel", 5, (2.5, 0.08, 4.0, 0.25), 0.78),
    ("Cell Wall", 5, (5.0, 0.4, 2.0, 0.0), 0.08),
    ("Vortex Lattice", 5, (3.5, 0.2, 3.7, -0.3), 0.42),
]

# Manual vibes: (energy, brightness), the same scale the music analysis produces.
VIBES = {
    "Calm": (0.12, 0.45),
    "Dreamy": (0.3, 0.65),
    "Groovy": (0.5, 0.5),
    "Energetic": (0.75, 0.7),
    "Aggressive": (0.95, 0.35),
    "Dark": (0.55, 0.12),
}

DEFAULT_SETTINGS = {
    "audio_device": "",         # playback device to listen to; empty = Windows default
    "vibe_mode": "auto",        # auto = read the vibe from the music; otherwise a name from VIBES
    "scene_change_on_track": True,
    "mode": "fullscreen",       # fullscreen | wallpaper | windowed
    "monitor": 0,               # 0 = primary
    "start_theme": "",          # theme file name without extension; empty = first
    "show_hud": False,
    "render_scale": 1.0,        # < 1.0 renders fewer pixels, then upscales
    "auto_quality": True,       # lower the render scale automatically if frames get slow
    "fps_cap": 0,               # frame-rate cap; 0 = match the display's refresh rate
    "antialias": 2,             # samples per axis for the 2D fractals (1 = off, 2 = 4x, 3 = 9x)
    "waveform_ring": False,     # draw the oscilloscope ring in the middle
    "smoothness": 0.6,
    "silence_fade": True,       # fade to near-grey and slow almost to a stop when nothing plays          # 0 = snappy and twitchy .. 1 = slow and silky
}

THEME_DEFAULTS = {
    "name": "Untitled",
    "scenes": ["julia", "apollonian", "kifs", "mandelbrot"],
    "scene_beats": 32,          # change scene every this many beats (0 = never)
    "transition_seconds": 12,   # how long the fall into the next scene takes
    "blend_length": 5.0,        # Infinite journey: how long the morph stretch of tunnel is
    "journey_presets": [],      # names from JOURNEY to use; empty = all 30
    # Two cosine palettes (a + b*cos(2pi(c*t + d))); the music's energy blends calm -> intense.
    "palette_calm": {"a": [0.5, 0.5, 0.5], "b": [0.5, 0.5, 0.5], "c": [1.0, 1.0, 1.0], "d": [0.0, 0.33, 0.67]},
    "palette_intense": {"a": [0.5, 0.5, 0.5], "b": [0.5, 0.5, 0.5], "c": [1.0, 1.0, 1.0], "d": [0.0, 0.1, 0.2]},
    "fx": {
        "speed": 1.0,           # how fast you travel / things evolve
        "detail": 0.6,          # fractal iterations and ray-march precision (GPU cost)
        "glow": 0.7,            # edge and orbit-trap glow
        "kaleido": 0,           # mirror segments for the 2D scenes, 0 = off
        "trails": 0.0,          # afterglow of bright edges (no blur)
        "hue_drift": 0.2,       # slow colour rotation
        "punch": 0.7,           # zoom kick and flash on beats
        "scope": 0.5,           # oscilloscope ring brightness (shown only if waveform_ring is on)
        "react": 1.0,           # how hard the music bends and lights the scene itself
        "aberration": 0.3,      # RGB split on treble and beats
        "saturation": 1.1,
    },
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings():
    data = {}
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, encoding="utf-8-sig") as f:
            data = json.load(f)
    s = _merge(DEFAULT_SETTINGS, {k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    if not SETTINGS_PATH.exists() or set(data) != set(DEFAULT_SETTINGS):
        save_settings(s)
    return s


def save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({k: s[k] for k in DEFAULT_SETTINGS}, f, indent=2)


def load_themes():
    if not THEMES_DIR.exists() and (BUNDLE / "themes").exists():
        shutil.copytree(BUNDLE / "themes", THEMES_DIR)     # first run of the exe: install default themes
    themes = []
    for path in sorted(THEMES_DIR.glob("*.json")):
        with open(path, encoding="utf-8-sig") as f:
            t = _merge(THEME_DEFAULTS, json.load(f))
        t["scenes"] = [s for s in t["scenes"] if s in SCENES] or list(SCENES)
        t["id"] = path.stem
        themes.append(t)
    if not themes:
        t = copy.deepcopy(THEME_DEFAULTS)
        t["name"], t["id"] = "Default", "default"
        themes.append(t)
    return themes


def save_theme(theme):
    data = {k: theme[k] for k in THEME_DEFAULTS if k in theme}
    path = THEMES_DIR / f"{theme['id']}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path
