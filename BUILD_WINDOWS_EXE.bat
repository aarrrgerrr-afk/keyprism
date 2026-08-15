@echo off
title Build KeyPrism EXE
python -m pip install pyinstaller mido pynput --quiet
echo Building KeyPrism EXE... this takes 1-2 minutes
pyinstaller --onefile --noconsole --name KeyPrism --icon=app.ico app.py ^
    --hidden-import mido ^
    --hidden-import pynput ^
    --collect-all customtkinter ^
    --add-data "logo_header.png;." ^
    --add-data "app.ico;." ^
    --exclude-module matplotlib ^
    --exclude-module numpy ^
    --exclude-module PIL ^
    --exclude-module PIL.Image ^
    --exclude-module PIL.ImageDraw ^
    --exclude-module PIL.ImageTk ^
    --exclude-module scipy ^
    --exclude-module pandas ^
    --exclude-module setuptools ^
    --exclude-module pkg_resources ^
    --exclude-module IPython ^
    --exclude-module notebook ^
    --exclude-module jupyter ^
    --exclude-module pytest ^
    --exclude-module unittest ^
    --exclude-module xmlrpc ^
    --exclude-module pydoc ^
    --exclude-module doctest ^
    --exclude-module tkinter.test ^
    --exclude-module test ^
    --exclude-module distutils ^
    --exclude-module lib2to3
echo.
echo DONE! Your EXE is in dist\KeyPrism.exe
explorer dist
pause
