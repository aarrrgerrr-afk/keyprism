@echo off
title Build NanoPlayer EXE
python -m pip install pyinstaller mido pynput --quiet
echo Building EXE... this takes 1-2 minutes
pyinstaller --onefile --noconsole --name NanoPlayer --icon=NONE NanoPlayer.py --hidden-import mido --hidden-import pynput
echo.
echo DONE! Your EXE is in dist\NanoPlayer.exe
echo This EXE works DIRECT without Python!
explorer dist
pause
