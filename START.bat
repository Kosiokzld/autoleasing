@echo off
chcp 65001 >nul 2>&1
title AutoLeasing v3.0

REM Proverka dali Python e instaliran
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  ================================================
    echo   VNIMANIE: Python ne e instaliran!
    echo  ================================================
    echo.
    echo   Za da raboti programata, tryabva da instalirate
    echo   Python ot https://www.python.org/downloads/
    echo.
    echo   VAJNO: Pri instalaciya zadaljitelno slojete
    echo   otmetka na "Add Python to PATH"!
    echo.
    echo   Sled instalaciya na Python, startirayte
    echo   tozi fail otnovo.
    echo  ================================================
    echo.
    pause
    exit /b
)

python START.py
pause
