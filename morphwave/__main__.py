"""Start Morphwave:  python -m morphwave [options]"""

import argparse
import sys

from . import config
from .audio import AudioAnalyzer
from .nowplaying import NowPlaying
from .renderer import Renderer
from .settings_ui import SettingsWindow
from .winutil import DesktopMouseHook, GlobalHotkeys, make_dpi_aware


def main():
    try:   # own taskbar identity, so Windows shows Morphwave's icon instead of Python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Morphwave.Visualiser")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="morphwave", description="Real-time fractal music visualiser for Windows")
    ap.add_argument("--mode", choices=["fullscreen", "wallpaper", "windowed"], help="overrides settings.json")
    ap.add_argument("--monitor", type=int, help="0 = primary monitor")
    ap.add_argument("--theme", help="theme file name without extension")
    ap.add_argument("--scene", choices=config.SCENES, help="stay on one fractal (for testing)")
    ap.add_argument("--preset", type=int, help="stay on one Infinite journey fractal, 0-29 (for testing)")
    ap.add_argument("--assume-music", action="store_true", help="behave as if music is playing (for testing)")
    ap.add_argument("--hud", action="store_true", help="show the info overlay")
    ap.add_argument("--settings", action="store_true", help="only open the settings window")
    ap.add_argument("--list-themes", action="store_true")
    ap.add_argument("--seconds", type=float, help="quit after this many seconds (for testing)")
    ap.add_argument("--snapshot", help="save the last frame to this PNG on exit (for testing)")
    args = ap.parse_args()

    settings = config.load_settings()
    if args.theme:
        settings["start_theme"] = args.theme
    themes = config.load_themes()
    if args.list_themes:
        for t in themes:
            print(f"{t['id']:<24} {t['name']}")
        return
    if args.settings:
        make_dpi_aware()
        SettingsWindow(settings, themes).run()
        return
    args.scene = config.SCENES.index(args.scene) if args.scene else None
    if args.preset is not None:
        args.scene = config.SCENES.index("journey")

    analyzer = AudioAnalyzer()
    analyzer.set_device(settings["audio_device"])
    nowplaying = NowPlaying()
    renderer = Renderer(settings, themes, analyzer, nowplaying, args)
    renderer.settings_window = SettingsWindow(settings, themes, analyzer, renderer)
    print("Morphwave running. Ctrl+Alt+S opens settings, Ctrl+Alt+Q quits.")

    analyzer.start()
    nowplaying.start()
    GlobalHotkeys(renderer.hotkey_bindings()).start()
    DesktopMouseHook(renderer.desktop_mouse, renderer.hook_wanted).start()   # lets the dock be clicked while we are the wallpaper
    try:
        renderer.run()
    finally:
        analyzer.stop()
        nowplaying.stop()


if __name__ == "__main__":
    sys.exit(main())
