@echo off
title Chatbox Studio - Install / Update
if not exist "%~dp0install.ps1" (
    echo.
    echo  Please extract the ZIP first:
    echo  right-click the ZIP file, choose "Extract All", then open the extracted
    echo  folder and double-click Install.bat there.
    echo.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
    echo.
    echo  Something went wrong - see the messages above.
    echo  You can run Install.bat again; it continues where it stopped.
    echo.
    pause
    exit /b 1
)
echo.
echo  All done. This window closes in 15 seconds.
timeout /t 15 >nul
