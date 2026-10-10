@echo off
rem Rebuilds dist\Morphwave\Morphwave.exe from the source.
cd /d "%~dp0"
".venv\Scripts\pyinstaller" --noconfirm --clean --windowed --name Morphwave --icon assets\morphwave.ico --version-file version_info.txt ^
  --add-data "assets;assets" --add-data "themes;themes" ^
  --add-binary ".venv\Lib\site-packages\glfw\glfw3.dll;glfw" --add-binary ".venv\Lib\site-packages\glfw\msvcr120.dll;glfw" ^
  --collect-submodules winrt --collect-all glcontext --hidden-import soundcard ^
  --distpath dist --workpath build run_morphwave.py
pause
