@echo off
setlocal
cd /d C:\TY_DEV2
python 05_orchestrator\ty_run.py --mode test_eye_hand_20
set EXITCODE=%ERRORLEVEL%
echo.
echo Exit code: %EXITCODE%
pause
exit /b %EXITCODE%
