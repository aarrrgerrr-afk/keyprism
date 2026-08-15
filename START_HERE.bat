@echo off
title NanoPlayer - Roblox Piano
python --version >nul 2>&1 || (echo Python not found! Install Python 3.10+ from python.org & pause & exit)
pip install -r requirements.txt
python app.py
pause
