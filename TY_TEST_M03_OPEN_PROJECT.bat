@echo off
setlocal
REM === Change project name here if needed ===
set PROJECT_NAME=Talison 1275
cd /d C:\TY_DEV2
python 04_modules\m03_open_project_by_name.py --project "%PROJECT_NAME%"
set EXITCODE=%ERRORLEVEL%
echo.
echo Exit code: %EXITCODE%
pause
exit /b %EXITCODE%
