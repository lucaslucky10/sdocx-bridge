@echo off
rem Inkbridge web app for Windows. No Python install needed: uv downloads Python and
rem the packages into this folder's .venv the first time (a few minutes), then starts fast.
setlocal
cd /d "%~dp0"
call :find_uv || goto failed
echo Starting Inkbridge. Your browser opens at http://127.0.0.1:8765
echo Close this window to stop it.
"%UV%" run --extra audio --quiet sdocx-bridge web
if errorlevel 1 goto failed
exit /b 0

:find_uv
where uv >nul 2>nul && (set "UV=uv" & exit /b 0)
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if exist "%UV%" exit /b 0
echo First run: installing the uv runner from astral.sh ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if exist "%UV%" exit /b 0
exit /b 1

:failed
echo.
echo Inkbridge could not start. Check the internet connection for the first run and try again.
pause
exit /b 1
