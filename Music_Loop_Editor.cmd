@echo off
setlocal
cd /d "%~dp0"
set "ZATO_AUDIO_PY=%~dp0experiments\audio_lab\.venv\Scripts\pythonw.exe"
if not exist "%ZATO_AUDIO_PY%" (
    echo Please set up experiments\audio_lab\.venv using requirements-editor.txt.
    pause
    exit /b 1
)
start "" "%ZATO_AUDIO_PY%" "%~dp0experiments\audio_lab\loop_editor.py"
