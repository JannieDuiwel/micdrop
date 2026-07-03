@echo off
rem Launch with a console window so you can see errors/tracebacks.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m micdrop
pause
