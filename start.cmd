@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)
if not exist ".venv\inkbridge-installed" (
  .venv\Scripts\python.exe -m pip install -e .
  if errorlevel 1 goto failed
  type nul > .venv\inkbridge-installed
)
echo Open http://127.0.0.1:8765 in your browser.
.venv\Scripts\python.exe -m sdocx_bridge --library "%~dp0library" web
exit /b %errorlevel%
:failed
echo Installation failed. Install Python 3.11 or newer, then try again.
pause
exit /b 1
