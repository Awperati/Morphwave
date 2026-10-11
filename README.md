# Morphwave

![Morphwave](assets/morphwave.png)

A real-time fractal music visualiser for Windows 11. It listens to whatever your
PC is playing and drives GPU-rendered fractals with it: endless zooms and 3D
fly-through tunnels whose speed, colour and shape follow the music.
Run it fullscreen or as a live desktop wallpaper behind your icons.

Everything is computed per pixel on the GPU, every frame, at full resolution.
There are no images or videos, so detail stays sharp.

## Install

1. Download `Morphwave-1.0.0-win64.zip` from the [Releases](../../releases) page.
2. Unzip it anywhere and run `Morphwave.exe`. No Python needed.

Keep `Morphwave.exe` and the `_internal` folder together. Settings and themes are created
next to the exe on first run, so you can edit them.

**Command-line options:** `--mode fullscreen|wallpaper|windowed`, `--monitor N`,
`--theme <file name>`, `--settings` (settings window only), `--hud`.

## Shortcuts

These work anywhere, even while it is the wallpaper.

| Keys | Action |
|---|---|
| Ctrl+Alt+S | Settings |
| Ctrl+Alt+→ / ← | Next / previous theme |
| Ctrl+Alt+Space | Next scene |
| Ctrl+Alt+H | Info overlay (fps, BPM, vibe, speed) |
| Ctrl+Alt+W | Switch fullscreen ↔ wallpaper |
| Ctrl+Alt+Q | Quit |

In the visualiser window the same keys work without Ctrl+Alt, and Esc also quits.
A slim button dock pops up along the bottom whenever you press a key or move the
mouse, then dissolves after a few seconds of leaving it alone. Its buttons are
clickable (right-click Theme to go back a theme). As wallpaper, move the mouse
over the bottom of the bare desktop to bring the dock up and click it there.

## Scenes

| Scene | Motion |
|---|---|
| Julia set (2D) | Falls forever into the set's own spiral |
| Mandelbrot dive (2D) | Dives about 5000x into famous spots, one after another |
| Apollonian lace (2D) | Falls into a junction of circles |
| Menger tunnel (3D) | Flies through an endless, twisting stone sponge |
| Sphere-lace tunnel (3D) | Flies through bubbles packed inside bubbles |
| Kleinian caverns (3D) | Flies through organic limit-set caverns |
| All-Seeing Spiral (3D) | An engraved ink tunnel of curling waves and watching eyes, flowing out of a giant eye |

## Default: the Infinite Journey

The default theme flies you through 36 different 3D fractals without ever fading.
Every 48 beats the next fractal is planted a little way down the tunnel, and the
geometry itself morphs from one into the other as you fly into it.

| Theme | What it is |
|---|---|
| Infinite Journey | 36 fractals, seamless morphs (default) |
| Menger Odyssey | Only the 12 Menger-sponge fractals |
| Net of Being | An endless hall of arches whose walls are made of watching eyes |
| Cosmic Ocean | Waves below, rippling sky-rings above, floating glowing shards |
| Unimaginable Horrors | The eye forest, a flesh-coloured Net of Being and organic fractals |
| All-Seeing Spiral | Blue-and-red engraving: rings of eyes and curling scrolls |

## Performance

On first launch Morphwave tests your graphics card for a moment and picks a frame-rate
cap, level of detail, render scale and anti-aliasing (Settings → Display → **Detect best
settings** runs it again). While it runs, the GPU's time per frame is measured and detail,
then resolution, are traded to stay under the **GPU load limit** (default 50%: quieter fans).

- **Frame rate cap:** 60–120 fps is plenty; matching a 240 Hz screen costs 3–4x the power.
- **Level of detail:** Sharpest … Fastest. How far away fractals start to simplify.
- **Frame rate while silent:** drops to 30 fps when nothing plays.
- **V-sync** stops tearing; **Pause while covered** stops rendering when games, videos or
  maximised windows hide it.

## How the music drives it

- **Speed:** BPM, how busy the beats are, the long-term energy, and the bass.
- **Colour comes from the music:** quiet passages are muted, loud ones vivid; in silence
  everything slows almost to a stop and fades to near greyscale.
- **Waves through the surfaces:** each slice of tunnel depth is tied to a frequency band,
  so ripples run through the fractal walls themselves, and the waveform wraps around them.
- **Beats:** a soft shockwave or light wave (no flashing); every N beats the scene changes.
- **Spectrum:** each colour band of the fractal is tied to a frequency band.
- **The scene itself bends:** in the 2D scenes the waveform ripples the whole plane,
  mids swirl the centre, bass swells it and each beat sends a shockwave outwards.
  In the tunnels, bass swells the walls, the waveform ripples them, mids corkscrew
  the tunnel, spectrum bands light up along the walls and each beat fires a light
  pulse down the tunnel. Strength: Settings → Visuals → "Music bends the scene".
- **Waveform ring:** optional and off by default (Settings → Display).
- **Vibe:** long-term energy and brightness blend the theme from its calm palette to its intense one.
  Energetic music also favours the 3D tunnels. You can fix the vibe in Settings.

## Themes

A theme is a `.json` file in `themes/`: which scenes it uses, how often it changes
scene, two colour palettes and effect strengths. Edit them live in
Settings → Visuals, then press **Save theme**.

## Code

| File | Role |
|---|---|
| [audio.py](morphwave/audio.py) | Loopback capture, FFT, beats, BPM, waveform, long-term vibe |
| [shaders.py](morphwave/shaders.py) | All fractals and effects (GLSL) |
| [renderer.py](morphwave/renderer.py) | Window, frame loop, scene changes, speed, auto quality |
| [settings_ui.py](morphwave/settings_ui.py) | Settings window |
| [winutil.py](morphwave/winutil.py) | Wallpaper embedding, global hotkeys, covered-screen check |
| [autotune.py](morphwave/autotune.py) | Hardware check and GPU test that picks the settings |

## Run from source

```
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m morphwave
```

## Build Morphwave.exe

```
.venv\Scripts\python tools\build.py          # builds dist\Morphwave\Morphwave.exe
.venv\Scripts\python tools\build.py --zip    # also makes the release zip
```

Your `settings.json` and themes in `dist\Morphwave` survive rebuilds. The fractal icon is
generated by `tools\make_icon.py`.

Windows 10/11 only (WASAPI loopback, desktop wallpaper embedding, global hotkeys).
