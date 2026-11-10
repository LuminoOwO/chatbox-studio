@echo off
title Chatbox Studio - Uninstall
copy /y "%~dp0uninstall.ps1" "%TEMP%\chatbox-studio-uninstall.ps1" >nul & powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\chatbox-studio-uninstall.ps1" & pause & exit /b
