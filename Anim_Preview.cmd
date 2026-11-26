@echo off
cd /d "%~dp0"
start "" pythonw.exe "%~dp0experiments\asset_lab\anim_preview.py" --scale 2
