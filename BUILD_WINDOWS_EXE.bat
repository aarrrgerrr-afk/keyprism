@echo off
title Build KeyPrism EXE
python -m pip install pyinstaller mido pynput --quiet
echo Building KeyPrism EXE... this takes 1-2 minutes
pyinstaller --onefile --noconsole --name KeyPrism --icon=app.ico app.py --hidden-import mido --hidden-import pynput --collect-all customtkinter --add-data "logo_header.png;." --add-data "app.ico;."
echo.
echo DONE! Your EXE is in dist\KeyPrism.exe
explorer dist
pause
