@echo off
setlocal
title Phigros Auto - Console
cd /d "%~dp0"
if errorlevel 1 (
    echo [x] Cannot open the script folder.
    pause
    exit /b 1
)

rem Pick the first interpreter that can "import PIL" (Pillow is required).
set "LOCALPY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "PYEXE="
set "PYARG="

py -3 -c "import PIL" >nul 2>nul && (
    set "PYEXE=py"
    set "PYARG=-3"
)

if not defined PYEXE (
    python -c "import PIL" >nul 2>nul && set "PYEXE=python"
)

if not defined PYEXE (
    if exist "%LOCALPY%" (
        "%LOCALPY%" -c "import PIL" >nul 2>nul && set "PYEXE=%LOCALPY%"
    )
)

if not defined PYEXE (
    echo [x] No Python with Pillow was found.
    echo.
    echo     Option 1: install Pillow for your Python:
    echo         py -3 -m pip install pillow
    echo     Option 2: install Python 3.10+ with "Add python.exe to PATH" ticked,
    echo               then run: python -m pip install pillow
    echo.
    pause
    exit /b 1
)

echo Starting Phigros Auto console...
echo Interpreter: %PYEXE% %PYARG%
"%PYEXE%" %PYARG% "app\main.py"
if errorlevel 1 (
    echo.
    echo [x] The app exited with an error. Send the message above to the developer.
    pause
)
