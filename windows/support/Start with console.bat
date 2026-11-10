@echo off
title Chatbox Studio (console)
rem Starts the app with a console window that shows all messages - useful when something fails.
"%~dp0runtime\python.exe" "%~dp0app\app.py"
echo.
pause
