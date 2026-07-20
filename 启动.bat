@echo off
cd /d "%~dp0"
start "" /min .venv\Scripts\pythonw.exe app.py
