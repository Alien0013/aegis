@echo off
rem Compatibility wrapper for tooling that expects the installer under scripts\.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\install.ps1" %*
exit /b %ERRORLEVEL%
