"""Build dist/Morphwave/Morphwave.exe (and a zip of it for GitHub releases).

    .venv\\Scripts\\python tools\\build.py          build the exe
    .venv\\Scripts\\python tools\\build.py --zip    also make dist/Morphwave-<version>-win64.zip

Your settings.json and themes inside dist/Morphwave are kept across rebuilds.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "Morphwave"
GLFW = ROOT / ".venv" / "Lib" / "site-packages" / "glfw"


def running():
    out = subprocess.run(["tasklist", "/fi", "imagename eq Morphwave.exe"], capture_output=True, text=True).stdout
    return "Morphwave.exe" in out


def main():
    if running():
        sys.exit("Morphwave is running. Close it first (Ctrl+Alt+Q), then build again.")
    keep = Path(tempfile.mkdtemp())
    for name in ("settings.json", "themes"):
        src = DIST / name
        if src.is_dir():
            shutil.copytree(src, keep / name)
        elif src.exists():
            shutil.copy2(src, keep / name)

    sep = ";"
    # PyInstaller through this Python (the venv's pyinstaller.exe launcher breaks when Python updates itself)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
           "--name", "Morphwave", "--icon", "assets/morphwave.ico", "--version-file", "version_info.txt",
           "--add-data", f"assets{sep}assets", "--add-data", f"themes{sep}themes",
           "--add-binary", f"{GLFW / 'glfw3.dll'}{sep}glfw", "--add-binary", f"{GLFW / 'msvcr120.dll'}{sep}glfw",
           "--collect-submodules", "winrt", "--collect-all", "glcontext", "--hidden-import", "soundcard",
           "--distpath", "dist", "--workpath", "build", "--specpath", "build", "run_morphwave.py"]
    # --specpath build makes relative paths resolve from build/, so pass absolute ones
    cmd = [c.replace("assets/morphwave.ico", str(ROOT / "assets" / "morphwave.ico"))
             .replace("version_info.txt", str(ROOT / "version_info.txt"))
             .replace(f"assets{sep}assets", f"{ROOT / 'assets'}{sep}assets")
             .replace(f"themes{sep}themes", f"{ROOT / 'themes'}{sep}themes") for c in cmd]
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode:
        sys.exit("Build failed.")

    for name in ("settings.json", "themes"):
        src = keep / name
        if src.is_dir():
            shutil.copytree(src, DIST / name, dirs_exist_ok=True)
        elif src.exists():
            shutil.copy2(src, DIST / name)
    shutil.rmtree(keep, ignore_errors=True)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    print("Built", DIST / "Morphwave.exe")

    if "--zip" in sys.argv:
        sys.path.insert(0, str(ROOT))
        from morphwave import __version__
        stage = Path(tempfile.mkdtemp()) / "Morphwave"
        shutil.copytree(DIST, stage, ignore=shutil.ignore_patterns("settings.json"))   # ship defaults only
        shutil.rmtree(stage / "themes", ignore_errors=True)                            # fresh themes come from the bundle
        z = shutil.make_archive(str(ROOT / "dist" / f"Morphwave-{__version__}-win64"), "zip", stage.parent, "Morphwave")
        shutil.rmtree(stage.parent, ignore_errors=True)
        print("Zipped", z)


if __name__ == "__main__":
    main()
