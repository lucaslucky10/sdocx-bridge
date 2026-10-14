@echo off
rem Registers Inkbridge in Claude Desktop (claude_desktop_config.json, a backup is kept).
rem Use this only if installing sdocx-bridge-*.mcpb by double-click does not work.
setlocal
cd /d "%~dp0"
call :find_uv || goto failed
"%UV%" run --extra audio --quiet sdocx-bridge install-claude
if errorlevel 1 goto failed
echo.
echo Done. Quit Claude completely (also from the system tray) and open it again.
pause
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
echo Could not register Inkbridge in Claude. Check the internet connection and try again.
pause
exit /b 1
