@echo off
setlocal

where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    python "%~dp0main.py" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3 "%~dp0main.py" %*
    exit /b %ERRORLEVEL%
)

if exist "C:\Program Files\Inkscape\bin\python.exe" (
    "C:\Program Files\Inkscape\bin\python.exe" "%~dp0main.py" %*
    exit /b %ERRORLEVEL%
)

echo Python 3 was not found. Please install Python 3.
exit /b 1

