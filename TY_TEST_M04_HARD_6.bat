@echo off
setlocal
set PROJECT_NAME=Talison 1275
cd /d C:\TY_DEV2
python 05_orchestrator\m04_hard_test_matrix.py --project "%PROJECT_NAME%"
set EXITCODE=%ERRORLEVEL%
echo.
echo Exit code: %EXITCODE%
pause
exit /b %EXITCODE%
