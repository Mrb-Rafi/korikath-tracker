@echo off
rem Opens the dashboard (same as the Reoa shortcut). Start crawls with its buttons.
cd /d "%~dp0"
start "" pythonw server.py
