@echo off
rem Double-click to install HSC. Runs install.ps1 without changing your PowerShell policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
