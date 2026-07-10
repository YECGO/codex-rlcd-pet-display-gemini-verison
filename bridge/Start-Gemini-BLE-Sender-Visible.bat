@echo off
cd /d "%~dp0"
echo Starting visible Gemini BLE sender...
echo.
echo This window shows scanning, connection, and update logs.
echo Close this window to stop this visible sender.
echo.
C:\Python313\python.exe -u gemini_ble_sender.py
pause
