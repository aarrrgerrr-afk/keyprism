@echo off
title NanoPlayer PRO - Direct Run
echo Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    py --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo Python not found! Install from https://python.org
        echo Then double-click this again.
        pause
        exit /b
    ) else (
        set PYTHON=py
    )
) else (
    set PYTHON=python
)
echo Installing deps if missing...
%PYTHON% -m pip install mido pynput pyautogui --quiet
echo Starting NanoPlayer PRO...
%PYTHON% NanoPlayer.py
pause
