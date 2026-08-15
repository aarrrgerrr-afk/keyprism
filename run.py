#!/usr/bin/env python3
"""Quick launcher with dependency check"""
import sys
import subprocess

def ensure(pkg, import_name=None):
    try:
        __import__(import_name or pkg)
    except ImportError:
        print(f"Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

ensure("mido")
ensure("pynput")
ensure("pyautogui")
# customtkinter optional
try:
    import customtkinter
except:
    print("customtkinter not found, will use fallback tkinter (install with: pip install customtkinter)")

import app
app.main()
