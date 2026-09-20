@echo off
setlocal
if defined LUPLE_PYTHON goto explicit
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" goto bundled
py -3 --version >nul 2>nul
if not errorlevel 1 goto launcher
python "%~dp0lu.py" %*
exit /b %errorlevel%
:explicit
"%LUPLE_PYTHON%" "%~dp0lu.py" %*
exit /b %errorlevel%
:bundled
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0lu.py" %*
exit /b %errorlevel%
:launcher
py -3 "%~dp0lu.py" %*
exit /b %errorlevel%
